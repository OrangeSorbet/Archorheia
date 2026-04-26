"""
main.py — FastAPI backend
Run with: python backend/main.py
Docs:     http://localhost:8000/docs
WS:       ws://localhost:8000/ws/metrics
"""

import asyncio
import json
import logging
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from process_manager import ProcessManager
from metrics_store import MetricsStore
from shaper import Shaper

app = FastAPI(title="NetShaper Backend", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

process_manager = ProcessManager()
metrics_store   = MetricsStore()
shaper          = Shaper(metrics_store)

_ws_clients: list[WebSocket] = []


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class AddProcessRequest(BaseModel):
    name:   str = Field(..., description="Process name e.g. 'chrome.exe'")
    weight: int = Field(1, ge=1, le=10)

class SetWeightRequest(BaseModel):
    name:   str
    weight: int = Field(..., ge=1, le=10)

class SetModeRequest(BaseModel):
    mode: str = Field(..., description="'sim' or 'real'")

class AddSimAppRequest(BaseModel):
    name:   str = Field(..., description="Fake app name for Sim mode")
    weight: int = Field(1, ge=1, le=10)

class SetRateLimitRequest(BaseModel):
    name: str
    rate_value: float = Field(..., gt=0)
    rate_unit: str = Field("mbps")

class SetBandwidthRequest(BaseModel):
    rate_value: float = Field(..., gt=0)
    rate_unit: str = Field("mbps", description="mbps|kbps|gbps|MBs|KBs|GBs")


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/processes", summary="List all running processes")
def get_processes():
    """Full process list — used by Sim mode Add App modal."""
    return {"processes": process_manager.get_all_processes()}


@app.get("/network-processes", summary="List processes with active network connections")
def get_network_processes():
    """
    Returns only processes that currently have active network (inet) connections.
    Grouped by process name — multi-PID apps (Chrome, Firefox) appear as one row.
    Sorted by IO activity descending (most active first).

    Each entry:
      name        - e.g. 'chrome.exe'
      pid         - lowest PID in the group
      pid_count   - number of PIDs sharing this name on the network
      status      - OS process status of the representative PID
      memory_mb   - total RSS across all PIDs in the group
      bytes_sent  - cumulative IO read bytes (activity proxy)
      bytes_recv  - cumulative IO write bytes (activity proxy)

    Used by: Real mode Add App modal (refreshed every 3s while modal is open).
    """
    return {"processes": process_manager.get_network_processes()}


@app.post("/add-process", summary="Add a real process to the shaper")
def add_process(req: AddProcessRequest):
    shaper.add_app(req.name, req.weight)
    return {"ok": True, "added": req.name, "weight": req.weight}


@app.post("/add-sim-app", summary="Add a fake app in Sim mode")
def add_sim_app(req: AddSimAppRequest):
    shaper.add_app(req.name, req.weight)
    return {"ok": True, "added": req.name, "weight": req.weight}


@app.post("/set-weight", summary="Change WFQ priority weight for a process")
def set_weight(req: SetWeightRequest):
    shaper.set_weight(req.name, req.weight)
    return {"ok": True, "name": req.name, "weight": req.weight}


@app.post("/set-mode", summary="Toggle Real / Sim mode")
def set_mode(req: SetModeRequest):
    shaper.set_mode(req.mode)
    return {"ok": True, "mode": req.mode}


_UNIT_TO_BPS = {
    "kbps": 1_000, "mbps": 1_000_000, "gbps": 1_000_000_000,
    "kbs":  8_000, "mbs":  8_000_000, "gbs":  8_000_000_000,
}

@app.post("/set-rate-limit", summary="Set per-app token bucket rate limit")
def set_rate_limit(req: SetRateLimitRequest):
    bps = req.rate_value * _UNIT_TO_BPS.get(req.rate_unit.lower(), 1_000_000)
    shaper.set_rate_limit(req.name, bps)
    return {"ok": True, "name": req.name, "rate_bps": bps}
    
@app.post("/set-bandwidth", summary="Set total pipe bandwidth")
def set_bandwidth(req: SetBandwidthRequest):
    bps = req.rate_value * _UNIT_TO_BPS.get(req.rate_unit.lower(), 1_000_000)
    mbps = bps / 1_000_000
    shaper.set_bandwidth(mbps)
    return {"ok": True, "total_bandwidth_mbps": mbps}

@app.get("/status", summary="Current shaper state")
def get_status():
    return {
        "mode": shaper.mode,
        "bandwidth_mbps": shaper.total_bandwidth_mbps,
        "metrics": metrics_store.snapshot(),
    }


# ---------------------------------------------------------------------------
# WebSocket — live metrics stream
# ---------------------------------------------------------------------------

@app.websocket("/ws/metrics")
async def ws_metrics(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)


async def _broadcast_loop():
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
                if ws in _ws_clients:
                    _ws_clients.remove(ws)
        await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    asyncio.create_task(shaper.run())
    asyncio.create_task(_broadcast_loop())
    logging.getLogger(__name__).info("NetShaper backend started. Mode: %s", shaper.mode)


@app.on_event("shutdown")
async def shutdown():
    shaper.stop()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
