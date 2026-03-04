/**
 * Pinewood Derby — ESP32 Sensor Bridge (Module 6)
 * ─────────────────────────────────────────────────
 * Each lane has an IR break-beam or photo-interrupter sensor.
 * When a car triggers the sensor the ESP32 records the elapsed
 * time (ms since the gate opened) and POSTs it to the Derby
 * server as  { "lane": N, "time_ms": T }.
 *
 * Compatible with both backends:
 *   • Local FastAPI  → POST http://<server_ip>:8000/api/sensor/hit
 *   • Supabase edge  → POST https://<project>.supabase.co/functions/v1/sensor-hit
 *
 * Hardware wiring (adjust pins as needed):
 *   Lane 1 → GPIO 25
 *   Lane 2 → GPIO 26
 *   Lane 3 → GPIO 27
 *   Lane 4 → GPIO 14
 *
 * A "gate" sensor (lane 0 / GPIO 12) can optionally be wired to
 * detect the start gate opening — this zeros the race timer so
 * all lane times are relative to gate-open rather than power-on.
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ── User configuration ────────────────────────────────────────────
const char* WIFI_SSID     = "DerbyNet";           // Hotspot SSID (run.py creates this)
const char* WIFI_PASSWORD = "derbyrace";           // Hotspot password

// ── Backend mode — uncomment exactly ONE ─────────────────────────
// local → POST to FastAPI running on the Raspberry Pi (default)
// cloud → POST directly to Supabase REST API  (sensor_hits table)
#define BACKEND_MODE_LOCAL    //  ← keep this for Pi/local server
//#define BACKEND_MODE_CLOUD  //  ← switch to this for Supabase cloud

// Local backend config (BACKEND_MODE_LOCAL only):
// e.g. "http://192.168.4.1:8000" or "http://derby.local:8000"
const char* SERVER_BASE   = "http://192.168.4.1:8000";
const char* HIT_PATH      = "/api/sensor/hit";

// Supabase cloud config (BACKEND_MODE_CLOUD only):
const char* SUPABASE_URL      = "https://YOURPROJECT.supabase.co";
const char* SUPABASE_ANON_KEY = "YOUR_ANON_KEY_HERE";

// Number of lanes
const int   NUM_LANES     = 4;

// GPIO pins for each lane (index 0 = lane 1, etc.)
const int   LANE_PINS[NUM_LANES] = { 25, 26, 27, 14 };

// Optional gate-open sensor pin (set to -1 to disable)
// When triggered, the race timer is zeroed.
const int   GATE_PIN      = 12;

// Sensor type: LOW = triggered (IR beam broken), HIGH = triggered (reflective sensor)
const int   TRIGGER_LEVEL = LOW;

// Debounce window in ms — ignore signals within this window after first hit
const unsigned long DEBOUNCE_MS = 500;

// How long (ms) to keep results before auto-reset for next heat
// Set to 0 to require manual reset via incoming HTTP request
const unsigned long AUTO_RESET_MS = 30000;

// ── State ─────────────────────────────────────────────────────────
volatile unsigned long gateOpenAt      = 0;    // millis() when gate opened
volatile bool          raceActive      = false;
volatile bool          gateEventPending = false; // loop() reads and posts
volatile unsigned long lastHitAt[NUM_LANES]  = {};   // debounce timestamps
volatile bool          laneHit[NUM_LANES]    = {};   // did this lane already report?
unsigned long          resetAt = 0;

// ── ISR helpers ───────────────────────────────────────────────────
// ISRs must be in IRAM for ESP32
void IRAM_ATTR onGate() {
  gateOpenAt       = millis();
  raceActive       = true;
  gateEventPending = true;   // signal loop() to broadcast gate-open event
  for (int i = 0; i < NUM_LANES; i++) {
    lastHitAt[i] = 0;
    laneHit[i]   = false;
  }
}

struct LaneHitEvent {
  int  lane;
  unsigned long time_ms;
  bool pending;
};

// Ring buffer for hit events (ISR writes, loop() reads)
#define EVENT_BUF_SIZE 8
volatile LaneHitEvent eventBuf[EVENT_BUF_SIZE];
volatile int evtWrite = 0;
volatile int evtRead  = 0;

void IRAM_ATTR onLaneSensor(int laneIdx) {
  if (!raceActive) return;
  unsigned long now = millis();
  // Debounce
  if (laneHit[laneIdx]) return;
  if (now - lastHitAt[laneIdx] < DEBOUNCE_MS) return;
  laneHit[laneIdx]   = true;
  lastHitAt[laneIdx] = now;
  unsigned long elapsed = (gateOpenAt > 0) ? (now - gateOpenAt) : 0;

  // Push to ring buffer
  int next = (evtWrite + 1) % EVENT_BUF_SIZE;
  if (next != evtRead) {  // not full
    eventBuf[evtWrite] = { laneIdx + 1, elapsed, true };
    evtWrite = next;
  }
}

// One ISR per lane — generate lambdas at compile time
void IRAM_ATTR onLane1() { onLaneSensor(0); }
void IRAM_ATTR onLane2() { onLaneSensor(1); }
void IRAM_ATTR onLane3() { onLaneSensor(2); }
void IRAM_ATTR onLane4() { onLaneSensor(3); }

void (*LANE_ISRS[4])() = { onLane1, onLane2, onLane3, onLane4 };

// ── WiFi ──────────────────────────────────────────────────────────
void connectWifi() {
  Serial.printf("[WiFi] Connecting to %s ", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long t = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500); Serial.print(".");
    if (millis() - t > 15000) {
      Serial.println("\n[WiFi] Timeout — retrying in 5s");
      delay(5000);
      WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
      t = millis();
    }
  }
  Serial.printf("\n[WiFi] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
}

// ── HTTP POST ─────────────────────────────────────────────────────
void postHit(int lane, unsigned long time_ms) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.printf("[HTTP] Not connected — lane %d time %lums dropped.\n", lane, time_ms);
    return;
  }

  StaticJsonDocument<128> doc;
  String body;

#ifdef BACKEND_MODE_CLOUD
  // ── Supabase REST API: POST /rest/v1/sensor_hits ──────────────
  String url = String(SUPABASE_URL) + "/rest/v1/sensor_hits";
  doc["lane"]    = lane;
  doc["time_ms"] = (float)time_ms;
  serializeJson(doc, body);

  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type",  "application/json");
  http.addHeader("apikey",        SUPABASE_ANON_KEY);
  http.addHeader("Authorization", String("Bearer ") + SUPABASE_ANON_KEY);
  http.addHeader("Prefer",        "return=minimal");

  Serial.printf("[HTTP] POST (cloud) lane=%d time_ms=%lu  →  %s\n", lane, time_ms, url.c_str());
  int code = http.POST(body);
#else
  // ── Local FastAPI: POST /api/sensor/hit ───────────────────────
  String url = String(SERVER_BASE) + HIT_PATH;
  doc["lane"]    = lane;
  doc["time_ms"] = (int)time_ms;
  serializeJson(doc, body);

  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");

  Serial.printf("[HTTP] POST (local) lane=%d time_ms=%lu  →  %s\n", lane, time_ms, url.c_str());
  int code = http.POST(body);
#endif

  if (code > 0) {
    Serial.printf("[HTTP] Response: %d %s\n", code, http.getString().c_str());
  } else {
    Serial.printf("[HTTP] Error: %s\n", HTTPClient::errorToString(code).c_str());
  }
  http.end();
}

// ── Setup ─────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== Pinewood Derby Sensor Bridge ===");

  // Configure gate pin
  if (GATE_PIN >= 0) {
    pinMode(GATE_PIN, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(GATE_PIN), onGate,
                    TRIGGER_LEVEL == LOW ? FALLING : RISING);
    Serial.printf("[GPIO] Gate sensor on pin %d\n", GATE_PIN);
  } else {
    // No gate sensor — activate immediately so any sensor works
    gateOpenAt = millis();
    raceActive = true;
  }

  // Configure lane pins + ISRs
  for (int i = 0; i < NUM_LANES; i++) {
    pinMode(LANE_PINS[i], INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(LANE_PINS[i]), LANE_ISRS[i],
                    TRIGGER_LEVEL == LOW ? FALLING : RISING);
    Serial.printf("[GPIO] Lane %d sensor on pin %d\n", i + 1, LANE_PINS[i]);
  }

  connectWifi();

  Serial.println("[Ready] Waiting for gate to open...");
}

// ── Gate event POST ───────────────────────────────────────────
void postGateEvent(const char* state) {
  if (WiFi.status() != WL_CONNECTED) return;

  StaticJsonDocument<64> doc;
  doc["state"] = state;
  String body;
  serializeJson(doc, body);

  HTTPClient http;
  int code = -1;

#ifdef BACKEND_MODE_CLOUD
  String url = String(SUPABASE_URL) + "/realtime/v1/api/broadcast";
  StaticJsonDocument<256> msg;
  JsonArray messages = msg.createNestedArray("messages");
  JsonObject m = messages.createNestedObject();
  m["topic"] = "realtime:gate";
  m["event"] = "gate_event";
  JsonObject payload = m.createNestedObject("payload");
  payload["state"] = state;
  String cloudBody;
  serializeJson(msg, cloudBody);
  http.begin(url);
  http.addHeader("Content-Type",  "application/json");
  http.addHeader("apikey",        SUPABASE_ANON_KEY);
  http.addHeader("Authorization", String("Bearer ") + SUPABASE_ANON_KEY);
  code = http.POST(cloudBody);
#else
  String url = String(SERVER_BASE) + "/api/gate";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  code = http.POST(body);
#endif

  Serial.printf("[Gate] POST state=%s  →  %s  (%d)\n", state, url.c_str(), code);
  http.end();
}

// ── Loop ─────────────────────────────────────────────────────
void loop() {
  // Reconnect WiFi if dropped
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Reconnecting...");
    connectWifi();
  }

  // Broadcast gate-open event (set by ISR)
  if (gateEventPending) {
    gateEventPending = false;
    postGateEvent("open");
  }

  // Drain event ring buffer
  while (evtRead != evtWrite) {
    LaneHitEvent evt = { eventBuf[evtRead].lane,
                         eventBuf[evtRead].time_ms,
                         eventBuf[evtRead].pending };
    evtRead = (evtRead + 1) % EVENT_BUF_SIZE;

    Serial.printf("[SENSOR] Lane %d hit! time_ms = %lu\n", evt.lane, evt.time_ms);
    postHit(evt.lane, evt.time_ms);
  }

  // Auto-reset after all lanes have hit (or timeout)
  if (AUTO_RESET_MS > 0 && raceActive) {
    bool allHit = true;
    for (int i = 0; i < NUM_LANES; i++) allHit &= laneHit[i];
    if (allHit) {
      if (resetAt == 0) resetAt = millis();
      if (millis() - resetAt > AUTO_RESET_MS) {
        Serial.println("[Auto-reset] Resetting for next heat.");
        raceActive = false;
        gateOpenAt = 0;
        resetAt    = 0;
        for (int i = 0; i < NUM_LANES; i++) {
          laneHit[i]   = false;
          lastHitAt[i] = 0;
        }
        if (GATE_PIN < 0) {
          // No gate sensor — re-arm immediately
          gateOpenAt = millis();
          raceActive = true;
        }
      }
    }
  }

  delay(10);
}
