import psutil


class ProcessManager:
    """
    Thin wrapper around psutil.
    Provides a full list of running processes and pid → app name resolution.
    """

    def get_all_processes(self) -> list[dict]:
        """
        Returns every running process on the machine.
        Each entry: { pid, name, status, cpu_percent, memory_mb }
        Frontend handles any filtering.
        """
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
                # Process died between iteration steps or we lack permissions — skip it
                continue

        return sorted(processes, key=lambda p: p["name"].lower())

    def pid_to_name(self, pid: int) -> str | None:
        """Resolves a PID to its process name. Returns None if not found."""
        try:
            return psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    def name_to_pids(self, name: str) -> list[int]:
        """Returns all PIDs currently running under a given process name."""
        result = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"] == name:
                    result.append(proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return result
