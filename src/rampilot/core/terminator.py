"""PID-reuse-safe process termination.

A pidfd (when available) is opened *before* validation and used to deliver the
signal.  A pidfd remains bound to the original process even if its numeric PID
is recycled, which closes the usual kill-by-PID race.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import signal
import time

import psutil

from .safety import ProcessGuard, same_process_alive


@dataclass(frozen=True, slots=True)
class TerminationTarget:
    pid: int
    name: str
    create_time: float
    rss: int = 0


@dataclass(frozen=True, slots=True)
class TerminationResult:
    pid: int
    name: str
    #: "sent" (signal delivered), "exited" (already gone), "refused", "error"
    outcome: str
    message: str
    forced: bool = False

    @property
    def signalled(self) -> bool:
        return self.outcome == "sent"

    @property
    def ok(self) -> bool:
        return self.outcome in ("sent", "exited")


class SafeTerminator:
    def __init__(self, guard: ProcessGuard | None = None):
        self.guard = guard or ProcessGuard.create()

    def _open_pidfd(self, pid: int) -> int | None:
        if not hasattr(os, "pidfd_open"):
            return None
        try:
            return os.pidfd_open(pid, 0)
        except OSError as exc:
            # ESRCH = already gone; fall through to psutil for a clean result.
            if exc.errno == 3:  # ESRCH
                return None
            return None

    def _send_via_pidfd(self, fd: int | None, pid: int, sig: int) -> None:
        if fd is not None and hasattr(signal, "pidfd_send_signal"):
            signal.pidfd_send_signal(fd, sig)
            return
        raise OSError("Safe process signalling requires Linux pidfd support")

    def terminate(
        self,
        target: TerminationTarget,
        *,
        force: bool = False,
    ) -> TerminationResult:
        sig = signal.SIGKILL if force else signal.SIGTERM
        fd: int | None = self._open_pidfd(target.pid)
        proc: psutil.Process | None = None
        try:
            try:
                proc = psutil.Process(target.pid)
            except psutil.NoSuchProcess:
                return TerminationResult(
                    target.pid, target.name, "exited", "Process has already exited", force
                )
            try:
                current_create_time = proc.create_time()
            except psutil.NoSuchProcess:
                return TerminationResult(
                    target.pid, target.name, "exited", "Process has already exited", force
                )
            except (psutil.AccessDenied, OSError) as exc:
                return TerminationResult(
                    target.pid,
                    target.name,
                    "refused",
                    f"Could not verify process identity: {exc}",
                    force,
                )
            if current_create_time != target.create_time:
                return TerminationResult(
                    target.pid,
                    target.name,
                    "refused",
                    "Refused: the PID now belongs to a different process",
                    force,
                )

            allowed, reason = self.guard.can_terminate(proc)
            if not allowed:
                return TerminationResult(
                    target.pid, target.name, "refused", f"Refused: {reason}", force
                )
            try:
                status = proc.status()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                status = ""
            if status in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                return TerminationResult(
                    target.pid, target.name, "exited", "Process has already exited", force
                )
            name = proc.name()
            if psutil.Process(target.pid).create_time() != target.create_time:
                return TerminationResult(
                    target.pid,
                    target.name,
                    "refused",
                    "Refused: process changed while it was being verified",
                    force,
                )
            try:
                self._send_via_pidfd(fd, target.pid, sig)
            except ProcessLookupError:
                return TerminationResult(
                    target.pid, target.name, "exited", "Process has already exited", force
                )
            except PermissionError:
                return TerminationResult(
                    target.pid,
                    target.name,
                    "refused",
                    "Permission denied by the system",
                    force,
                )
            except OSError as exc:
                if exc.errno == 3:
                    return TerminationResult(
                        target.pid,
                        target.name,
                        "exited",
                        "Process has already exited",
                        force,
                    )
                return TerminationResult(
                    target.pid,
                    target.name,
                    "error",
                    f"Could not signal process: {exc.strerror or exc}",
                    force,
                )
            verb = "force-closed" if force else "asked to close"
            return TerminationResult(
                target.pid,
                name,
                "sent",
                f"Sent {'SIGKILL' if force else 'SIGTERM'}; process was {verb}",
                force,
            )
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return TerminationResult(target.pid, target.name, "exited", "Process has already exited", force)
        except (psutil.AccessDenied, PermissionError):
            return TerminationResult(target.pid, target.name, "refused", "Permission denied by the system", force)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def refresh_alive(self, targets: list[TerminationTarget]) -> list[TerminationTarget]:
        """Return targets still alive as the same process instance."""
        return [
            target
            for target in targets
            if same_process_alive(target.pid, target.create_time)
        ]

    @staticmethod
    def wait_until_exit(
        targets: list[TerminationTarget], timeout: float = 3.0, poll: float = 0.1
    ) -> list[TerminationTarget]:
        """Blocking helper for tests; the UI polls asynchronously instead."""
        deadline = time.monotonic() + timeout
        remaining = list(targets)
        while time.monotonic() < deadline:
            remaining = [
                target
                for target in remaining
                if same_process_alive(target.pid, target.create_time)
            ]
            if not remaining:
                break
            time.sleep(poll)
        return remaining
