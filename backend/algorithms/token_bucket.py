"""
Token Bucket — per-process bandwidth shaper.

Each process gets a bucket:
  - capacity  : max burst size in bytes
  - rate      : refill speed in bytes/sec (derived from WFQ weight)

A packet of size N bytes is forwarded immediately if N tokens are available,
otherwise it is delayed until enough tokens have accumulated (or dropped if
the delay would exceed max_delay_ms).
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TokenBucketStats:
    packets_forwarded: int = 0
    packets_dropped: int = 0
    packets_delayed: int = 0
    bytes_forwarded: int = 0
    total_delay_ms: float = 0.0

    @property
    def avg_delay_ms(self) -> float:
        if self.packets_delayed == 0:
            return 0.0
        return self.total_delay_ms / self.packets_delayed

    @property
    def loss_percent(self) -> float:
        total = self.packets_forwarded + self.packets_dropped
        if total == 0:
            return 0.0
        return (self.packets_dropped / total) * 100.0


class TokenBucket:
    """
    Thread-safe Token Bucket for a single process.

    Parameters
    ----------
    rate_bps   : allowed throughput in bits-per-second
    capacity   : burst capacity in bytes (default = 1 second worth of data)
    max_delay_ms : max time we'll hold a packet before dropping it (ms)
    """

    def __init__(
        self,
        rate_bps: float,
        capacity: Optional[float] = None,
        max_delay_ms: float = 500.0,
    ):
        self._lock = threading.Lock()
        self.max_delay_ms = max_delay_ms
        self.stats = TokenBucketStats()

        self._set_rate(rate_bps, capacity)
        self._tokens: float = self._capacity          # start full
        self._last_refill: float = time.perf_counter()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_rate(self, rate_bps: float, capacity: Optional[float] = None) -> None:
        """Live-update the rate (called when WFQ weights change)."""
        with self._lock:
            self._refill()                            # settle current tokens first
            self._set_rate(rate_bps, capacity)
            self._tokens = min(self._tokens, self._capacity)

    def consume(self, packet_size_bytes: int) -> dict:
        """
        Try to consume tokens for a packet.

        Returns a result dict:
          {
            "action"   : "forward" | "delay" | "drop",
            "delay_ms" : float,          # 0 if forwarded immediately
            "tokens_left": float
          }
        """
        with self._lock:
            self._refill()
            needed = float(packet_size_bytes)

            if self._tokens >= needed:
                # Enough tokens — forward immediately
                self._tokens -= needed
                self.stats.packets_forwarded += 1
                self.stats.bytes_forwarded += packet_size_bytes
                return {
                    "action": "forward",
                    "delay_ms": 0.0,
                    "tokens_left": self._tokens,
                }

            # Not enough tokens — calculate wait time
            deficit = needed - self._tokens
            wait_sec = deficit / self._rate_bytes_sec
            wait_ms = wait_sec * 1000.0

            if wait_ms > self.max_delay_ms:
                # Would wait too long — drop
                self.stats.packets_dropped += 1
                return {
                    "action": "drop",
                    "delay_ms": wait_ms,
                    "tokens_left": self._tokens,
                }

            # Delay the packet (tokens will be available after wait)
            self._tokens = 0.0          # consume everything now, rest will refill
            self.stats.packets_delayed += 1
            self.stats.packets_forwarded += 1
            self.stats.bytes_forwarded += packet_size_bytes
            self.stats.total_delay_ms += wait_ms
            return {
                "action": "delay",
                "delay_ms": wait_ms,
                "tokens_left": 0.0,
            }

    def peek_tokens(self) -> float:
        """Return current token count without consuming (for dashboard)."""
        with self._lock:
            self._refill()
            return self._tokens

    def reset_stats(self) -> TokenBucketStats:
        """Atomically reset and return the stats snapshot."""
        with self._lock:
            snapshot = self.stats
            self.stats = TokenBucketStats()
            return snapshot

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_rate(self, rate_bps: float, capacity: Optional[float]) -> None:
        if rate_bps <= 0:
            raise ValueError(f"rate_bps must be > 0, got {rate_bps}")
        self._rate_bps = rate_bps
        self._rate_bytes_sec = rate_bps / 8.0
        # Default burst = 1 second of data (capped between 4 KB and 16 MB)
        if capacity is None:
            capacity = max(4_096, min(self._rate_bytes_sec, 16 * 1024 * 1024))
        self._capacity = float(capacity)

    def _refill(self) -> None:
        """Add tokens based on elapsed time since last refill (must hold lock)."""
        now = time.perf_counter()
        elapsed = now - self._last_refill
        self._last_refill = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_bytes_sec)

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"TokenBucket(rate={self._rate_bps/1e6:.2f} Mbps, "
            f"capacity={self._capacity/1024:.1f} KB, "
            f"tokens={self.peek_tokens()/1024:.1f} KB)"
        )
