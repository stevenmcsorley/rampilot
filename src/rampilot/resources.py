"""Application resources (CSS, icons) and desktop-entry icon lookup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

APP_ICON_NAME = "io.github.rampilot.RAMPilot"
BROOM_ICON_NAME = "rampilot-broom-symbolic"

_DATA_DIR = Path(__file__).resolve().parent / "data"
ICON_THEME_ROOT = _DATA_DIR / "icons"
STYLE_PATH = _DATA_DIR / "style.css"
APP_ICON_PATH = (
    ICON_THEME_ROOT / "hicolor" / "scalable" / "apps" / f"{APP_ICON_NAME}.svg"
)


def install_resources() -> None:
    display = Gdk.Display.get_default()
    if display is None:
        return
    theme = Gtk.IconTheme.get_for_display(display)
    theme.add_search_path(str(ICON_THEME_ROOT))
    provider = Gtk.CssProvider()
    try:
        provider.load_from_path(str(STYLE_PATH))
    except GLib.Error:
        # A broken theme must not make the monitoring app unusable.
        return
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


@dataclass(frozen=True)
class AppVisual:
    title: str | None
    icon: Gio.Icon | None
    desktop_id: str | None = None


def humanize_process_name(name: str) -> str:
    text = (name or "Unknown").strip()
    if not text:
        return "Unknown"
    if text.isupper() and len(text) > 3:
        return text
    if text.islower():
        return text[:1].upper() + text[1:]
    return text


class DesktopIndex:
    """Maps executable names to installed desktop entries, once per app run."""

    def __init__(self) -> None:
        self._by_key: dict[str, Gio.DesktopAppInfo] = {}
        self._visuals: dict[tuple[str, str, bool], AppVisual] = {}
        self._built = False

    def _build(self) -> None:
        if self._built:
            return
        self._built = True
        data_dirs = [GLib.get_user_data_dir()]
        for directory in GLib.get_system_data_dirs():
            if directory not in data_dirs:
                data_dirs.append(directory)
        for data_dir in data_dirs:
            apps_dir = Path(data_dir) / "applications"
            try:
                entries = sorted(apps_dir.glob("*.desktop"))
            except OSError:
                continue
            for path in entries:
                try:
                    info = Gio.DesktopAppInfo.new_from_filename(str(path))
                except (TypeError, GLib.Error, OSError):
                    continue
                if info is None or info.get_nodisplay():
                    continue
                desktop_id = info.get_id()
                if desktop_id:
                    self._by_key.setdefault(desktop_id, info)
                    self._by_key.setdefault(Path(desktop_id).stem, info)
                    self._by_key.setdefault(Path(desktop_id).stem.lower(), info)
                exec_line = ""
                try:
                    exec_line = info.get_string("Exec") or ""
                except (AttributeError, GLib.Error):
                    pass
                if not exec_line:
                    continue
                try:
                    parts = shlex.split(exec_line)
                except ValueError:
                    parts = exec_line.split()
                for token in parts[:1]:
                    if token.startswith("%"):
                        continue
                    key = Path(token).name
                    if key:
                        self._by_key.setdefault(key, info)
                        self._by_key.setdefault(key.lower(), info)
                    self._by_key.setdefault(token, info)
                try:
                    wm_class = info.get_startup_wm_class()
                except (AttributeError, GLib.Error):
                    wm_class = None
                if wm_class:
                    self._by_key.setdefault(wm_class, info)
                    self._by_key.setdefault(wm_class.lower(), info)

    def resolve(self, exe: str | None, name: str, is_service: bool) -> AppVisual:
        key = (exe or "", name or "", bool(is_service))
        cached = self._visuals.get(key)
        if cached is not None:
            return cached
        self._build()
        info = None
        if exe:
            info = self._by_key.get(exe) or self._by_key.get(str(Path(exe).name).lower())
            info = info or self._by_key.get(str(Path(exe).name))
        if info is None and name:
            info = self._by_key.get(name) or self._by_key.get(name.lower())
        if info is not None:
            try:
                title = info.get_display_name()
            except (AttributeError, GLib.Error):
                title = None
            try:
                icon = info.get_icon()
            except (AttributeError, GLib.Error):
                icon = None
            visual = AppVisual(title=title, icon=icon, desktop_id=info.get_id())
        else:
            visual = AppVisual(title=None, icon=None)
        if len(self._visuals) >= 1024:
            self._visuals.pop(next(iter(self._visuals)))
        self._visuals[key] = visual
        return visual


_resolver: DesktopIndex | None = None


def desktop_index() -> DesktopIndex:
    global _resolver
    if _resolver is None:
        _resolver = DesktopIndex()
    return _resolver
