import psutil
from collections import defaultdict


class ProcessManager:
    """
    Thin wrapper around psutil.
    Provides a full list of running processes and pid → app name resolution.
    Phase 3 adds: port → pid resolution for WinDivert packet tagging.
    Phase 4 adds: get_network_processes() for the Real-mode Add App modal.
    """

    def get_all_processes(self) -> list[dict]:
        processes = []
        for proc in psutil.process_iter(["pid", "name", "status", "cpu_percent", "memory_info"]):
            try:
                info = proc.info
                memory_mb = 0.0
                if info["memory_info"]:
                    memory_mb = round(info["memory_info"].rss / (1024 * 1024), 2)
                processes.append({
                    "pid": info["pid"],
                    "name": info["name"] or "unknown",
                    "status": info["status"] or "unknown",
                    "cpu_percent": info["cpu_percent"] or 0.0,
                    "memory_mb": memory_mb,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return sorted(processes, key=lambda p: p["name"].lower())

    def get_network_processes(self) -> list[dict]:
        """
        Returns only processes that currently have active network connections,
        grouped by process name (multi-PID apps like Chrome merged into one row).

        Each entry:
          name        - process name e.g. 'chrome.exe'
          pid         - lowest PID in the group (representative)
          pid_count   - total PIDs sharing this name on the network
          status      - status of the representative PID
          memory_mb   - summed RSS across all PIDs in the group
          bytes_sent  - cumulative IO read bytes (activity proxy)
          bytes_recv  - cumulative IO write bytes (activity proxy)

        Sorted by total IO activity descending (most active first).
        """
        # Step 1: collect all PIDs with active inet connections
        active_pids: set[int] = set()
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.pid is not None:
                    active_pids.add(conn.pid)
        except (psutil.AccessDenied, OSError):
            pass

        if not active_pids:
            return []

        # Step 2: fetch info per PID, group by name
        groups: dict[str, dict] = {}

        for pid in active_pids:
            try:
                proc = psutil.Process(pid)
                name = proc.name() or "unknown"
                status = proc.status()
                mem_mb = round(proc.memory_info().rss / (1024 * 1024), 2)

                try:
                    io = proc.io_counters()
                    sent = io.read_bytes
                    recv = io.write_bytes
                except (psutil.AccessDenied, AttributeError, NotImplementedError):
                    sent = 0
                    recv = 0

                if name not in groups:
                    groups[name] = {
                        "name": name,
                        "pid": pid,
                        "pid_count": 0,
                        "status": status,
                        "memory_mb": 0.0,
                        "bytes_sent": 0,
                        "bytes_recv": 0,
                    }

                g = groups[name]
                g["pid_count"] += 1
                g["memory_mb"] = round(g["memory_mb"] + mem_mb, 2)
                g["bytes_sent"] += sent
                g["bytes_recv"] += recv
                if pid < g["pid"]:
                    g["pid"] = pid

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Step 3: sort by IO activity descending
        return sorted(
            groups.values(),
            key=lambda e: e["bytes_sent"] + e["bytes_recv"],
            reverse=True,
        )

    def pid_to_name(self, pid: int) -> str | None:
        try:
            return psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    def name_to_pids(self, name: str) -> list[int]:
        result = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"] == name:
                    result.append(proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return result

    def port_to_pid(self, port: int, proto: str = "tcp") -> int | None:
        kind = proto.lower()
        try:
            for conn in psutil.net_connections(kind=kind):
                if conn.laddr and conn.laddr.port == port and conn.pid is not None:
                    return conn.pid
        except (psutil.AccessDenied, OSError):
            pass
        return None
