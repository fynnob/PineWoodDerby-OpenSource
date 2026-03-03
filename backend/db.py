"""
Pinewood Derby — SQLite Database Layer
"""
import sqlite3, json, uuid, threading
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).parent.parent / "derby.db"
_lock   = threading.Lock()

def _now():
    return datetime.now(timezone.utc).isoformat()

def get_conn():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db(num_lanes: int = 4):
    with _lock, get_conn() as conn:
        conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS cars (
            id           TEXT PRIMARY KEY,
            car_number   INTEGER UNIQUE,
            kid_name     TEXT NOT NULL,
            image_url    TEXT,
            device_token TEXT NOT NULL,
            legal_status TEXT NOT NULL DEFAULT 'pending'
                           CHECK (legal_status IN ('pending','legal','not_legal')),
            eliminated   INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rounds (
            id           TEXT PRIMARY KEY,
            round_number INTEGER UNIQUE NOT NULL,
            status       TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','active','completed')),
            advance_count INTEGER,
            created_at   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS heats (
            id          TEXT PRIMARY KEY,
            round_id    TEXT NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
            heat_number INTEGER NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','active','completed')),
            UNIQUE(round_id, heat_number)
        );

        CREATE TABLE IF NOT EXISTS heat_entries (
            id          TEXT PRIMARY KEY,
            heat_id     TEXT NOT NULL REFERENCES heats(id) ON DELETE CASCADE,
            lane_number INTEGER NOT NULL CHECK (lane_number BETWEEN 1 AND {num_lanes}),
            car_id      TEXT NOT NULL REFERENCES cars(id),
            UNIQUE(heat_id, lane_number),
            UNIQUE(heat_id, car_id)
        );

        CREATE TABLE IF NOT EXISTS heat_results (
            id               TEXT PRIMARY KEY,
            heat_id          TEXT NOT NULL REFERENCES heats(id) ON DELETE CASCADE UNIQUE,
            first_place_car  TEXT NOT NULL REFERENCES cars(id),
            second_place_car TEXT NOT NULL REFERENCES cars(id),
            third_place_car  TEXT NOT NULL REFERENCES cars(id),
            fourth_place_car TEXT NOT NULL REFERENCES cars(id),
            time_ms_lane1    REAL,
            time_ms_lane2    REAL,
            time_ms_lane3    REAL,
            time_ms_lane4    REAL,
            entered_at       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS race_state (
            id               INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            current_round_id TEXT REFERENCES rounds(id),
            current_heat_id  TEXT REFERENCES heats(id),
            email_enabled    INTEGER NOT NULL DEFAULT 0,
            scoring_mode     TEXT NOT NULL DEFAULT 'heat',
            updated_at       TEXT NOT NULL
        );

        INSERT OR IGNORE INTO race_state (id, updated_at)
            VALUES (1, '{_now()}');
        """)

# ── Generic helpers ──────────────────────────────────────────────

def rows_to_dicts(rows):
    return [dict(r) for r in rows]

def next_car_number(conn) -> int:
    row = conn.execute("SELECT COALESCE(MAX(car_number),0)+1 FROM cars").fetchone()
    return row[0]

# ── Cars ─────────────────────────────────────────────────────────

def list_cars(filters: dict = None) -> list:
    where, params = _build_where(filters)
    with get_conn() as conn:
        return rows_to_dicts(conn.execute(
            f"SELECT * FROM cars{where} ORDER BY car_number", params).fetchall())

def get_car(car_id: str) -> dict | None:
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM cars WHERE id=?", (car_id,)).fetchone()
        return dict(r) if r else None

def insert_car(data: dict) -> dict:
    car_id = str(uuid.uuid4())
    with _lock, get_conn() as conn:
        num = next_car_number(conn)
        conn.execute(
            "INSERT INTO cars (id,car_number,kid_name,image_url,device_token,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (car_id, num, data["kid_name"], data.get("image_url"),
             data.get("device_token", str(uuid.uuid4())), _now())
        )
    return get_car(car_id)

def update_car(car_id: str, patch: dict) -> dict:
    allowed = {"kid_name","image_url","legal_status","eliminated","device_token"}
    patch   = {k: v for k, v in patch.items() if k in allowed}
    if not patch: return get_car(car_id)
    sets   = ", ".join(f"{k}=?" for k in patch)
    params = list(patch.values()) + [car_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE cars SET {sets} WHERE id=?", params)
    return get_car(car_id)

def delete_car(car_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM cars WHERE id=?", (car_id,))

# ── Rounds ───────────────────────────────────────────────────────

def list_rounds() -> list:
    with get_conn() as conn:
        return rows_to_dicts(conn.execute(
            "SELECT * FROM rounds ORDER BY round_number").fetchall())

def insert_round(data: dict) -> dict:
    rid = str(uuid.uuid4())
    with get_conn() as conn:
        rnum = (conn.execute("SELECT COALESCE(MAX(round_number),0)+1 FROM rounds").fetchone()[0])
        conn.execute(
            "INSERT INTO rounds (id,round_number,status,advance_count,created_at) VALUES (?,?,?,?,?)",
            (rid, rnum, data.get("status","pending"), data.get("advance_count"), _now())
        )
    with get_conn() as conn:
        return dict(conn.execute("SELECT * FROM rounds WHERE id=?", (rid,)).fetchone())

def update_round(round_id: str, patch: dict) -> dict:
    allowed = {"status","advance_count"}
    patch   = {k: v for k, v in patch.items() if k in allowed}
    if not patch: return {}
    sets   = ", ".join(f"{k}=?" for k in patch)
    params = list(patch.values()) + [round_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE rounds SET {sets} WHERE id=?", params)
        return dict(conn.execute("SELECT * FROM rounds WHERE id=?", (round_id,)).fetchone())

# ── Heats ────────────────────────────────────────────────────────

def list_heats(round_id: str = None, include_round: bool = False) -> list:
    with get_conn() as conn:
        if round_id:
            rows = rows_to_dicts(conn.execute(
                "SELECT * FROM heats WHERE round_id=? ORDER BY heat_number", (round_id,)).fetchall())
        else:
            rows = rows_to_dicts(conn.execute("SELECT * FROM heats ORDER BY heat_number").fetchall())

    if include_round:
        round_cache: dict = {}
        def _get_round(rid):
            if rid not in round_cache:
                with get_conn() as conn:
                    r = conn.execute("SELECT * FROM rounds WHERE id=?", (rid,)).fetchone()
                    round_cache[rid] = dict(r) if r else {}
            return round_cache[rid]
        for h in rows:
            h["rounds"] = _get_round(h.get("round_id"))
    return rows

def insert_heat(data: dict) -> dict:
    hid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO heats (id,round_id,heat_number,status) VALUES (?,?,?,?)",
            (hid, data["round_id"], data["heat_number"], data.get("status","pending"))
        )
        return dict(conn.execute("SELECT * FROM heats WHERE id=?", (hid,)).fetchone())

def update_heat(heat_id: str, patch: dict) -> dict:
    allowed = {"status"}
    patch   = {k: v for k, v in patch.items() if k in allowed}
    if not patch: return {}
    with get_conn() as conn:
        conn.execute(f"UPDATE heats SET status=? WHERE id=?", (patch["status"], heat_id))
        return dict(conn.execute("SELECT * FROM heats WHERE id=?", (heat_id,)).fetchone())

# ── Heat entries ─────────────────────────────────────────────────

def _nest_car(row: dict) -> dict:
    """Move car_number/kid_name into a nested 'cars' dict to match Supabase format."""
    r = dict(row)
    r["cars"] = {"car_number": r.pop("car_number", None), "kid_name": r.pop("kid_name", None)}
    return r


def list_heat_entries(heat_ids: list = None) -> list:
    with get_conn() as conn:
        if heat_ids:
            placeholders = ",".join("?" * len(heat_ids))
            rows = rows_to_dicts(conn.execute(
                f"SELECT he.*, c.car_number, c.kid_name FROM heat_entries he "
                f"JOIN cars c ON c.id=he.car_id "
                f"WHERE he.heat_id IN ({placeholders})", heat_ids).fetchall())
        else:
            rows = rows_to_dicts(conn.execute(
                "SELECT he.*, c.car_number, c.kid_name FROM heat_entries he "
                "JOIN cars c ON c.id=he.car_id").fetchall())
        return [_nest_car(r) for r in rows]

def insert_heat_entries(entries: list) -> list:
    result = []
    with get_conn() as conn:
        for e in entries:
            eid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO heat_entries (id,heat_id,lane_number,car_id) VALUES (?,?,?,?)",
                (eid, e["heat_id"], e["lane_number"], e["car_id"])
            )
            result.append({"id": eid, **e})
    return result

def delete_heat_entries(heat_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM heat_entries WHERE heat_id=?", (heat_id,))

# ── Heat results ─────────────────────────────────────────────────

def get_heat_result(heat_id: str) -> dict | None:
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM heat_results WHERE heat_id=?", (heat_id,)).fetchone()
        return dict(r) if r else None

def list_heat_results(round_ids=None) -> list:
    with get_conn() as conn:
        if round_ids:
            if isinstance(round_ids, str):
                round_ids = [round_ids]
            placeholders = ",".join("?" * len(round_ids))
            return rows_to_dicts(conn.execute(
                f"SELECT hr.* FROM heat_results hr "
                f"JOIN heats h ON h.id=hr.heat_id WHERE h.round_id IN ({placeholders})", round_ids).fetchall())
        return rows_to_dicts(conn.execute("SELECT * FROM heat_results").fetchall())

def upsert_heat_result(data: dict) -> dict:
    rid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute("DELETE FROM heat_results WHERE heat_id=?", (data["heat_id"],))
        conn.execute(
            "INSERT INTO heat_results "
            "(id,heat_id,first_place_car,second_place_car,third_place_car,fourth_place_car,"
            " time_ms_lane1,time_ms_lane2,time_ms_lane3,time_ms_lane4,entered_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rid, data["heat_id"],
             data["first_place_car"], data["second_place_car"],
             data["third_place_car"], data["fourth_place_car"],
             data.get("time_ms_lane1"), data.get("time_ms_lane2"),
             data.get("time_ms_lane3"), data.get("time_ms_lane4"),
             _now())
        )
        return dict(conn.execute("SELECT * FROM heat_results WHERE id=?", (rid,)).fetchone())

def delete_heat_result(heat_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM heat_results WHERE heat_id=?", (heat_id,))

# ── Race state ───────────────────────────────────────────────────

def get_race_state() -> dict:
    with get_conn() as conn:
        return dict(conn.execute("SELECT * FROM race_state WHERE id=1").fetchone())

def get_race_state_full() -> dict:
    """Returns race_state with embedded heats + rounds (mirrors Supabase join format)."""
    state    = get_race_state()
    heat_id  = state.get("current_heat_id")
    round_id = state.get("current_round_id")
    with get_conn() as conn:
        heat   = conn.execute("SELECT * FROM heats  WHERE id=?", (heat_id,)).fetchone()  if heat_id  else None
        round_ = conn.execute("SELECT * FROM rounds WHERE id=?", (round_id,)).fetchone() if round_id else None
    state["heats"]  = dict(heat)   if heat   else None
    state["rounds"] = dict(round_) if round_ else None
    if state["heats"] and round_:
        state["heats"]["rounds"] = dict(round_)
    return state

def update_race_state(patch: dict) -> dict:
    allowed = {"current_round_id","current_heat_id","email_enabled","scoring_mode"}
    patch   = {k: v for k, v in patch.items() if k in allowed}
    patch["updated_at"] = _now()
    sets   = ", ".join(f"{k}=?" for k in patch)
    params = list(patch.values())
    with get_conn() as conn:
        conn.execute(f"UPDATE race_state SET {sets} WHERE id=1", params)
    return get_race_state()

# ── Internal helpers ─────────────────────────────────────────────

def _build_where(filters: dict) -> tuple[str, list]:
    if not filters:
        return "", []
    clauses, params = [], []
    for k, v in filters.items():
        if isinstance(v, list):
            clauses.append(f"{k} IN ({','.join('?'*len(v))})")
            params.extend(v)
        else:
            clauses.append(f"{k}=?")
            params.append(v)
    return " WHERE " + " AND ".join(clauses), params
