"""
Pinewood Derby — Remote Sensor Bridge Scoring (Module 6)
Receives HTTP POST hits from an external device (ESP32 or similar).
POST /api/sensor/hit  { "lane": 1, "time_ms": 4521.3 }
"""
import asyncio, time
import db


class SensorScoring:
    def __init__(self, config: dict, broadcast_fn):
        self.config     = config
        self.broadcast  = broadcast_fn
        self.num_lanes  = config.get("num_lanes", 4)
        # hits[heat_id] = {lane: time_ms}
        self._hits: dict[str, dict[int, float]] = {}
        self._lock = asyncio.Lock()

    async def record_hit(self, lane: int, time_ms: float) -> dict | None:
        """
        Called when a lane sensor fires. Returns a heat_result dict once
        all lanes in the current heat have reported (or timeout).
        """
        state  = db.get_race_state()
        heat_id = state.get("current_heat_id")
        if not heat_id:
            return None

        async with self._lock:
            if heat_id not in self._hits:
                self._hits[heat_id] = {}
            self._hits[heat_id][lane] = time_ms

            # Fetch entries to know how many cars are in this heat
            entries = db.list_heat_entries([heat_id])
            expected = len(entries)
            if len(self._hits[heat_id]) < expected:
                return None  # still waiting for more lanes

            # All lanes reported — compute finish order
            hits     = self._hits.pop(heat_id)
            order    = sorted(hits.items(), key=lambda x: x[1])  # fastest first
            entry_by_lane = {e["lane_number"]: e["car_id"] for e in entries}

            place_fields = ["first_place_car","second_place_car",
                            "third_place_car","fourth_place_car"]
            result_data = {"heat_id": heat_id}
            for i, (lane_no, t) in enumerate(order):
                if i < 4:
                    result_data[place_fields[i]] = entry_by_lane.get(lane_no, "")
                result_data[f"time_ms_lane{lane_no}"] = t

            # Pad missing places if < 4 cars
            for f in place_fields:
                result_data.setdefault(f, "")

            return db.upsert_heat_result(result_data)

    def reset_heat(self, heat_id: str):
        """Clear any partial hits for a heat (used on re-race)."""
        self._hits.pop(heat_id, None)
