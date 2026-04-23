from collections import deque
import time


# How many data points to keep — 100ms interval × 600 = 60 seconds
MAX_POINTS = 600


class AppMetrics:
    """Holds rolling 60-second history for a single tracked app."""

    def __init__(self, name: str, weight: int = 1):
        self.name = name
        self.weight = weight
        self.history = deque(maxlen=MAX_POINTS)

    def record(self, throughput_mbps: float, latency_ms: float, packet_loss_pct: float, tokens_available: int):
        self.history.append({
            "timestamp": time.time(),
            "throughput_mbps": round(throughput_mbps, 3),
            "latency_ms": round(latency_ms, 2),
            "packet_loss_pct": round(packet_loss_pct, 2),
            "tokens_available": tokens_available,
        })

    def latest(self) -> dict:
        """Returns the most recent snapshot, or zeroed data if nothing recorded yet."""
        if self.history:
            snap = self.history[-1]
        else:
            snap = {
                "timestamp": time.time(),
                "throughput_mbps": 0.0,
                "latency_ms": 0.0,
                "packet_loss_pct": 0.0,
                "tokens_available": 0,
            }
        return {
            **snap,
            "weight": self.weight,
            "name": self.name,
        }


class MetricsStore:
    """Central registry — one AppMetrics entry per tracked process."""

    def __init__(self):
        # app_name (str) → AppMetrics
        self._apps: dict[str, AppMetrics] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def add_app(self, name: str, weight: int = 1):
        if name not in self._apps:
            self._apps[name] = AppMetrics(name, weight)

    def remove_app(self, name: str):
        self._apps.pop(name, None)

    def set_weight(self, name: str, weight: int):
        if name in self._apps:
            self._apps[name].weight = weight

    def list_apps(self) -> list[str]:
        return list(self._apps.keys())

    def has_app(self, name: str) -> bool:
        return name in self._apps

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def record(self, name: str, throughput_mbps: float, latency_ms: float,
               packet_loss_pct: float, tokens_available: int):
        if name in self._apps:
            self._apps[name].record(throughput_mbps, latency_ms, packet_loss_pct, tokens_available)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Returns the latest data point for every tracked app — sent over WebSocket."""
        return {name: app.latest() for name, app in self._apps.items()}
