#!/usr/bin/env bash
# Comprehensive API tests for PineWoodDerby local backend
# Usage: bash test_api.sh
set -euo pipefail

B="http://localhost:8080/api"
PASS=0; FAIL=0

check() {
  local label="$1" expected="$2" actual="$3"
  local pretty
  pretty=$(echo "$actual" | python3 -c "import sys,json; d=sys.stdin.read(); print(json.dumps(json.loads(d), indent=2))" 2>/dev/null || echo "$actual")
  if echo "$pretty" | grep -q "$expected"; then
    printf "✅  %-50s\n" "$label"
    PASS=$((PASS+1))
  else
    printf "❌  %-50s\n" "$label"
    echo "    expected : $expected"
    echo "    actual   : $(echo "$actual" | head -c 300)"
    FAIL=$((FAIL+1))
  fi
}

echo "Waiting for server on $B ..."
for i in $(seq 1 15); do
  curl -sf $B/race_state > /dev/null 2>&1 && break
  sleep 1
done
echo "Server ready."; echo ""

echo "=== CARS ==="
check "GET /cars — returns JSON array" '\[' "$(curl -s $B/cars)"

CAR1=$(curl -s -X POST $B/cars -H "Content-Type: application/json" \
  -d '{"kid_name":"Alice"}')
CAR1_ID=$(echo "$CAR1" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
check "POST /cars — kid_name" '"Alice"' "$CAR1"

PATCHED=$(curl -s -X PATCH $B/cars/$CAR1_ID -H "Content-Type: application/json" \
  -d '{"kid_name":"Alicia"}')
check "PATCH /cars/{id} — rename" '"Alicia"' "$PATCHED"

CAR2=$(curl -s -X POST $B/cars -H "Content-Type: application/json" \
  -d '{"kid_name":"Bob"}')
CAR2_ID=$(echo "$CAR2" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
check "POST /cars — car 2" '"Bob"' "$CAR2"

check "GET /cars — list both" '"Alicia"' "$(curl -s $B/cars)"
check "GET /cars — filter by id" '"Alicia"' "$(curl -s "$B/cars?id=$CAR1_ID")"

DEL_SC=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE $B/cars/$CAR2_ID)
check "DELETE /cars/{id} — 200" '200' "$DEL_SC"
# re-create car 2
CAR2=$(curl -s -X POST $B/cars -H "Content-Type: application/json" -d '{"kid_name":"Bob"}')
CAR2_ID=$(echo "$CAR2" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

echo ""; echo "=== ROUNDS ==="
check "GET /rounds — returns JSON array" '\[' "$(curl -s $B/rounds)"

RND=$(curl -s -X POST $B/rounds -H "Content-Type: application/json" \
  -d '{"name":"Round 1"}')
RND_ID=$(echo "$RND" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
check "POST /rounds — name stored" '"Round 1"' "$RND"
check "POST /rounds — round_number assigned" '"round_number"' "$RND"

PRND=$(curl -s -X PATCH $B/rounds/$RND_ID -H "Content-Type: application/json" \
  -d '{"name":"Qualifying"}')
check "PATCH /rounds/{id} — rename" '"Qualifying"' "$PRND"
check "GET /rounds — updated name" '"Qualifying"' "$(curl -s $B/rounds)"

echo ""; echo "=== HEATS ==="
check "GET /heats — returns JSON array" '\[' "$(curl -s $B/heats)"

H1=$(curl -s -X POST $B/heats -H "Content-Type: application/json" \
  -d "{\"round_id\":\"$RND_ID\",\"heat_number\":1,\"status\":\"pending\"}")
H1_ID=$(echo "$H1" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
check "POST /heats — single insert" '"heat_number": 1' "$H1"

BULK=$(curl -s -X POST $B/heats -H "Content-Type: application/json" \
  -d "[{\"round_id\":\"$RND_ID\",\"heat_number\":2},{\"round_id\":\"$RND_ID\",\"heat_number\":3}]")
check "POST /heats — bulk (heat 2)" '"heat_number": 2' "$BULK"
check "POST /heats — bulk (heat 3)" '"heat_number": 3' "$BULK"

check "PATCH /heats/{id} — active" '"active"' \
  "$(curl -s -X PATCH $B/heats/$H1_ID -H "Content-Type: application/json" -d '{"status":"active"}')"

check "GET /heats?round_id" '"heat_number": 1' "$(curl -s "$B/heats?round_id=$RND_ID")"
check "GET /heats?include_round=true" '"Qualifying"' "$(curl -s "$B/heats?round_id=$RND_ID&include_round=true")"

echo ""; echo "=== HEAT ENTRIES ==="
BULK_ENT=$(curl -s -X POST $B/heat_entries/bulk -H "Content-Type: application/json" \
  -d "[{\"heat_id\":\"$H1_ID\",\"car_id\":\"$CAR1_ID\",\"lane_number\":1},
       {\"heat_id\":\"$H1_ID\",\"car_id\":\"$CAR2_ID\",\"lane\":2}]")
check "POST /heat_entries/bulk — count 2" '"count": 2' "$BULK_ENT"
check "POST /heat_entries/bulk — entries array" '"entries"' "$BULK_ENT"

ENTS=$(curl -s "$B/heat_entries?heat_id=$H1_ID")
check "GET /heat_entries — lane_number" '"lane_number"' "$ENTS"
check "GET /heat_entries — nested cars" '"cars"' "$ENTS"
check "GET /heat_entries — car name" '"Alicia"' "$ENTS"

echo ""; echo "=== HEAT RESULTS ==="
# New sensor/flexible format
RES=$(curl -s -X POST $B/heat_results -H "Content-Type: application/json" \
  -d "{\"heat_id\":\"$H1_ID\",\"round_id\":\"$RND_ID\",
       \"results\":[{\"lane\":1,\"time_ms\":2500,\"car_id\":\"$CAR1_ID\"},
                   {\"lane\":2,\"time_ms\":2700,\"car_id\":\"$CAR2_ID\"}]}")
check "POST /heat_results (sensor format) — heat_id" '"heat_id"' "$RES"
check "POST /heat_results — results array returned" '"results"' "$RES"
check "POST /heat_results — first_place_car derived" '"first_place_car"' "$RES"

check "GET /heat_results?round_id" '"heat_id"' "$(curl -s "$B/heat_results?round_id=$RND_ID")"
check "GET /heat_results?heat_id" '"heat_id"' "$(curl -s "$B/heat_results?heat_id=$H1_ID")"

# Legacy frontend format
RES_LEG=$(curl -s -X POST $B/heat_results -H "Content-Type: application/json" \
  -d "{\"heat_id\":\"$H1_ID\",\"first_place_car\":\"$CAR1_ID\",
       \"second_place_car\":\"$CAR2_ID\",\"third_place_car\":null,\"fourth_place_car\":null}")
check "POST /heat_results (legacy format)" '"first_place_car"' "$RES_LEG"

DEL_RES=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE $B/heat_results/$H1_ID)
check "DELETE /heat_results/{heat_id} — 200" '200' "$DEL_RES"

# Re-insert for state join test
curl -s -X POST $B/heat_results -H "Content-Type: application/json" \
  -d "{\"heat_id\":\"$H1_ID\",\"round_id\":\"$RND_ID\",
       \"results\":[{\"lane\":1,\"time_ms\":2500,\"car_id\":\"$CAR1_ID\",\"place\":1}]}" > /dev/null

echo ""; echo "=== RACE STATE ==="
check "GET /race_state — id:1" '"id": 1' "$(curl -s $B/race_state)"

check "PATCH /race_state — sets heat" '"current_heat_id"' \
  "$(curl -s -X PATCH $B/race_state -H "Content-Type: application/json" \
     -d "{\"current_round_id\":\"$RND_ID\",\"current_heat_id\":\"$H1_ID\"}")"

STATE=$(curl -s $B/state)
check "GET /state — heats joined" '"heats"' "$STATE"
check "GET /state — rounds joined" '"rounds"' "$STATE"
check "GET /state — round name" '"Qualifying"' "$STATE"

echo ""; echo "=== SENSOR HIT ==="
check "POST /sensor/hit — ok" '"ok"' \
  "$(curl -s -X POST $B/sensor/hit -H "Content-Type: application/json" -d '{"lane":1,"time_ms":2345.6}')"

echo ""; echo "=== STORAGE ==="
check "POST /storage/upload" '"url"' \
  "$(curl -s -X POST "$B/storage/upload?bucket=test&name=hello.txt" -F "file=@/etc/hostname")"
check "GET /storage/{bucket} — listed" '"hello.txt"' "$(curl -s $B/storage/test)"
check "GET /storage/{bucket}/{file}" 'raspberrypi' "$(curl -s $B/storage/test/hello.txt)"

echo ""; echo "=== HEAT SCORING (unit) ==="
cd /home/fynn/Desktop/PineWoodDerby-OpenSource
SCHED=$(python3 - <<'PYEOF'
import sys, json
sys.path.insert(0, 'backend')
import db
from scoring_heat import HeatScoring

# Seed minimal data for the unit test
db.DB_PATH = db.DB_PATH  # reuse existing DB so FK constraints pass
rnd = db.insert_round({"name": "UnitTestRound"})
c1  = db.insert_car({"kid_name": "UnitCar1"})
c2  = db.insert_car({"kid_name": "UnitCar2"})
c3  = db.insert_car({"kid_name": "UnitCar3"})

cfg = {"num_lanes": 2}
hs  = HeatScoring(cfg, None)
heats = hs.generate_heats(rnd["id"], [c1["id"], c2["id"], c3["id"]])
# Return the heat_entries for inspection
entries = db.list_heat_entries([h["id"] for h in heats])
print(json.dumps({"heats": len(heats), "entries": entries}))
PYEOF
)
check "HeatScoring.generate_heats — heats created" '"heats"' "$SCHED"
check "HeatScoring — entries have lane_number" '"lane_number"' "$SCHED"
check "HeatScoring — entries have car_id" '"car_id"' "$SCHED"

echo ""
echo "==========================================="
printf "  ✅ PASSED: %-3s   ❌ FAILED: %-3s\n" "$PASS" "$FAIL"
echo "==========================================="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
