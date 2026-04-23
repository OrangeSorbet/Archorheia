"""
shaper.py — Phase 2 (FIXED ARCHITECTURE)

Correct orchestration layer for:
TrafficGenerator → WFQScheduler → TokenBucket

NO duplicate logic.
NO manual scheduling.
WFQ is the single source of truth.

Sim mode:
  TrafficGenerator drives packets automatically.

Real mode:
  Placeholder for WinDivert (Phase 3).
"""

import asyncio
import time
import itertools
from typing import Dict

from algorithms.wfq import WFQScheduler
from algorithms.traffic_generator import TrafficGenerator
from metrics_store import MetricsStore


DEFAULT_BANDWIDTH_MBPS = 100.0
TICK_INTERVAL = 0.1  # 100 ms


class Shaper:
    def __init__(self, metrics_store: MetricsStore):
        self.store = metrics_store

        self.mode: str = "sim"
        self.total_bandwidth_mbps: float = DEFAULT_BANDWIDTH_MBPS

        # Core engine
        self._scheduler = WFQScheduler(total_bandwidth_bps=self._mbps_to_bps(self.total_bandwidth_mbps))
        self._traffic_gen = TrafficGenerator(self._scheduler)

        # App tracking
        self._name_to_pid: Dict[str, int] = {}
        self._pid_to_name: Dict[int, str] = {}
        self._pid_counter = itertools.count(1000)

        # Per-app load config (NEW)
        self._load_mbps: Dict[str, float] = {}

        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_app(self, name: str, weight: int = 1, load_mbps: float = 10.0):
        if name in self._name_to_pid:
            return

        pid = next(self._pid_counter)

        self._name_to_pid[name] = pid
        self._pid_to_name[pid] = name
        self._load_mbps[name] = load_mbps

        # Register in WFQ + TrafficGenerator
        self._scheduler.add_process(pid=pid, name=name, weight=weight)

        if self.mode == "sim":
            self._traffic_gen.add_sim_process(
                pid=pid,
                name=name,
                weight=weight,
                load_mbps=load_mbps,
            )

        self.store.add_app(name, weight)

    def remove_app(self, name: str):
        pid = self._name_to_pid.get(name)
        if pid is None:
            return

        self._traffic_gen.remove_sim_process(pid)
        self._scheduler.remove_process(pid)

        del self._name_to_pid[name]
        del self._pid_to_name[pid]
        self._load_mbps.pop(name, None)

        self.store.remove_app(name)

    def set_weight(self, name: str, weight: int):
        pid = self._name_to_pid.get(name)
        if pid is None:
            return

        self._scheduler.set_weight(pid, weight)
        self.store.set_weight(name, weight)

    def set_load(self, name: str, load_mbps: float):
        """NEW: update per-app simulated load"""
        pid = self._name_to_pid.get(name)
        if pid is None:
            return

        self._load_mbps[name] = load_mbps
        self._traffic_gen.update_load(pid, load_mbps)

    def set_mode(self, mode: str):
        assert mode in ("sim", "real")
        self.mode = mode

        if mode == "sim":
            # Restart generators
            for name, pid in self._name_to_pid.items():
                self._traffic_gen.add_sim_process(
                    pid=pid,
                    name=name,
                    weight=1,  # weight already in WFQ
                    load_mbps=self._load_mbps.get(name, 10.0),
                )
            self._traffic_gen.start()

        else:
            self._traffic_gen.stop()

    def set_bandwidth(self, mbps: float):
        self.total_bandwidth_mbps = mbps
        self._scheduler.set_total_bandwidth(self._mbps_to_bps(mbps))

    def list_apps(self):
        return list(self._name_to_pid.keys())

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def run(self):
        self._running = True

        # Start sim if needed
        if self.mode == "sim":
            self._traffic_gen.start()

        while self._running:
            start = time.perf_counter()

            self._tick()

            elapsed = time.perf_counter() - start
            await asyncio.sleep(max(0.0, TICK_INTERVAL - elapsed))

    def stop(self):
        self._running = False
        self._traffic_gen.stop()

    # ------------------------------------------------------------------
    # Tick logic (METRICS ONLY)
    # ------------------------------------------------------------------

    def _tick(self):
        snapshots = self._scheduler.snapshot()

        for snap in snapshots:
            name = snap.name

            throughput_mbps = snap.throughput_bps / 1e6
            latency_ms = snap.stats.avg_delay_ms
            loss_pct = snap.stats.loss_percent
            tokens = int(snap.tokens_left)

            self.store.record(
                name=name,
                throughput_mbps=throughput_mbps,
                latency_ms=latency_ms,
                packet_loss_pct=loss_pct,
                tokens_available=tokens,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mbps_to_bps(mbps: float) -> float:
        return mbps * 1e6