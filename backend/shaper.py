"""
shaper.py — Phase 3

Orchestration layer. Connects Phase 1 algorithms to MetricsStore + FastAPI.
Phase 3 adds WinDivertCapture for real packet interception.

Architecture:
  [Sim mode]
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

  [Real mode]  ← Phase 3
    WinDivertCapture._capture_loop()
      → receives real packets from WinDivert
      → resolves PID via port→pid cache (psutil)
      → calls Shaper.process_packet(pid, size_bytes)
          → WFQScheduler.consume(pid, size)
          → returns {action, delay_ms, tokens_left}
      → forwards / delays / drops packet accordingly
    Shaper._tick() runs unchanged — reads real throughput from scheduler

CRITICAL: TrafficGenerator.add_sim_process() already registers the process
with WFQScheduler internally. Shaper must NEVER call scheduler.add_process()
directly — that causes double-registration and a ValueError crash.

Mode switching is mutually exclusive:
  "sim"  → TrafficGenerator threads run, WinDivertCapture is stopped
  "real" → WinDivertCapture runs, TrafficGenerator threads are stopped
"""

import asyncio
import itertools
import logging
import time
from typing import Dict, List

from algorithms.wfq import WFQScheduler
from algorithms.traffic_generator import TrafficGenerator
from metrics_store import MetricsStore
from process_manager import ProcessManager
from windivert_capture import WinDivertCapture

logger = logging.getLogger(__name__)


DEFAULT_BANDWIDTH_MBPS = 100.0
TICK_INTERVAL = 0.1  # 100ms — matches WebSocket push rate

# Fake PIDs assigned to named apps in Sim mode.
# Start high to avoid collision with real PIDs in Real mode.
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

        # Phase 3 — WinDivert capture. Lazy-initialised on first set_mode("real").
        self._capture = WinDivertCapture(self)

        # ProcessManager used in Real mode to resolve process name → actual OS PIDs.
        self._process_manager = ProcessManager()

        # name <-> pid mapping (the rest of the stack uses names, algorithms use PIDs)
        # In Sim mode: one fake PID (90000+) per name.
        # In Real mode: one fake PID per name for the scheduler/TrafficGenerator
        #               registration, PLUS all real OS PIDs tracked separately below.
        self._name_to_pid: Dict[str, int] = {}
        self._pid_to_name: Dict[int, str] = {}

        # Real mode only: name → list of actual OS PIDs currently tracked.
        # WinDivertCapture checks _name_to_pid.values() for fast lookup, so we
        # maintain a flat pid→name map that includes all real PIDs too.
        self._name_to_real_pids: Dict[str, List[int]] = {}

        # weight cache — needed to compute proportional sim load on rebalance
        self._weights: Dict[str, int] = {}

        self._running = False

    # ------------------------------------------------------------------
    # Public API — called by FastAPI endpoints
    # ------------------------------------------------------------------

    def add_app(self, name: str, weight: int = 1):
        if name in self._name_to_pid:
            return  # already tracked, ignore duplicate

        # Assign a fake PID for the scheduler/TrafficGenerator registration.
        # This fake PID is always used as the canonical scheduler key regardless
        # of mode — the scheduler only knows about this one PID per app.
        fake_pid = next(_pid_counter)
        self._name_to_pid[name] = fake_pid
        self._pid_to_name[fake_pid] = name
        self._weights[name] = weight
        self._name_to_real_pids[name] = []

        # Register in MetricsStore
        self.store.add_app(name, weight)

        # Register with TrafficGenerator — this ALSO registers with WFQScheduler
        # internally. Do NOT call self._scheduler.add_process() separately.
        self._traffic_gen.add_sim_process(
            pid=fake_pid,
            name=name,
            weight=float(weight),
            load_mbps=self._sim_load_for(name, weight),
        )

        if self.mode == "real":
            # Sim threads must idle — real packets drive throughput.
            self._traffic_gen.update_load(fake_pid, 0.0)
            # Register all currently running OS PIDs for this name so
            # WinDivert can match captured packets to this app.
            self._register_real_pids(name)

    def remove_app(self, name: str):
        fake_pid = self._name_to_pid.pop(name, None)
        if fake_pid is None:
            return
        self._pid_to_name.pop(fake_pid, None)
        self._weights.pop(name, None)

        # Clean up all real OS PIDs for this app from the capture lookup map.
        for real_pid in self._name_to_real_pids.pop(name, []):
            self._pid_to_name.pop(real_pid, None)

        # TrafficGenerator.remove_sim_process() also calls scheduler.remove_process()
        self._traffic_gen.remove_sim_process(fake_pid)
        self.store.remove_app(name)

    def set_weight(self, name: str, weight: int):
        pid = self._name_to_pid.get(name)
        if pid is None:
            return
        self._weights[name] = weight
        self.store.set_weight(name, weight)
        # Update the bucket rate inside the scheduler
        self._scheduler.set_weight(pid, float(weight))
        # In Sim mode: also adjust the generator load to match new allocation.
        # In Real mode: load is driven by real packets, so skip the sim adjustment.
        if self.mode == "sim":
            self._traffic_gen.update_load(pid, self._sim_load_for(name, weight))

    def set_mode(self, mode: str):
        """
        Switch between 'sim' and 'real' modes.

        sim  → TrafficGenerator threads drive fake packets through the pipeline.
        real → WinDivert intercepts real OS packets; TrafficGenerator idles.

        The two are mutually exclusive. Switching is clean — the outgoing mode
        is fully stopped before the incoming mode starts.
        """
        assert mode in ("sim", "real"), "mode must be 'sim' or 'real'"
        if mode == self.mode:
            return
        self.mode = mode

        if mode == "real":
            # Stop sim traffic generation first.
            self._traffic_gen.stop()
            # Zero out all sim loads so generator threads don't produce packets
            # if they're somehow still ticking.
            for name, pid in self._name_to_pid.items():
                self._traffic_gen.update_load(pid, 0.0)
            # Register real OS PIDs for every currently tracked app.
            for name in list(self._name_to_pid.keys()):
                self._register_real_pids(name)
            # Start real packet capture.
            self._capture.start()

        else:  # mode == "sim"
            # Stop real capture first.
            self._capture.stop()
            # Clear all real OS PID mappings — no longer needed in sim mode.
            for name in list(self._name_to_real_pids.keys()):
                for real_pid in self._name_to_real_pids[name]:
                    self._pid_to_name.pop(real_pid, None)
                self._name_to_real_pids[name] = []
            # Restore sim loads proportional to current weights.
            for name, pid in self._name_to_pid.items():
                self._traffic_gen.update_load(pid, self._sim_load_for(name, self._weights[name]))
            # Restart sim traffic generation.
            self._traffic_gen.start()

    def set_bandwidth(self, mbps: float):
        self.total_bandwidth_mbps = mbps
        self._scheduler.set_total_bandwidth(mbps * 1_000_000)

    def list_apps(self) -> list:
        return list(self._name_to_pid.keys())

    # Phase 3 entry point — WinDivert calls this per real packet.
    # Returns the TokenBucket decision dict so the caller can act on it.
    def process_packet(self, pid: int, size_bytes: int) -> dict:
        """
        Feed a real packet into the shaping engine.

        WinDivert resolves the real OS PID from the port table. The scheduler
        however is keyed on fake PIDs (90000+). This method translates the
        real PID to the canonical fake PID before calling consume().

        If the real PID isn't in _pid_to_name (stale cache, process restarted),
        returns a forward decision so the packet is never silently dropped.
        """
        name = self._pid_to_name.get(pid)
        if name is None:
            return {"action": "forward", "delay_ms": 0.0, "tokens_left": 0.0}

        fake_pid = self._name_to_pid.get(name)
        if fake_pid is None:
            return {"action": "forward", "delay_ms": 0.0, "tokens_left": 0.0}

        return self._scheduler.consume(fake_pid, size_bytes)

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def run(self):
        self._running = True
        # Start sim traffic threads (no-op if no apps added yet;
        # add_sim_process() will auto-start threads for apps added later)
        self._traffic_gen.start()

        _real_pid_refresh_counter = 0
        while self._running:
            t = time.perf_counter()
            self._tick()

            # Refresh real OS PIDs every 5 seconds (50 ticks × 100ms) in Real
            # mode. Handles processes that restart and get new PIDs.
            if self.mode == "real":
                _real_pid_refresh_counter += 1
                if _real_pid_refresh_counter >= 50:
                    _real_pid_refresh_counter = 0
                    for name in list(self._name_to_pid.keys()):
                        self._register_real_pids(name)

            elapsed = time.perf_counter() - t
            await asyncio.sleep(max(0.0, TICK_INTERVAL - elapsed))

    def stop(self):
        self._running = False
        self._traffic_gen.stop()
        # Stop capture if it was running in real mode.
        if self._capture.is_running:
            self._capture.stop()

    # ------------------------------------------------------------------
    # Metrics tick — reads from scheduler, writes to store
    # ------------------------------------------------------------------

    def _tick(self):
        """
        WFQScheduler.snapshot() returns a list[WFQSnapshot].
        Each WFQSnapshot has: pid, name, throughput_bps, tokens_left, stats.
        stats is a TokenBucketStats with avg_delay_ms and loss_percent.

        In Real mode throughput_bps reflects actual intercepted packet bytes;
        in Sim mode it reflects generated fake traffic. The tick logic is
        identical in both modes — the scheduler doesn't care about the source.
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

    def _register_real_pids(self, name: str) -> None:
        """
        Look up all OS PIDs currently running under `name` and add them to
        _pid_to_name so WinDivertCapture can match captured packets to this app.

        Called when switching to Real mode and every 5 seconds thereafter to
        handle processes that restart and get new PIDs.

        The scheduler is still keyed on the fake PID — real PIDs only exist in
        _pid_to_name as a lookup bridge. process_packet() does the translation.
        """
        real_pids = self._process_manager.name_to_pids(name)
        if not real_pids:
            logger.warning(
                "No running process found with name '%s'. "
                "Start the process and it will be picked up within 5 seconds.",
                name,
            )

        # Remove stale real PIDs that are no longer running.
        current = set(self._name_to_real_pids.get(name, []))
        fresh = set(real_pids)

        for stale_pid in current - fresh:
            self._pid_to_name.pop(stale_pid, None)
            logger.debug("Removed stale real PID %d for '%s'.", stale_pid, name)

        for new_pid in fresh - current:
            self._pid_to_name[new_pid] = name
            logger.info("Registered real PID %d for '%s'.", new_pid, name)

        self._name_to_real_pids[name] = list(fresh)

    def _sim_load_for(self, name: str, weight: int) -> float:
        """
        Target sim load = this app's WFQ share of total bandwidth.
        Slightly over-drives the bucket so throttling effects are visible.
        """
        total = sum(self._weights.values()) or 1
        share_mbps = (weight / total) * self.total_bandwidth_mbps
        # Drive at 120% of allocation so the bucket actually has to work
        return share_mbps * 1.2