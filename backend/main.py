"""
main.py

FastAPI backend — Phase 2.
Run with:  python backend/main.py
Then open: http://localhost:8000/docs  to test every endpoint via the auto-generated UI.
WebSocket:  ws://localhost:8000/ws/metrics
"""

import asyncio
import json
import uvicorn

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from process_manager import ProcessManager
from metrics_store import MetricsStore
from shaper import Shaper


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="NetShaper Backend", version="2.0.0")

# Open CORS for localhost — required when Electron / React dev server calls this
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tightened to localhost only in production build
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Singletons
process_manager = ProcessManager()
metrics_store = MetricsStore()
shaper = Shaper(metrics_store)

# Connected WebSocket clients
_ws_clients: list[WebSocket] = []


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class AddProcessRequest(BaseModel):
    name: str = Field(..., description="Process name e.g. 'chrome.exe'")
    weight: int = Field(1, ge=1, le=10, description="Initial WFQ weight (1–10)")

class SetWeightRequest(BaseModel):
    name: str
    weight: int = Field(..., ge=1, le=10)

class SetModeRequest(BaseModel):
    mode: str = Field(..., description="'sim' or 'real'")

class AddSimAppRequest(BaseModel):
    name: str = Field(..., description="Fake app name for Sim mode")
    weight: int = Field(1, ge=1, le=10)

class SetBandwidthRequest(BaseModel):
    mbps: float = Field(..., gt=0, description="Total pipe bandwidth in Mbps")


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/processes", summary="List all running processes")
def get_processes():
    """Returns every running process on the machine. Frontend handles filtering."""
    return {"processes": process_manager.get_all_processes()}


@app.post("/add-process", summary="Add a real process to the shaper")
def add_process(req: AddProcessRequest):
    """
    Adds a process (by name) to the shaping pipeline.
    In Sim mode the TrafficGenerator will simulate its traffic immediately.
    """
    shaper.add_app(req.name, req.weight)
    return {"ok": True, "added": req.name, "weight": req.weight}


@app.post("/set-weight", summary="Change priority weight for a process")
def set_weight(req: SetWeightRequest):
    """
    Updates the WFQ weight for a tracked process.
    Weight range: 1 (lowest) – 10 (highest).
    Bandwidth shares rebalance immediately across all tracked apps.
    """
    shaper.set_weight(req.name, req.weight)
    return {"ok": True, "name": req.name, "weight": req.weight}


@app.post("/set-mode", summary="Toggle Real / Sim mode")
def set_mode(req: SetModeRequest):
    """
    'sim'  — TrafficGenerator drives fake packets through the algorithm pipeline.
    'real' — WinDivert (Phase 3) feeds real packets. No-op until Phase 3 is wired.
    """
    shaper.set_mode(req.mode)
    return {"ok": True, "mode": req.mode}


@app.post("/add-sim-app", summary="Add a fake app in Sim mode")
def add_sim_app(req: AddSimAppRequest):
    """
    Adds a fake application to the shaper (for demo / Sim mode use).
    Behaves identically to add-process — TrafficGenerator handles it.
    """
    shaper.add_app(req.name, req.weight)
    return {"ok": True, "added": req.name, "weight": req.weight}


@app.get("/status", summary="Current state of all tracked apps")
def get_status():
    """Returns the latest metrics snapshot for every tracked app."""
    return {
        "mode": shaper.mode,
        "total_bandwidth_mbps": shaper.total_bandwidth_mbps,
        "apps": metrics_store.snapshot(),
    }


@app.post("/set-bandwidth", summary="Set total pipe bandwidth")
def set_bandwidth(req: SetBandwidthRequest):
    """
    Overrides the default 100 Mbps total bandwidth.
    WFQ shares rebalance immediately.
    """
    shaper.set_bandwidth(req.mbps)
    return {"ok": True, "total_bandwidth_mbps": req.mbps}


# ---------------------------------------------------------------------------
# WebSocket — live metrics stream
# ---------------------------------------------------------------------------

@app.websocket("/ws/metrics")
async def ws_metrics(websocket: WebSocket):
    """
    Pushes a JSON snapshot of all tracked app metrics every ~100ms.

    Example payload:
    {
      "chrome.exe": {
        "throughput_mbps": 4.2, "latency_ms": 12,
        "packet_loss_pct": 0.0, "weight": 5, "tokens_available": 830
      },
      ...
    }

    Test from browser console:
      const ws = new WebSocket("ws://localhost:8000/ws/metrics");
      ws.onmessage = e => console.log(JSON.parse(e.data));
    """
    await websocket.accept()
    _ws_clients.append(websocket)
    try:
        while True:
            # Keep connection alive — actual pushes come from the broadcast loop
            await websocket.receive_text()
    except WebSocketDisconnect:
        _ws_clients.remove(websocket)


async def _broadcast_loop():
    """Runs forever — pushes metrics snapshot to every connected WS client every 100ms."""
    while True:
        if _ws_clients:
            payload = json.dumps(metrics_store.snapshot())
            dead = []
            for ws in _ws_clients:
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                _ws_clients.remove(ws)
        await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    # Start the shaper tick loop and WebSocket broadcast loop as background tasks
    asyncio.create_task(shaper.run())
    asyncio.create_task(_broadcast_loop())


@app.on_event("shutdown")
async def shutdown():
    shaper.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
