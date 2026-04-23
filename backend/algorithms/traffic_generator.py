"""
Sim Mode Traffic Generator.

Fires synthetic packet objects at configurable rates through the real
WFQScheduler + TokenBucket pipeline — identical algorithm path to Real mode,
just no actual WinDivert packets.

Usage
-----
    gen = TrafficGenerator(scheduler)
    gen.add_sim_process(pid=1001, name="fake_chrome.exe", weight=5, load_mbps=30)
    gen.add_sim_process(pid=1002, name="fake_steam.exe",  weight=2, load_mbps=10)
    gen.start()
    time.sleep(5)
    gen.stop()
"""

import random
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from .wfq import WFQScheduler


# ── fake packet ───────────────────────────────────────────────────────────────

@dataclass
class FakePacket:
    pid: int
    size: int           # bytes
    created_at: float = 0.0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.perf_counter()


# ── per-process generator ─────────────────────────────────────────────────────

class ProcessGenerator:
    """
    Generates fake packets for one simulated process at a target load.

    Parameters
    ----------
    pid        : fake PID
    load_bps   : desired offered load in bits/sec
    pkt_size   : mean packet size in bytes (Gaussian jitter applied)
    """

    MEAN_PKT_SIZE = 1_024       # 1 KB default
    PKT_SIZE_STDDEV = 256

    def __init__(self, pid: int, load_bps: float, pkt_size: int = MEAN_PKT_SIZE):
        self.pid = pid
        self.load_bps = load_bps
        self.pkt_size = pkt_size
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, callback) -> None:
        """callback(packet: FakePacket) is called for each generated packet."""
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(callback,), daemon=True, name=f"gen-{self.pid}"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def update_load(self, load_bps: float) -> None:
        self.load_bps = load_bps

    def _run(self, callback) -> None:
        while not self._stop.is_set():
            # Jitter packet size slightly
            size = max(64, int(random.gauss(self.pkt_size, self.PKT_SIZE_STDDEV)))
            pkt = FakePacket(pid=self.pid, size=size)
            callback(pkt)

            # Inter-packet gap derived from load and packet size
            gap = (size * 8) / max(self.load_bps, 1)
            # Add ±10 % jitter so the stream isn't perfectly clocked
            gap *= random.uniform(0.9, 1.1)
            time.sleep(gap)


# ── traffic generator ─────────────────────────────────────────────────────────

class TrafficGenerator:
    """
    Manages multiple ProcessGenerators and feeds their packets through
    the shared WFQScheduler.
    """

    def __init__(self, scheduler: WFQScheduler):
        self._scheduler = scheduler
        self._generators: Dict[int, ProcessGenerator] = {}
        self._lock = threading.Lock()
        self._running = False

        # Event log (capped at 500 entries) — useful for debugging
        self._events: list[dict] = []
        self._events_lock = threading.Lock()

    # ── sim process management ────────────────────────────────────────────────

    def add_sim_process(
        self,
        pid: int,
        name: str,
        weight: float = 5.0,
        load_mbps: float = 10.0,
    ) -> None:
        """Add a fake process to both the scheduler and the generator."""
        load_bps = load_mbps * 1e6
        self._scheduler.add_process(pid=pid, name=name, weight=weight)
        gen = ProcessGenerator(pid=pid, load_bps=load_bps)
        with self._lock:
            self._generators[pid] = gen
        if self._running:
            gen.start(self._on_packet)

    def remove_sim_process(self, pid: int) -> None:
        with self._lock:
            gen = self._generators.pop(pid, None)
        if gen:
            gen.stop()
        self._scheduler.remove_process(pid)

    def update_load(self, pid: int, load_mbps: float) -> None:
        with self._lock:
            gen = self._generators.get(pid)
        if gen:
            gen.update_load(load_mbps * 1e6)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        with self._lock:
            gens = list(self._generators.values())
        for gen in gens:
            gen.start(self._on_packet)

    def stop(self) -> None:
        self._running = False
        with self._lock:
            gens = list(self._generators.values())
        for gen in gens:
            gen.stop()

    # ── packet callback ───────────────────────────────────────────────────────

    def _on_packet(self, pkt: FakePacket) -> None:
        result = self._scheduler.consume(pkt.pid, pkt.size)
        event = {
            "pid": pkt.pid,
            "size": pkt.size,
            "action": result["action"],
            "delay_ms": result.get("delay_ms", 0.0),
            "ts": pkt.created_at,
        }
        with self._events_lock:
            self._events.append(event)
            if len(self._events) > 500:
                self._events.pop(0)

    # ── inspection ────────────────────────────────────────────────────────────

    def pop_events(self) -> list[dict]:
        """Drain and return all buffered events (called by FastAPI on each tick)."""
        with self._events_lock:
            events, self._events = self._events, []
        return events

    def is_running(self) -> bool:
        return self._running

    def active_pids(self) -> list[int]:
        with self._lock:
            return list(self._generators.keys())
