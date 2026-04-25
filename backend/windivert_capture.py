"""
windivert_capture.py — Phase 3

Real packet interception using WinDivert + pydivert.

Architecture
------------
                  ┌─────────────────────────────────┐
  Network         │        WinDivertCapture          │
  packets    ───► │                                  │
                  │  CaptureThread                   │
                  │    pydivert.WinDivert.recv()     │
                  │    → port_to_pid() lookup        │
                  │    → shaper.process_packet()     │
                  │    → forward / queue for delay   │
                  │                                  │
                  │  ReinjectorThread                │
                  │    priority queue (by send_at)   │
                  │    → WinDivert.send() on time    │
                  └─────────────────────────────────┘

Threading model
---------------
- One _capture_thread: blocking recv() loop — never sleeps.
- One _reinjector_thread: sleeps until the next scheduled packet is due.
- A threading.Event (_stop_event) signals both threads to exit cleanly.

Packet decisions (from TokenBucket via shaper.process_packet)
--------------------------------------------------------------
  "forward" → re-inject immediately via send()
  "delay"   → push to _delay_queue with scheduled send time; reinjector handles it
  "drop"    → silently discard (TCP retransmit handles recovery, standard shaping)

PID resolution strategy
-----------------------
WinDivert packets carry no PID. We resolve by matching the packet's local port
against the OS TCP/UDP connection table (psutil.net_connections). Rebuilding
that table per-packet is too expensive (~5ms per call), so we cache it and
refresh every PORT_TABLE_REFRESH_MS milliseconds in a background pass inside
the capture loop.

Only packets whose resolved PID is in shaper._name_to_pid.values() are shaped.
All other traffic is re-injected untouched immediately.

Requirements
------------
- Must run as Administrator (WinDivert needs kernel-level access).
- pydivert must be installed: pip install pydivert
- WinDivert driver ships with pydivert on Windows — no separate install needed.
"""

import heapq
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

try:
    import pydivert
except ImportError:
    pydivert = None  # Graceful degradation — ImportError surfaced at start() call

from process_manager import ProcessManager

if TYPE_CHECKING:
    from shaper import Shaper

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resolve the correct WinDivert "forward" layer enum value at import time.
# pydivert versions disagree on the name:
#   older builds  → Layer.FORWARD
#   newer builds  → Layer.NETWORK_FORWARD
# We try both and fall back to None (which makes the reinjector open its
# handle on the default NETWORK layer instead — still correct for send()).
# ---------------------------------------------------------------------------
def _resolve_forward_layer():
    if pydivert is None:
        return None
    for candidate in ("NETWORK_FORWARD", "FORWARD"):
        val = getattr(pydivert.Layer, candidate, None)
        if val is not None:
            logger.debug("Using pydivert.Layer.%s for reinjector handle.", candidate)
            return val
    logger.warning(
        "Could not find a FORWARD layer in pydivert.Layer. "
        "Available values: %s. Reinjector will use default layer.",
        [a for a in dir(pydivert.Layer) if not a.startswith("_")],
    )
    return None

_FORWARD_LAYER = _resolve_forward_layer()

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

# WinDivert filter — capture all outbound + inbound TCP and UDP traffic.
# We do PID filtering in Python rather than in the WinDivert filter string
# because WinDivert filter syntax has no PID predicate.
_WINDIVERT_FILTER = "tcp or udp"

# How often (ms) to rebuild the port→pid lookup table from psutil.
# 500ms is a good balance: low staleness, low CPU overhead.
PORT_TABLE_REFRESH_MS = 500

# How often (ms) the reinjector thread wakes to check the delay queue.
# Smaller = more accurate delays, higher CPU. 5ms is imperceptible to users.
REINJECTOR_POLL_MS = 5

# Maximum packets to hold in the delay queue before we start dropping them.
# Prevents unbounded memory growth if the reinjector falls behind.
MAX_DELAY_QUEUE = 2000


# ---------------------------------------------------------------------------
# WinDivertCapture
# ---------------------------------------------------------------------------

class WinDivertCapture:
    """
    Manages real packet capture and re-injection for Phase 3.

    Usage (called by Shaper):
        capture = WinDivertCapture(shaper)
        capture.start()   # begins interception
        capture.stop()    # stops cleanly, re-injects any queued packets
    """

    def __init__(self, shaper: "Shaper"):
        self._shaper = shaper
        self._process_manager = ProcessManager()

        self._stop_event = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._reinjector_thread: threading.Thread | None = None

        # Priority queue of (send_at_monotonic, packet) tuples.
        # heapq is a min-heap — smallest send_at comes first.
        # Typed as Any because pydivert.Packet has no stub — object would
        # cause Pylance to reject passing the popped item back to wd.send().
        self._delay_queue: list[tuple[float, Any]] = []
        self._queue_lock = threading.Lock()

        # Port → PID cache: {(port, proto): pid}
        # Rebuilt every PORT_TABLE_REFRESH_MS ms inside the capture loop.
        self._port_pid_cache: dict[tuple[int, str], int] = {}
        self._cache_last_refresh: float = 0.0

        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        """
        Open WinDivert handle and start capture + reinjector threads.
        Must be called from an Administrator process.
        Raises ImportError if pydivert is not installed.
        Raises OSError if WinDivert driver cannot be loaded (not admin, or
        driver missing — though pydivert ships the driver).
        """
        if pydivert is None:
            raise ImportError(
                "pydivert is not installed. Run: pip install pydivert"
            )
        if self._running:
            logger.warning("WinDivertCapture.start() called while already running — ignored.")
            return

        self._stop_event.clear()
        self._running = True

        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="windivert-capture",
            daemon=True,
        )
        self._reinjector_thread = threading.Thread(
            target=self._reinjector_loop,
            name="windivert-reinjector",
            daemon=True,
        )

        self._capture_thread.start()
        self._reinjector_thread.start()
        logger.info("WinDivertCapture started — intercepting real packets.")

    def stop(self):
        """
        Signal threads to stop and wait for clean exit.
        Any packets still in the delay queue are re-injected before shutdown.
        """
        if not self._running:
            return
        self._running = False
        self._stop_event.set()

        if self._capture_thread:
            self._capture_thread.join(timeout=3.0)
        if self._reinjector_thread:
            self._reinjector_thread.join(timeout=3.0)

        # Drain any remaining queued packets immediately so connections don't stall.
        self._drain_queue_on_stop()
        logger.info("WinDivertCapture stopped.")

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Capture loop (runs in _capture_thread)
    # ------------------------------------------------------------------

    def _capture_loop(self):
        """
        Opens a WinDivert handle, receives packets one at a time, resolves
        the owning PID, calls shaper.process_packet(), and either forwards,
        delays, or drops each packet.
        """
        try:
            assert pydivert is not None  # guaranteed — checked in start() before threads launch
            with pydivert.WinDivert(_WINDIVERT_FILTER) as wd:
                logger.info("WinDivert handle opened with filter: %s", _WINDIVERT_FILTER)
                while not self._stop_event.is_set():
                    try:
                        packet = wd.recv()
                    except OSError as exc:
                        # Can fire on shutdown when the handle is closed externally.
                        if self._stop_event.is_set():
                            break
                        logger.error("WinDivert recv() error: %s", exc)
                        break

                    self._maybe_refresh_port_table()
                    self._handle_packet(wd, packet)

        except OSError as exc:
            logger.error(
                "Failed to open WinDivert handle. Are you running as Administrator? Error: %s",
                exc,
            )
            self._running = False

    def _handle_packet(self, wd: Any, packet: Any) -> None:
        """
        Core per-packet decision logic.

          1. Extract local port from packet headers.
          2. Resolve PID from port cache.
          3. If PID is tracked → call shaper.process_packet() for a decision.
          4. Act on decision: forward / delay / drop.
          5. If PID is not tracked → forward immediately (untouched).
        """
        pid = self._resolve_pid(packet)

        # Fast path — not a tracked process, pass straight through.
        # _pid_to_name contains both fake PIDs (sim) and real OS PIDs (real mode),
        # so a single membership check covers both cases correctly.
        if pid is None or pid not in self._shaper._pid_to_name:
            try:
                wd.send(packet)
            except OSError:
                pass  # Connection may have closed; drop silently.
            return

        # Feed packet into the shaping engine.
        size_bytes = len(packet.raw)
        result = self._shaper.process_packet(pid, size_bytes)

        action = result.get("action", "forward")
        delay_ms = result.get("delay_ms", 0.0)

        if action == "forward":
            self._reinject_now(wd, packet)

        elif action == "delay":
            send_at = time.monotonic() + (delay_ms / 1000.0)
            self._enqueue_delayed(send_at, packet)

        elif action == "drop":
            # Silent drop — do not call wd.send(). TCP will retransmit.
            logger.debug(
                "Packet dropped — pid=%d, size=%d bytes", pid, size_bytes
            )

        else:
            # Unknown action — safe default is to forward.
            logger.warning("Unknown shaper action '%s' — forwarding packet.", action)
            self._reinject_now(wd, packet)

    # ------------------------------------------------------------------
    # Reinjector loop (runs in _reinjector_thread)
    # ------------------------------------------------------------------

    def _reinjector_loop(self):
        """
        Wakes every REINJECTOR_POLL_MS ms, checks the delay queue for packets
        whose scheduled send time has arrived, and re-injects them.

        Uses a separate WinDivert handle opened in FORWARD layer so we can
        inject without recapturing our own injected packets.
        """
        try:
            assert pydivert is not None  # guaranteed — checked in start() before threads launch
            # _FORWARD_LAYER is resolved at import time to whichever enum value
            # this pydivert build exposes (NETWORK_FORWARD or FORWARD).
            # If neither exists it is None and WinDivert uses the default layer.
            _layer_kwargs = {"layer": _FORWARD_LAYER} if _FORWARD_LAYER is not None else {}
            with pydivert.WinDivert(_WINDIVERT_FILTER, **_layer_kwargs) as wd:
                while not self._stop_event.is_set():
                    self._flush_due_packets(wd)
                    time.sleep(REINJECTOR_POLL_MS / 1000.0)

                # Final flush on shutdown.
                self._flush_due_packets(wd)

        except OSError as exc:
            logger.error("Reinjector WinDivert handle error: %s", exc)

    def _flush_due_packets(self, wd):
        """Re-inject all packets whose send_at time has passed."""
        now = time.monotonic()
        with self._queue_lock:
            while self._delay_queue and self._delay_queue[0][0] <= now:
                _, packet = heapq.heappop(self._delay_queue)
                try:
                    wd.send(packet)
                except OSError:
                    pass  # Connection died while packet was queued — discard.

    # ------------------------------------------------------------------
    # PID resolution
    # ------------------------------------------------------------------

    def _maybe_refresh_port_table(self):
        """
        Rebuilds the port→pid cache if it's stale (older than PORT_TABLE_REFRESH_MS).
        Called once per packet in the capture loop — only does work every 500ms.
        """
        now = time.monotonic()
        if (now - self._cache_last_refresh) * 1000 < PORT_TABLE_REFRESH_MS:
            return  # Still fresh

        new_cache: dict[tuple[int, str], int] = {}
        try:
            import psutil
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr and conn.pid is not None:
                    proto = "tcp" if conn.type.name.startswith("SOCK_STREAM") else "udp"
                    new_cache[(conn.laddr.port, proto)] = conn.pid
        except Exception as exc:
            logger.warning("Port table refresh failed: %s", exc)

        self._port_pid_cache = new_cache
        self._cache_last_refresh = now

    def _resolve_pid(self, packet) -> int | None:
        """
        Extracts the local port from a pydivert packet and maps it to a PID
        using the cached connection table.

        pydivert exposes src_port / dst_port on tcp and udp sub-objects.
        For outbound packets the source port is the local port.
        For inbound packets the destination port is the local port.
        """
        try:
            if packet.tcp:
                proto = "tcp"
                # Outbound: src is local. Inbound: dst is local.
                local_port = packet.src_port if packet.is_outbound else packet.dst_port
            elif packet.udp:
                proto = "udp"
                local_port = packet.src_port if packet.is_outbound else packet.dst_port
            else:
                return None  # ICMP or other — not TCP/UDP, skip

            return self._port_pid_cache.get((local_port, proto))

        except AttributeError:
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reinject_now(self, wd, packet):
        """Re-inject a packet immediately."""
        try:
            wd.send(packet)
        except OSError:
            pass

    def _enqueue_delayed(self, send_at: float, packet):
        """Push a packet onto the delay priority queue."""
        with self._queue_lock:
            if len(self._delay_queue) >= MAX_DELAY_QUEUE:
                # Queue full — drop oldest entry to make room, log a warning.
                logger.warning(
                    "Delay queue full (%d packets) — dropping oldest to prevent overflow.",
                    MAX_DELAY_QUEUE,
                )
                heapq.heappop(self._delay_queue)
            heapq.heappush(self._delay_queue, (send_at, packet))

    def _drain_queue_on_stop(self):
        """
        Called on stop() — re-injects all queued packets immediately so
        active connections don't stall after shaping ends.
        Opens a fresh handle for injection.
        """
        with self._queue_lock:
            if not self._delay_queue:
                return
            packets_to_drain = [heapq.heappop(self._delay_queue)[1]
                                 for _ in range(len(self._delay_queue))]

        if not packets_to_drain or pydivert is None:
            return

        assert pydivert is not None
        try:
            with pydivert.WinDivert(_WINDIVERT_FILTER) as wd:
                for packet in packets_to_drain:
                    try:
                        wd.send(packet)
                    except OSError:
                        pass
        except OSError as exc:
            logger.warning("Could not drain delay queue on stop: %s", exc)