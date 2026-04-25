"""
shaper.py — Phase 2

Orchestration layer. Connects Phase 1 algorithms to MetricsStore + FastAPI.

Architecture:
  TrafficGenerator.add_sim_process()
    → internally calls WFQScheduler.add_process()   (ONE registration only)
    → creates ProcessGenerator thread
  TrafficGenerator._on_packet()
    → calls WFQScheduler.consume(pid, size)
    → WFQScheduler routes through TokenBucket
  Shaper._tick()
    → calls WFQScheduler.snapshot()
    → writes results into MetricsStore
    → MetricsStore is read by WebSocket broadcast loop in main.py

CRITICAL: TrafficGenerator.add_sim_process() already registers the process
with WFQScheduler internally. Shaper must NEVER call scheduler.add_process()
directly — that causes double-registration and a ValueError crash.
"""

import asyncio
import itertools
import time
from typing import Dict

from algorithms.wfq import WFQScheduler
from algorithms.traffic_generator import TrafficGenerator
from metrics_store import MetricsStore


DEFAULT_BANDWIDTH_MBPS = 100.0
TICK_INTERVAL = 0.1  # 100ms — matches WebSocket push rate

# Fake PIDs assigned to named apps. Start high to avoid any real PID collision.
_pid_counter = itertools.count(start=90000)


class Shaper:
    def __init__(self, metrics_store: MetricsStore):
        self.store = metrics_store
        self.mode: str = "sim"
        self.total_bandwidth_mbps: float = DEFAULT_BANDWIDTH_MBPS

        # Single shared scheduler — owns all TokenBuckets
        self._scheduler = WFQScheduler(
            total_bandwidth_bps=DEFAULT_BANDWIDTH_MBPS * 1_000_000
        )

        # TrafficGenerator wraps the scheduler and owns sim threads.
        # It registers processes with the scheduler internally via add_sim_process().
        self._traffic_gen = TrafficGenerator(self._scheduler)

        # name <-> pid mapping (the rest of the stack uses names, algorithms use PIDs)
        self._name_to_pid: Dict[str, int] = {}
        self._pid_to_name: Dict[int, str] = {}

        # weight cache — needed to compute proportional sim load on rebalance
        self._weights: Dict[str, int] = {}

        self._running = False

    # ------------------------------------------------------------------
    # Public API — called by FastAPI endpoints
    # ------------------------------------------------------------------

    def add_app(self, name: str, weight: int = 1):
        if name in self._name_to_pid:
            return  # already tracked, ignore duplicate

        pid = next(_pid_counter)
        self._name_to_pid[name] = pid
        self._pid_to_name[pid] = name
        self._weights[name] = weight

        # Register in MetricsStore
        self.store.add_app(name, weight)

        # Register with TrafficGenerator — this ALSO registers with WFQScheduler
        # internally. Do NOT call self._scheduler.add_process() separately.
        self._traffic_gen.add_sim_process(
            pid=pid,
            name=name,
            weight=float(weight),
            load_mbps=self._sim_load_for(name, weight),
        )

    def remove_app(self, name: str):
        pid = self._name_to_pid.pop(name, None)
        if pid is None:
            return
        self._pid_to_name.pop(pid, None)
        self._weights.pop(name, None)

        # TrafficGenerator.remove_sim_process() also calls scheduler.remove_process()
        self._traffic_gen.remove_sim_process(pid)
        self.store.remove_app(name)

    def set_weight(self, name: str, weight: int):
        pid = self._name_to_pid.get(name)
        if pid is None:
            return
        self._weights[name] = weight
        self.store.set_weight(name, weight)
        # Update the bucket rate inside the scheduler
        self._scheduler.set_weight(pid, float(weight))
        # Adjust sim load so the generator actually stresses the new allocation
        self._traffic_gen.update_load(pid, self._sim_load_for(name, weight))

    def set_mode(self, mode: str):
        assert mode in ("sim", "real"), "mode must be 'sim' or 'real'"
        if mode == self.mode:
            return
        self.mode = mode
        if mode == "sim":
            self._traffic_gen.start()
        else:
            # Phase 3: WinDivert takes over. Stop sim threads.
            self._traffic_gen.stop()

    def set_bandwidth(self, mbps: float):
        self.total_bandwidth_mbps = mbps
        self._scheduler.set_total_bandwidth(mbps * 1_000_000)

    def list_apps(self) -> list:
        return list(self._name_to_pid.keys())

    # Phase 3 entry point — WinDivert will call this per real packet
    def process_packet(self, pid: int, size_bytes: int):
        self._scheduler.consume(pid, size_bytes)

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def run(self):
        self._running = True
        # Start sim traffic threads (no-op if no apps added yet;
        # add_sim_process() will auto-start threads for apps added later)
        self._traffic_gen.start()

        while self._running:
            t = time.perf_counter()
            self._tick()
            elapsed = time.perf_counter() - t
            await asyncio.sleep(max(0.0, TICK_INTERVAL - elapsed))

    def stop(self):
        self._running = False
        self._traffic_gen.stop()

    # ------------------------------------------------------------------
    # Metrics tick — reads from scheduler, writes to store
    # ------------------------------------------------------------------

    def _tick(self):
        """
        WFQScheduler.snapshot() returns a list[WFQSnapshot].
        Each WFQSnapshot has: pid, name, throughput_bps, tokens_left, stats.
        stats is a TokenBucketStats with avg_delay_ms and loss_percent.
        """
        for snap in self._scheduler.snapshot():
            name = snap.name  # WFQSnapshot carries the name set at add_process()
            if name not in self._name_to_pid:
                continue  # stale entry, skip

            self.store.record(
                name=name,
                throughput_mbps=round(snap.throughput_bps / 1_000_000, 3),
                latency_ms=round(snap.stats.avg_delay_ms, 2),
                packet_loss_pct=round(snap.stats.loss_percent, 2),
                tokens_available=int(snap.tokens_left),
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _sim_load_for(self, name: str, weight: int) -> float:
        """
        Target sim load = this app's WFQ share of total bandwidth.
        Slightly over-drives the bucket so throttling effects are visible.
        """
        total = sum(self._weights.values()) or 1
        share_mbps = (weight / total) * self.total_bandwidth_mbps
        # Drive at 120% of allocation so the bucket actually has to work
        return share_mbps * 1.2