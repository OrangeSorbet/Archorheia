"""
Weighted Fair Queuing (WFQ) Scheduler.

Manages a set of processes, each with a weight (1–10).
Computes each process's fair bandwidth share and hands that
rate to its TokenBucket.

Mathematical model
------------------
  share_i = (weight_i / Σ weights) × total_bandwidth_bps

Example
-------
  Fitgirl weight=5, Chrome weight=2, Steam weight=1
  Total weight = 8
  Fitgirl share = 5/8 × BW = 62.5 %
  Chrome  share = 2/8 × BW = 25.0 %
  Steam   share = 1/8 × BW = 12.5 %
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from .token_bucket import TokenBucket, TokenBucketStats


# ── data classes ──────────────────────────────────────────────────────────────

@dataclass
class ProcessEntry:
    pid: int
    name: str
    weight: float                       # 1–10
    bucket: TokenBucket = field(repr=False)
    added_at: float = field(default_factory=time.time)

    # Rolling throughput tracked by WFQ (bytes in last window)
    _bytes_this_window: int = field(default=0, repr=False)

    def record_bytes(self, n: int) -> None:
        self._bytes_this_window += n

    def flush_window(self) -> int:
        b = self._bytes_this_window
        self._bytes_this_window = 0
        return b


@dataclass
class WFQSnapshot:
    """One-shot metrics snapshot for a single process — sent to dashboard."""
    pid: int
    name: str
    weight: float
    share_percent: float
    allocated_bps: float
    throughput_bps: float               # measured over last window
    tokens_left: float
    stats: TokenBucketStats


# ── scheduler ─────────────────────────────────────────────────────────────────

class WFQScheduler:
    """
    Thread-safe WFQ scheduler.

    Parameters
    ----------
    total_bandwidth_bps : total pipe bandwidth (bits/sec)
    min_weight          : floor so no process starves
    """

    MIN_RATE_BPS = 10_000       # 10 Kbps floor — never let a bucket hit zero

    def __init__(
        self,
        total_bandwidth_bps: float = 100 * 1e6,   # 100 Mbps default
        min_weight: float = 1.0,
    ):
        self._lock = threading.Lock()
        self._processes: Dict[int, ProcessEntry] = {}   # pid → entry
        self._total_bw = total_bandwidth_bps
        self._min_weight = min_weight
        self._last_window = time.perf_counter()

    # ── process management ────────────────────────────────────────────────────

    def add_process(self, pid: int, name: str, weight: float = 5.0) -> ProcessEntry:
        """Register a new process. Rebalances all buckets automatically."""
        weight = self._clamp_weight(weight)
        with self._lock:
            if pid in self._processes:
                raise ValueError(f"PID {pid} already registered")
            rate = self._compute_share(pid_extra_weight=weight)
            bucket = TokenBucket(rate_bps=rate)
            entry = ProcessEntry(pid=pid, name=name, weight=weight, bucket=bucket)
            self._processes[pid] = entry
            self._rebalance()
            return entry

    def remove_process(self, pid: int) -> None:
        """Unregister a process and rebalance remaining buckets."""
        with self._lock:
            self._processes.pop(pid, None)
            self._rebalance()

    def set_weight(self, pid: int, weight: float) -> None:
        """Live-update a process's priority weight (called by dashboard slider)."""
        weight = self._clamp_weight(weight)
        with self._lock:
            if pid not in self._processes:
                raise KeyError(f"PID {pid} not found")
            self._processes[pid].weight = weight
            self._rebalance()

    def set_total_bandwidth(self, bps: float) -> None:
        """Update the total pipe bandwidth and rebalance."""
        with self._lock:
            self._total_bw = bps
            self._rebalance()

    # ── packet path ───────────────────────────────────────────────────────────

    def consume(self, pid: int, packet_size_bytes: int) -> dict:
        """
        Run a packet through the WFQ → TokenBucket pipeline.

        Returns the TokenBucket result dict, or {"action": "drop", ...} if
        the PID is unknown.
        """
        with self._lock:
            entry = self._processes.get(pid)
        if entry is None:
            return {"action": "drop", "delay_ms": 0.0, "tokens_left": 0.0, "reason": "unknown_pid"}

        result = entry.bucket.consume(packet_size_bytes)
        if result["action"] in ("forward", "delay"):
            entry.record_bytes(packet_size_bytes)
        return result

    # ── metrics ───────────────────────────────────────────────────────────────

    def snapshot(self) -> list[WFQSnapshot]:
        """
        Collect per-process metrics snapshot.
        Resets byte counters for the next window.
        """
        now = time.perf_counter()
        with self._lock:
            elapsed = max(now - self._last_window, 1e-6)
            self._last_window = now
            total_w = self._total_weight()

            snapshots = []
            for entry in self._processes.values():
                bytes_sent = entry.flush_window()
                throughput = (bytes_sent * 8) / elapsed      # bps
                share_pct = (entry.weight / total_w * 100) if total_w > 0 else 0
                allocated = self._rate_for_weight(entry.weight, total_w)

                snapshots.append(WFQSnapshot(
                    pid=entry.pid,
                    name=entry.name,
                    weight=entry.weight,
                    share_percent=share_pct,
                    allocated_bps=allocated,
                    throughput_bps=throughput,
                    tokens_left=entry.bucket.peek_tokens(),
                    stats=entry.bucket.reset_stats(),
                ))
            return snapshots

    def get_pids(self) -> list[int]:
        with self._lock:
            return list(self._processes.keys())

    def process_names(self) -> Dict[int, str]:
        with self._lock:
            return {pid: e.name for pid, e in self._processes.items()}

    # ── internal helpers ──────────────────────────────────────────────────────

    def _rebalance(self) -> None:
        """Recalculate and push new rates to all buckets. Must hold lock."""
        total_w = self._total_weight()
        for entry in self._processes.values():
            rate = self._rate_for_weight(entry.weight, total_w)
            entry.bucket.update_rate(rate)

    def _total_weight(self) -> float:
        return sum(e.weight for e in self._processes.values()) or 1.0

    def _rate_for_weight(self, weight: float, total_weight: float) -> float:
        rate = (weight / total_weight) * self._total_bw
        return max(rate, self.MIN_RATE_BPS)

    def _compute_share(self, pid_extra_weight: float) -> float:
        """Compute hypothetical rate for a new process being added."""
        total_w = self._total_weight() + pid_extra_weight
        return self._rate_for_weight(pid_extra_weight, total_w)

    @staticmethod
    def _clamp_weight(w: float) -> float:
        return max(1.0, min(10.0, float(w)))

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        with self._lock:
            procs = ", ".join(
                f"{e.name}(w={e.weight})" for e in self._processes.values()
            )
        return f"WFQScheduler(bw={self._total_bw/1e6:.0f} Mbps, [{procs}])"
