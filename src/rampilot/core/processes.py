"""Process enumeration for the dashboard and cleanup workflow.

Enumeration runs in a worker thread.  Static metadata is cached per process
instance and dynamic counters are read with psutil.  CPU percentages are
calculated from CPU-time deltas, which avoids needing process objects to stay
alive between scans.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import pwd
import time

import psutil

from .safety import (
    ProcessGuard,
    SYSTEM_EXEC_PREFIXES,
    name_is_service,
)


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    pid: int
    ppid: int
    create_time: float
    name: str
    exe: str | None
    cmdline: str
    username: str
    uid: int
    rss: int
    cpu_percent: float
    status: str
    is_kernel: bool
    is_service: bool
    is_current_user: bool
    protected: bool
    protection_reason: str | None
    accessible: bool = True

    @property
    def memory_mib(self) -> float:
        return self.rss / (1024 * 1024)

    @property
    def cleanup_eligible(self) -> bool:
        """Whether this process may be offered as a cleanup candidate."""
        return (
            self.is_current_user
            and not self.protected
            and not self.is_kernel
            and not self.is_service
            and self.accessible
            and self.pid > 1
        )

    @property
    def subtitle(self) -> str:
        if self.is_kernel:
            return f"Kernel thread · PID {self.pid}"
        owner = self.username or f"uid {self.uid}"
        return f"{owner} · PID {self.pid}"


@dataclass(frozen=True, slots=True)
class ScanResult:
    processes: tuple[ProcessInfo, ...]
    duration_ms: float
    scanned_at: float
    inaccessible: int = 0


@dataclass(slots=True)
class _CachedProcess:
    proc: psutil.Process
    create_time: float
    name: str
    exe: str | None
    cmdline: str
    username: str
    uid: int
    ppid: int
    last_cpu_total: float | None = None
    last_cpu_time: float | None = None
    is_kernel: bool = False
    is_service: bool = False
    protected: bool = False
    protection_reason: str | None = None


def _safe_name(proc: psutil.Process, pid: int) -> str:
    try:
        name = proc.name()
        if name:
            return name
    except (psutil.Error, OSError):
        pass
    return f"Process {pid}"


def _safe_exe(proc: psutil.Process) -> str | None:
    try:
        return proc.exe() or None
    except (psutil.Error, OSError):
        return None


def _safe_cmdline(proc: psutil.Process) -> str:
    try:
        args = proc.cmdline()
    except (psutil.Error, OSError):
        return ""
    # Command lines are untrusted display data: remove NULs/control characters
    # and keep a bounded amount so a malicious process cannot bloat the UI.
    text = " ".join(args)
    text = "".join(ch if ch >= " " and ch != "\x7f" else " " for ch in text)
    return text[:512].strip()


def _safe_username(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except (KeyError, OSError):
        return str(uid)


def _is_kernel_thread(name: str, cmdline: str, uid: int, ppid: int) -> bool:
    return uid == 0 and not cmdline


def _is_service_process(
    name: str,
    exe: str | None,
    uid: int,
    current_uid: int,
    ppid: int,
    is_kernel: bool,
) -> bool:
    if is_kernel or uid != current_uid or uid < 1000:
        return True
    if name_is_service(name):
        return True
    if any(name.startswith(prefix) for prefix in ("gsd-", "systemd-")):
        return True
    if ppid == 1:
        return True
    if exe:
        try:
            path = str(Path(exe))
        except (OSError, ValueError):
            path = ""
        if any(path.startswith(prefix) for prefix in SYSTEM_EXEC_PREFIXES):
            return True
    return False


class ProcessCollector:
    def __init__(self, guard: ProcessGuard | None = None):
        self.guard = guard or ProcessGuard.create()
        self.current_uid = os.getuid()
        self._cache: dict[int, _CachedProcess] = {}
        self._last_scan_monotonic: float | None = None

    def reset_cpu(self) -> None:
        for cached in self._cache.values():
            cached.last_cpu_total = None
            cached.last_cpu_time = None

    def clear(self) -> None:
        self._cache.clear()
        self._last_scan_monotonic = None

    def scan(self) -> ScanResult:
        started = time.monotonic()
        pids = psutil.pids()
        now = time.monotonic()
        new_cache: dict[int, _CachedProcess] = {}
        result: list[ProcessInfo] = []
        inaccessible = 0

        for pid in pids:
            cached = self._cache.get(pid)
            try:
                if cached is None:
                    cached = self._create_cached(pid)
                    if cached is None:
                        continue
                else:
                    # Detect PID reuse before doing anything with the object.
                    try:
                        current_create_time = psutil.Process(pid).create_time()
                    except psutil.NoSuchProcess:
                        continue
                    if current_create_time != cached.create_time:
                        cached = self._create_cached(pid)
                        if cached is None:
                            continue

                rss, cpu_total, status, accessible = self._read_dynamic(cached)
                cpu_percent = 0.0
                if accessible and cached.last_cpu_total is not None and cached.last_cpu_time is not None:
                    elapsed = now - cached.last_cpu_time
                    if 0.01 < elapsed < 120:
                        delta = cpu_total - cached.last_cpu_total
                        if delta >= 0:
                            cpu_percent = min(delta / elapsed * 100.0, 6400.0)
                if accessible:
                    cached.last_cpu_total = cpu_total
                    cached.last_cpu_time = now
                if not accessible:
                    inaccessible += 1

                protection_reason = self.guard.protection_reason(
                    pid,
                    cached.name,
                    cached.uid,
                    status=status,
                    cmdline=cached.cmdline,
                )
                info = ProcessInfo(
                    pid=pid,
                    ppid=cached.ppid,
                    create_time=cached.create_time,
                    name=cached.name,
                    exe=cached.exe,
                    cmdline=cached.cmdline,
                    username=cached.username,
                    uid=cached.uid,
                    rss=rss,
                    cpu_percent=round(cpu_percent, 1),
                    status=status,
                    is_kernel=cached.is_kernel,
                    is_service=cached.is_service,
                    is_current_user=cached.uid == self.current_uid,
                    protected=protection_reason is not None,
                    protection_reason=protection_reason,
                    accessible=accessible,
                )
                result.append(info)
                new_cache[pid] = cached
            except (psutil.NoSuchProcess, psutil.ZombieProcess, ProcessLookupError):
                continue
            except (psutil.AccessDenied, PermissionError):
                inaccessible += 1
                continue
            except (OSError, ValueError):
                continue

        self._cache = new_cache
        self._last_scan_monotonic = now
        result.sort(key=lambda item: (item.rss, item.name.lower()), reverse=True)
        return ScanResult(
            processes=tuple(result),
            duration_ms=(time.monotonic() - started) * 1000.0,
            scanned_at=time.time(),
            inaccessible=inaccessible,
        )

    def _create_cached(self, pid: int) -> _CachedProcess | None:
        try:
            proc = psutil.Process(pid)
            with proc.oneshot():
                create_time = proc.create_time()
                name = _safe_name(proc, pid)
                ppid = proc.ppid()
                try:
                    uid = proc.uids().real
                except (psutil.Error, OSError):
                    uid = -1
                exe = _safe_exe(proc)
                cmdline = _safe_cmdline(proc)
                username = _safe_username(uid) if uid >= 0 else "unknown"
                try:
                    status = proc.status()
                except (psutil.Error, OSError):
                    status = "unknown"
            is_kernel = _is_kernel_thread(name, cmdline, uid, ppid)
            is_service = _is_service_process(
                name, exe, uid, self.current_uid, ppid, is_kernel
            )
            reason = self.guard.protection_reason(
                pid, name, uid, status=status, cmdline=cmdline
            )
            return _CachedProcess(
                proc=proc,
                create_time=create_time,
                name=name,
                exe=exe,
                cmdline=cmdline,
                username=username,
                uid=uid,
                ppid=ppid,
                is_kernel=is_kernel,
                is_service=is_service,
                protected=reason is not None,
                protection_reason=reason,
            )
        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, OSError):
            return None

    def _read_dynamic(self, cached: _CachedProcess) -> tuple[int, float, str, bool]:
        proc = cached.proc
        try:
            with proc.oneshot():
                try:
                    rss = proc.memory_info().rss
                except (psutil.AccessDenied, psutil.ZombieProcess):
                    return 0, 0.0, "unknown", False
                try:
                    times = proc.cpu_times()
                    cpu_total = float(times.user + times.system)
                except (psutil.AccessDenied, psutil.ZombieProcess):
                    cpu_total = 0.0
                try:
                    status = proc.status()
                except (psutil.AccessDenied, psutil.ZombieProcess):
                    status = "unknown"
            return max(0, int(rss)), cpu_total, status, True
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            raise
        except (psutil.AccessDenied, PermissionError):
            return 0, 0.0, "unknown", False
        except OSError as exc:
            # ESRCH means it disappeared between pids() and reading it.
            if getattr(exc, "errno", None) == 3:
                raise psutil.NoSuchProcess(proc.pid) from exc
            return 0, 0.0, "unknown", False


class ProcessScanner:
    """Background scanner; results are delivered on a caller-provided callback.

    The callback runs on the worker thread.  The UI is responsible for
    marshalling it onto the GTK main loop (e.g. via GLib.idle_add).
    """

    def __init__(
        self,
        collector: ProcessCollector,
        on_result,
        interval: float = 2.0,
        on_error=None,
    ):
        self.collector = collector
        self._on_result = on_result
        self._on_error = on_error
        self._interval = max(1.0, float(interval))
        self._thread = None
        self._stop = None
        self._wake = None
        self._enabled = None
        self._paused = False

    def start(self) -> None:
        import threading

        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._enabled = threading.Event()
        self._enabled.set()
        self._thread = threading.Thread(
            target=self._run, name="rampilot-process-scanner", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._wake is not None:
            self._wake.set()
        if self._enabled is not None:
            self._enabled.set()

    def set_interval(self, interval: float) -> None:
        self._interval = max(1.0, float(interval))
        if self._wake is not None:
            self._wake.set()

    def request_refresh(self) -> None:
        if self._wake is not None:
            self._wake.set()

    def pause(self) -> None:
        self._paused = True
        if self._enabled is not None:
            self._enabled.clear()

    def resume(self) -> None:
        self._paused = False
        self.collector.reset_cpu()
        if self._enabled is not None:
            self._enabled.set()
        self.request_refresh()

    @property
    def paused(self) -> bool:
        return self._paused

    def _run(self) -> None:
        assert self._stop is not None and self._wake is not None and self._enabled is not None
        while not self._stop.is_set():
            if not self._enabled.wait(timeout=0.5):
                continue
            if self._stop.is_set():
                break
            loop_started = time.monotonic()
            try:
                result = self.collector.scan()
            except Exception as exc:  # keep the monitor alive on unexpected errors
                if self._on_error is not None:
                    self._on_error(exc)
                result = None
            if result is not None and not self._stop.is_set():
                try:
                    self._on_result(result)
                except Exception:
                    pass
            elapsed = time.monotonic() - loop_started
            wait_time = max(0.5, self._interval - elapsed)
            self._wake.wait(timeout=wait_time)
            self._wake.clear()
