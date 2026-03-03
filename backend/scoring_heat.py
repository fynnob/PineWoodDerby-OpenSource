"""
Pinewood Derby — Heat/Round Scoring Logic (Module 5)
Provides helpers for generating heats, advancing rounds, and computing standings.
"""
import itertools, math
import db


class HeatScoring:
    def __init__(self, config: dict, broadcast_fn):
        self.config    = config
        self.broadcast = broadcast_fn
        self.num_lanes = config.get("num_lanes", 4)

    # ── Generate heats for a round ───────────────────────────────
    def generate_heats(self, round_id: str, car_ids: list) -> list:
        """
        Assign cars to lanes across heats so each car races once per round
        and ideally every car races in every lane (balanced rotation).
        Returns list of heat dicts with embedded entries.
        """
        n      = len(car_ids)
        lanes  = self.num_lanes
        # Number of heats: each car races once, grouped in sets of num_lanes
        groups = [car_ids[i:i+lanes] for i in range(0, n, lanes)]

        heats   = []
        entries = []
        for i, group in enumerate(groups):
            heat = db.insert_heat({"round_id": round_id, "heat_number": i + 1})
            heats.append(heat)
            for lane, car_id in enumerate(group, start=1):
                entries.append({"heat_id": heat["id"], "lane_number": lane, "car_id": car_id})

        db.insert_heat_entries(entries)
        return heats

    # ── Standings ────────────────────────────────────────────────
    def compute_standings(self, round_id: str) -> list:
        """
        Returns list of {car_id, points, races, rank} sorted by points ascending
        (lower is better: 1st = 1pt, 2nd = 2pts, etc.)
        """
        results = db.list_heat_results(round_id)
        place_fields = [
            ("first_place_car",  1),
            ("second_place_car", 2),
            ("third_place_car",  3),
            ("fourth_place_car", 4),
        ]
        pts   = {}
        races = {}
        for r in results:
            for field, points in place_fields:
                car_id = r.get(field)
                if car_id:
                    pts[car_id]   = pts.get(car_id, 0)   + points
                    races[car_id] = races.get(car_id, 0) + 1

        standings = sorted(
            [{"car_id": c, "points": p, "races": races.get(c, 0)}
             for c, p in pts.items()],
            key=lambda x: x["points"]
        )
        for i, s in enumerate(standings):
            s["rank"] = i + 1
        return standings

    # ── Advance to next round ────────────────────────────────────
    def cars_advancing(self, round_id: str, advance_count: int) -> list:
        """Returns car_ids of the top N finishers from the round."""
        standings = self.compute_standings(round_id)
        return [s["car_id"] for s in standings[:advance_count]]
