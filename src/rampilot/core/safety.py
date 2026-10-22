"""Safety rules for process termination.

The cleanup feature is deliberately conservative: only the current user's
ordinary application processes can be signalled, a PID must still refer to the
same process instance, and session-critical processes are refused outright.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import signal

import psutil

# Names of programs which keep a Linux desktop/session alive.  Matching is on
# the process name (without brackets/paths).  Never add user applications here.
PROTECTED_SERVICE_NAMES = {
    "Hyprland", "niri", "river", "weston", "cosmic-comp", "cosmic-session",
    "xfsettingsd", "mate-settings-daemon", "cinnamon-settings-daemon",
    "kscreen_backend_launcher", "kactivitymanagerd", "kwalletd5", "kwalletd6",
    "systemd",
    "init",
    "kthreadd",
    "kworker",
    "gnome-shell",
    "gnome-session",
    "gnome-session-b",
    "gnome-session-binary",
    "gnome-settings-daemon",
    "gnome-keyring-daemon",
    "gnome-session-ctl",
    "gsd-datetime",
    "gsd-housekeeping",
    "gsd-media-keys",
    "gsd-power",
    "gsd-print-notifications",
    "gsd-rfkill",
    "gsd-screensaver-proxy",
    "gsd-sharing",
    "gsd-smartcard",
    "gsd-sound",
    "gsd-usb-protection",
    "gsd-wacom",
    "gsd-xsettings",
    "Xorg",
    "Xwayland",
    "X",
    "gdm",
    "gdm3",
    "gdm-session-worker",
    "sddm",
    "sddm-helper",
    "lightdm",
    "lightdm-gtk-greeter",
    "gnome-remote-desktop",
    "dbus-daemon",
    "dbus-broker",
    "dbus-broker-launch",
    "dbus-launch",
    "pipewire",
    "pipewire-pulse",
    "pulseaudio",
    "wireplumber",
    "jackd",
    "plasmashell",
    "plasma-desktop",
    "kwin_x11",
    "kwin_wayland",
    "ksmserver",
    "kded5",
    "kded6",
    "kglobalaccel5",
    "kglobalaccel6",
    "xfce4-session",
    "xfce4-panel",
    "xfwm4",
    "xfdesktop",
    "mate-session",
    "mate-panel",
    "marco",
    "cinnamon",
    "cinnamon-session",
    "cinnamon-settings-daemon",
    "lxsession",
    "lxpanel",
    "openbox",
    "i3",
    "sway",
    "wayfire",
    "labwc",
    "hyprland",
    "sshd",
    "login",
    "polkitd",
    "polkitd-pkla",
    "udisksd",
    "upowerd",
    "accounts-daemon",
    "NetworkManager",
    "wpa_supplicant",
    "bluetoothd",
    "cupsd",
    "avahi-daemon",
    "snapd",
    "containerd",
    "dockerd",
    "at-spi-bus-launcher",
    "at-spi2-registryd",
    "xdg-desktop-portal",
    "xdg-document-portal",
    "xdg-permission-store",
    "ibus-daemon",
    "fcitx5",
    "fcitx",
    "gvfsd",
    "gvfsd-fuse",
    "gnome-terminal-server",
    "kgx",
}

PROTECTED_PREFIXES = (
    "gsd-",
    "xdg-desktop-portal-",
    "systemd-",
    "kworker/",
    "kworker-",
    "migration/",
    "rcu_",
    "rcu-",
    "watchdog/",
    "cpuhp/",
    "idle_inject/",
    "irq/",
    "ksoftirqd/",
    "at-spi",
    "ibus-",
    "gvfsd-",
    "gnome-shell-",
    "wayland-",
    "Xwayland-",
)

SYSTEM_EXEC_PREFIXES = (
    "/usr/sbin/",
    "/sbin/",
    "/usr/lib/systemd/",
    "/lib/systemd/",
    "/usr/libexec/",
    "/lib/systemd/system/",
)

SYSTEM_NAME_PREFIXES = (
    "gsd-",
    "systemd-",
    "xdg-desktop-portal",
)

# Daemons and desktop infrastructure which happen to run as the logged-in user.
# The process name is enough to make a safe decision here.
SYSTEM_USER_SERVICES = {
    "gnome-shell",
    "gnome-session-binary",
    "gnome-session-b",
    "gnome-settings-daemon",
    "pipewire",
    "pipewire-pulse",
    "wireplumber",
    "pulseaudio",
    "dbus-daemon",
    "dbus-broker",
    "gvfsd",
    "gnome-keyring-daemon",
    "xdg-desktop-portal",
    "xdg-document-portal",
    "xdg-permission-store",
    "at-spi-bus-launcher",
    "at-spi2-registryd",
    "tracker-miner-fs",
    "ibus-daemon",
    "fcitx5",
    "kgx",
}


def _name_matches(name: str, table: set[str], prefixes: tuple[str, ...]) -> bool:
    if name in table:
        return True
    return any(name.startswith(prefix) for prefix in prefixes)


def name_is_protected(name: str) -> bool:
    return _name_matches(name or "", PROTECTED_SERVICE_NAMES, PROTECTED_PREFIXES)


def name_is_service(name: str) -> bool:
    return _name_matches(name or "", SYSTEM_USER_SERVICES, SYSTEM_NAME_PREFIXES)


def collect_ancestor_pids(pid: int | None = None) -> frozenset[int]:
    """Return the process and all of its ancestors, including PID 1."""
    current = os.getpid() if pid is None else int(pid)
    pids = {current}
    seen: set[int] = set()
    while current > 1 and current not in seen and len(seen) < 128:
        seen.add(current)
        try:
            parent = psutil.Process(current).ppid()
        except (psutil.Error, OSError):
            break
        if parent <= 0 or parent in pids:
            break
        pids.add(parent)
        current = parent
    pids.add(1)
    return frozenset(pids)


@dataclass(frozen=True, slots=True)
class ProcessGuard:
    """Context used both for display and for final pre-kill validation."""

    uid: int
    protected_pids: frozenset[int]
    app_pid: int

    @classmethod
    def create(cls) -> "ProcessGuard":
        return cls(
            uid=os.getuid(),
            protected_pids=collect_ancestor_pids(),
            app_pid=os.getpid(),
        )

    def protection_reason(
        self,
        pid: int,
        name: str,
        uid: int,
        *,
        status: str = "",
        cmdline: str = "",
    ) -> str | None:
        if pid <= 1:
            return "Essential system process"
        if pid == self.app_pid or pid in self.protected_pids:
            return "RAMPilot or one of its parent processes"
        if uid != self.uid:
            return "Owned by another user"
        if status in ("zombie", "dead"):
            return "Process has already exited"
        if not cmdline:
            return "Kernel thread"
        if name_is_protected(name):
            return "Essential desktop or system service"
        return None

    def can_terminate(self, process: psutil.Process) -> tuple[bool, str]:
        """Validate a live psutil process; returns (allowed, reason)."""
        try:
            pid = process.pid
            if pid <= 1:
                return False, "Essential system process"
            if pid == self.app_pid or pid in self.protected_pids:
                return False, "RAMPilot or one of its parent processes"
            uids = process.uids()
            uid = uids.real
            if uid != self.uid or uid != os.getuid():
                return False, "Only processes owned by the current user can be closed"
            status = process.status()
            if status in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                return False, "Process has already exited"
            try:
                cmdline = " ".join(process.cmdline())
            except (psutil.AccessDenied, psutil.ZombieProcess):
                cmdline = ""
            name = process.name()
            if name_is_protected(name):
                return False, "Essential desktop or system service"
            if not cmdline:
                return False, "Could not verify application command line"
            exe = process.exe()
            if name_is_protected(os.path.basename(exe)) or name_is_service(name) or any(exe.startswith(prefix) for prefix in SYSTEM_EXEC_PREFIXES):
                return False, "System or desktop service"
            if process.ppid() == 1:
                return False, "Background service or orphaned process"
            if uid == 0:
                return False, "System processes cannot be closed"
            return True, ""
        except psutil.NoSuchProcess:
            return False, "Process has already exited"
        except psutil.AccessDenied:
            return False, "Permission denied by the system"
        except psutil.Error as exc:
            return False, f"Could not inspect process: {exc}"


def same_process_alive(pid: int, create_time: float) -> bool:
    """True if pid still identifies the same, non-zombie process instance."""
    try:
        proc = psutil.Process(pid)
        if proc.create_time() != create_time:
            return False
        return proc.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return False
