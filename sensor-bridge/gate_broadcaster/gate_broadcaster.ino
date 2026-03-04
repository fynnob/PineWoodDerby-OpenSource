/**
 * Pinewood Derby — Dedicated Gate Broadcaster (ESP32)
 * ──────────────────────────────────────────────────────
 * A single ESP32 monitors the starting gate switch.
 * When the gate opens it broadcasts an event immediately — with NO
 * database write — so every client sees "RACE STARTED" with minimum latency.
 *
 * Wiring:
 *   Gate switch → GPIO 14 (one leg) + GND (other leg)
 *   Internal pull-up is enabled; pin goes LOW when gate opens.
 *
 * Two backend modes (set exactly one #define below):
 *
 *   LOCAL  — HTTP POST to the FastAPI server  /api/gate
 *            Fast (~1-5 ms on LAN), no internet needed.
 *
 *   CLOUD  — Supabase Realtime Broadcast REST API
 *            No table write; pure pub/sub over HTTPS.
 *            Frontend subscribes to channel "gate".
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ── Backend mode — uncomment exactly ONE ──────────────────────────
#define GATE_BACKEND_LOCAL    //  ← keep for Pi / local server
//#define GATE_BACKEND_CLOUD  //  ← switch for Supabase cloud

// ── WiFi ──────────────────────────────────────────────────────────
const char* WIFI_SSID     = "DerbyNet";     // hotspot SSID from run.py
const char* WIFI_PASSWORD = "derbyrace";    // hotspot password

// ── Local config (GATE_BACKEND_LOCAL) ────────────────────────────
// Pi IP on the hotspot is usually 10.42.0.1 (detected in run.py output)
const char* PI_BASE       = "http://10.42.0.1:80";

// ── Cloud config (GATE_BACKEND_CLOUD) ────────────────────────────
const char* SUPABASE_URL      = "https://YOURPROJECT.supabase.co";
const char* SUPABASE_ANON_KEY = "YOUR_ANON_KEY_HERE";

// ── Gate hardware ─────────────────────────────────────────────────
// Pin that goes LOW when the gate opens (use INPUT_PULLUP)
const int  GATE_PIN         = 14;
// Level the pin is at when the gate IS OPEN
const int  OPEN_LEVEL       = LOW;
// Debounce: ignore transitions within this window (ms)
const unsigned long DEBOUNCE_MS = 80;
// How long after gate closes before we allow a new open event (ms)
// Prevents re-triggering if the gate bounces back
const unsigned long REARM_MS    = 2000;

// ── State ─────────────────────────────────────────────────────────
bool          gateOpen      = false;
unsigned long lastTransition = 0;
bool          pendingOpen   = false;
bool          pendingClose  = false;

// ── WiFi ──────────────────────────────────────────────────────────
void connectWifi() {
  Serial.printf("[WiFi] Connecting to %s ", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long t = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500); Serial.print(".");
    if (millis() - t > 15000) {
      Serial.println("\n[WiFi] Timeout — retrying...");
      WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
      t = millis();
    }
  }
  Serial.printf("\n[WiFi] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
}

// ── Gate event sender ──────────────────────────────────────────────
void sendGateEvent(const char* state) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.printf("[Gate] WiFi not connected — event '%s' dropped\n", state);
    return;
  }

  StaticJsonDocument<128> doc;
  doc["state"] = state;   // "open" or "closed"
  String body;
  serializeJson(doc, body);

  HTTPClient http;
  int code = -1;

#ifdef GATE_BACKEND_CLOUD
  // ── Supabase Realtime Broadcast (no DB write) ──────────────────
  // Channel: "gate"  Event: "gate_event"
  String url = String(SUPABASE_URL) + "/realtime/v1/api/broadcast";

  StaticJsonDocument<256> msg;
  JsonArray messages = msg.createNestedArray("messages");
  JsonObject m = messages.createNestedObject();
  m["topic"]   = "realtime:gate";
  m["event"]   = "gate_event";
  JsonObject payload = m.createNestedObject("payload");
  payload["state"] = state;
  String cloudBody;
  serializeJson(msg, cloudBody);

  http.begin(url);
  http.addHeader("Content-Type",  "application/json");
  http.addHeader("apikey",        SUPABASE_ANON_KEY);
  http.addHeader("Authorization", String("Bearer ") + SUPABASE_ANON_KEY);
  Serial.printf("[Gate] Broadcast (cloud) state=%s  →  %s\n", state, url.c_str());
  code = http.POST(cloudBody);
#else
  // ── Local FastAPI /api/gate (no DB write, instant WS broadcast) ─
  String url = String(PI_BASE) + "/api/gate";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  Serial.printf("[Gate] POST (local) state=%s  →  %s\n", state, url.c_str());
  code = http.POST(body);
#endif

  if (code > 0) {
    Serial.printf("[Gate] Response: %d\n", code);
  } else {
    Serial.printf("[Gate] HTTP error: %s\n", HTTPClient::errorToString(code).c_str());
  }
  http.end();
}

// ── Setup ──────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== Pinewood Derby Gate Broadcaster ===");

  pinMode(GATE_PIN, INPUT_PULLUP);
  // Read initial state
  gateOpen = (digitalRead(GATE_PIN) == OPEN_LEVEL);
  Serial.printf("[Gate] Pin %d — initial state: %s\n",
                GATE_PIN, gateOpen ? "OPEN" : "CLOSED");

  connectWifi();

  Serial.println("[Ready] Watching gate pin...");
}

// ── Loop ───────────────────────────────────────────────────────────
void loop() {
  // Reconnect WiFi if dropped
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Reconnecting...");
    connectWifi();
  }

  bool pinOpen = (digitalRead(GATE_PIN) == OPEN_LEVEL);
  unsigned long now = millis();

  if (pinOpen != gateOpen) {
    // State changed — debounce
    if ((now - lastTransition) > DEBOUNCE_MS) {
      lastTransition = now;
      gateOpen = pinOpen;
      if (gateOpen) {
        Serial.println("[Gate] *** GATE OPENED — race start! ***");
        sendGateEvent("open");
      } else {
        // Only send closed after the rearm window
        if ((now - lastTransition) > REARM_MS || lastTransition == 0) {
          Serial.println("[Gate] Gate closed.");
          sendGateEvent("closed");
        }
      }
    }
  }

  delay(5);  // 5 ms poll — fast enough for a gate switch
}
