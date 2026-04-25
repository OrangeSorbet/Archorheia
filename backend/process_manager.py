import psutil


class ProcessManager:
    """
    Thin wrapper around psutil.
    Provides a full list of running processes and pid → app name resolution.
    Phase 3 adds: port → pid resolution for WinDivert packet tagging.
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

    def port_to_pid(self, port: int, proto: str = "tcp") -> int | None:
        """
        Phase 3 — WinDivert packet → PID resolution.

        WinDivert captures packets at the network layer with no PID attached.
        We resolve PID by matching the packet's local port against the OS
        connection table via psutil.net_connections().

        Args:
            port:  Local port number extracted from the captured packet header.
            proto: 'tcp' or 'udp' (lowercase). Defaults to 'tcp'.

        Returns:
            The integer PID owning that port, or None if not found.

        Notes:
            - psutil.net_connections() requires no special privileges on Windows
              for most connections, but some system processes may be inaccessible.
            - This is called per-packet in the capture hot loop, so it must be
              fast. psutil caches the connection table internally per call —
              callers should batch lookups where possible (see WinDivertCapture
              which refreshes the table every 500ms rather than per-packet).
            - UDP connections may not appear in the table until the socket has
              sent or received at least one datagram.
        """
        kind = proto.lower()  # 'tcp' or 'udp'
        try:
            for conn in psutil.net_connections(kind=kind):
                if conn.laddr and conn.laddr.port == port and conn.pid is not None:
                    return conn.pid
        except (psutil.AccessDenied, OSError):
            # On some Windows configurations net_connections() needs elevation.
            # If we're already running as admin (required for WinDivert anyway)
            # this should never fire.
            pass
        return None