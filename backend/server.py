"""
Pinewood Derby — FastAPI Local Backend
Serves REST API + WebSocket realtime + static frontend files.
"""
import json, uuid, os, asyncio, aiofiles
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Query, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

import db
from scoring_heat   import HeatScoring
from scoring_sensor import SensorScoring

ROOT    = Path(__file__).parent.parent
UPLOADS = ROOT / "uploads"
UPLOADS.mkdir(exist_ok=True)

# ── WebSocket broadcast hub ──────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws) if hasattr(self.active, 'discard') else None
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, event: dict):
        data = json.dumps(event)
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

manager = ConnectionManager()

async def broadcast(table: str, event_type: str, row: dict):
    await manager.broadcast({"table": table, "event": event_type, "record": row})

# ── App factory ──────────────────────────────────────────────────
def create_app(config: dict) -> FastAPI:
    db.init_db(config.get("num_lanes", 4))

    heat_scoring   = HeatScoring(config, broadcast)
    sensor_scoring = SensorScoring(config, broadcast) \
        if config.get("scoring_mode") in ("sensor_remote", "sensor_gpio") else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield

    app = FastAPI(title="Pinewood Derby", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"])

    # ── WebSocket ────────────────────────────────────────────────
    @app.websocket("/api/ws")
    async def websocket_endpoint(ws: WebSocket):
        await manager.connect(ws)
        try:
            while True:
                await ws.receive_text()  # keep alive
        except WebSocketDisconnect:
            manager.disconnect(ws)

    # ── Cars ─────────────────────────────────────────────────────
    @app.get("/api/cars")
    async def get_cars(device_token: str = None, id: str = None,
                       eliminated: str = None):
        filters = {}
        if device_token: filters["device_token"] = device_token
        if id:           filters["id"] = id
        if eliminated is not None:
            filters["eliminated"] = 1 if eliminated.lower() == "true" else 0
        return db.list_cars(filters or None)

    @app.post("/api/cars")
    async def create_car(body: dict):
        car = db.insert_car(body)
        await broadcast("cars", "INSERT", car)
        return car

    @app.patch("/api/cars/{car_id}")
    async def patch_car(car_id: str, body: dict):
        car = db.update_car(car_id, body)
        await broadcast("cars", "UPDATE", car)
        return car

    @app.delete("/api/cars/{car_id}")
    async def remove_car(car_id: str):
        db.delete_car(car_id)
        await broadcast("cars", "DELETE", {"id": car_id})
        return {"ok": True}

    # ── Rounds ───────────────────────────────────────────────────
    @app.get("/api/rounds")
    async def get_rounds():
        return db.list_rounds()

    @app.post("/api/rounds")
    async def create_round(body: dict):
        rnd = db.insert_round(body)
        await broadcast("rounds", "INSERT", rnd)
        return rnd

    @app.patch("/api/rounds/{round_id}")
    async def patch_round(round_id: str, body: dict):
        rnd = db.update_round(round_id, body)
        await broadcast("rounds", "UPDATE", rnd)
        return rnd

    # ── Heats ────────────────────────────────────────────────────
    @app.get("/api/heats")
    async def get_heats(round_id: str = None, include_round: bool = False):
        return db.list_heats(round_id, include_round=include_round)

    # ── Full state (joined race_state + current heat + round) ────
    @app.get("/api/state")
    async def get_full_state():
        return db.get_race_state_full()

    @app.post("/api/heats")
    async def create_heat(request: Request):
        body = await request.json()
        if isinstance(body, list):
            heats = []
            for h in body:
                heat = db.insert_heat(h)
                await broadcast("heats", "INSERT", heat)
                heats.append(heat)
            heats.sort(key=lambda h: h["heat_number"])
            return heats
        heat = db.insert_heat(body)
        await broadcast("heats", "INSERT", heat)
        return heat

    @app.patch("/api/heats/{heat_id}")
    async def patch_heat(heat_id: str, body: dict):
        heat = db.update_heat(heat_id, body)
        await broadcast("heats", "UPDATE", heat)
        return heat

    # ── Heat entries ─────────────────────────────────────────────
    @app.get("/api/heat_entries")
    async def get_heat_entries(heat_id: list[str] = Query(default=None)):
        return db.list_heat_entries(heat_id if heat_id else None)

    @app.post("/api/heat_entries/bulk")
    async def create_heat_entries(body: list):
        entries = db.insert_heat_entries(body)
        await broadcast("heat_entries", "INSERT", {"count": len(entries)})
        return entries

    @app.delete("/api/heat_entries/{heat_id}")
    async def remove_heat_entries(heat_id: str):
        db.delete_heat_entries(heat_id)
        return {"ok": True}

    # ── Heat results ─────────────────────────────────────────────
    @app.get("/api/heat_results")
    async def get_heat_results(round_id: list[str] = Query(default=None), heat_id: str = None):
        if heat_id:
            r = db.get_heat_result(heat_id)
            return [r] if r else []
        return db.list_heat_results(round_id if round_id else None)

    @app.post("/api/heat_results")
    async def create_heat_result(body: dict):
        result = db.upsert_heat_result(body)
        await broadcast("heat_results", "INSERT", result)
        return result

    @app.delete("/api/heat_results/{heat_id}")
    async def remove_heat_result(heat_id: str):
        db.delete_heat_result(heat_id)
        await broadcast("heat_results", "DELETE", {"heat_id": heat_id})
        return {"ok": True}

    # ── Race state ───────────────────────────────────────────────
    @app.get("/api/race_state")
    async def get_state():
        return db.get_race_state()

    @app.patch("/api/race_state")
    async def patch_state(body: dict):
        state = db.update_race_state(body)
        await broadcast("race_state", "UPDATE", state)
        return state

    # ── File storage ─────────────────────────────────────────────
    @app.post("/api/storage/upload")
    async def upload_file(bucket: str, name: str, file: UploadFile = File(...)):
        dest = UPLOADS / bucket
        dest.mkdir(exist_ok=True)
        path = dest / name
        async with aiofiles.open(path, "wb") as f:
            await f.write(await file.read())
        url = f"/api/storage/{bucket}/{name}"
        return {"url": url}

    @app.get("/api/storage/{bucket}/{name}")
    async def serve_file(bucket: str, name: str):
        path = UPLOADS / bucket / name
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path)

    @app.get("/api/storage/{bucket}")
    async def list_storage(bucket: str, prefix: str = ""):
        dest = UPLOADS / bucket
        dest.mkdir(exist_ok=True)
        files = []
        for p in sorted(dest.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file() and p.name.startswith(prefix):
                files.append({"name": p.name, "created_at": p.stat().st_mtime})
        return files

    # ── Sensor bridge hit (Module 6 + 7) ─────────────────────────
    @app.post("/api/sensor/hit")
    async def sensor_hit(body: dict):
        """Called by ESP32 or GPIO scorer with {lane, time_ms}"""
        if sensor_scoring:
            result = await sensor_scoring.record_hit(body["lane"], body["time_ms"])
            if result:
                await broadcast("heat_results", "INSERT", result)
        return {"ok": True}

    # ── Email (local SMTP) ───────────────────────────────────────
    @app.post("/api/functions/send-registration-email")
    async def send_email(body: dict):
        from email_sender import send_registration_email
        ok, msg = await send_registration_email(config, body)
        if not ok:
            raise HTTPException(500, detail=msg)
        return {"ok": True, "message": msg}

    # ── Serve frontend static files (Module 4) ───────────────────
    frontend_dir = ROOT / "frontend"
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app
