"""Reusable GTK widgets: application icon, status pill, stat tiles."""

from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ..core.utils import format_percent
from ..resources import APP_ICON_NAME, desktop_index, humanize_process_name


class AppIconTile(Gtk.Box):
    """A 32px rounded tile showing a desktop icon or a coloured monogram."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_size_request(32, 32)
        self.set_valign(Gtk.Align.CENTER)
        self.add_css_class("ram-app-icon")
        self._child: Gtk.Widget | None = None
        self._signature: tuple | None = None
        self._resolver = desktop_index()

    def _clear_child(self) -> None:
        if self._child is not None:
            self.remove(self._child)
            self._child = None

    def _set_child(self, child: Gtk.Widget) -> None:
        child.set_halign(Gtk.Align.CENTER)
        child.set_valign(Gtk.Align.CENTER)
        self.append(child)
        self._child = child

    def update(self, exe: str | None, name: str, is_service: bool) -> None:
        signature = (exe, name, is_service)
        if signature == self._signature:
            return
        self._signature = signature
        for tone in range(6):
            self.remove_css_class(f"tone-{tone}")
        self.remove_css_class("service")
        if is_service:
            self.add_css_class("service")

        visual = self._resolver.resolve(exe, name, is_service)
        if visual.icon is not None:
            image = Gtk.Image.new_from_gicon(visual.icon)
            image.set_pixel_size(19)
            self._clear_child()
            self._set_child(image)
        elif is_service:
            image = Gtk.Image.new_from_icon_name("preferences-system-symbolic")
            image.set_pixel_size(17)
            image.add_css_class("dim-label")
            self._clear_child()
            self._set_child(image)
        else:
            display = humanize_process_name(name)
            initial = next((ch for ch in display if ch.isalnum()), "?").upper()
            label = Gtk.Label(label=initial)
            label.add_css_class("ram-monogram")
            tone = sum(ord(ch) for ch in (exe or name)) % 6
            self.add_css_class(f"tone-{tone}")
            self._clear_child()
            self._set_child(label)

        tooltip = visual.title or humanize_process_name(name)
        if exe:
            tooltip = f"{tooltip}\n{exe}"
        self.set_tooltip_text(tooltip)


class StatusPill(Gtk.Box):
    """Healthy / elevated / critical / paused status indicator."""

    LEVELS = {
        "healthy": "Healthy",
        "elevated": "Elevated",
        "critical": "Critical",
        "paused": "Paused",
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.set_valign(Gtk.Align.CENTER)
        self.add_css_class("ram-status-pill")
        self._dot = Gtk.Box()
        self._dot.add_css_class("ram-status-dot")
        self._dot.set_valign(Gtk.Align.CENTER)
        self.append(self._dot)
        self._label = Gtk.Label(label="Healthy")
        self.append(self._label)
        self._level = "healthy"
        self._set_class("healthy")

    def _set_class(self, level: str) -> None:
        for name in ("healthy", "elevated", "critical", "paused"):
            self.remove_css_class(name)
        self.add_css_class(level)

    def set_level(self, level: str, tooltip: str | None = None) -> None:
        if level not in self.LEVELS:
            level = "healthy"
        self._level = level
        self._label.set_label(self.LEVELS[level])
        self._set_class(level)
        if tooltip:
            self.set_tooltip_text(tooltip)


class StatTile(Gtk.Box):
    def __init__(
        self,
        icon_name: str,
        title: str,
        tooltip: str,
        *,
        value: str = "—",
        subtitle: str = "",
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add_css_class("ram-stat-tile")
        self.set_tooltip_text(tooltip)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        chip = Gtk.Box()
        chip.add_css_class("ram-icon-chip")
        chip.set_size_request(28, 28)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(15)
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_valign(Gtk.Align.CENTER)
        chip.append(icon)
        top.append(chip)
        label = Gtk.Label(label=title)
        label.add_css_class("ram-stat-title")
        label.set_xalign(0)
        label.set_valign(Gtk.Align.CENTER)
        top.append(label)
        self.append(top)
        self._value = Gtk.Label(label=value)
        self._value.set_xalign(0)
        self._value.add_css_class("ram-stat-value")
        self.append(self._value)
        self._subtitle = Gtk.Label(label=subtitle)
        self._subtitle.set_xalign(0)
        self._subtitle.set_ellipsize(3)
        self._subtitle.add_css_class("ram-stat-sub")
        self.append(self._subtitle)

    def set_value(self, value: str, subtitle: str | None = None) -> None:
        self._value.set_label(value)
        if subtitle is not None:
            self._subtitle.set_label(subtitle)


@dataclass(frozen=True)
class MetricText:
    value: str
    subtitle: str


def percent_text(used: float, total: float) -> str:
    return format_percent(used / total * 100 if total else 0.0, 0)


def build_brand_header() -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    image = Gtk.Image.new_from_icon_name(APP_ICON_NAME)
    image.set_pixel_size(26)
    box.append(image)
    labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    labels.set_valign(Gtk.Align.CENTER)
    title = Gtk.Label(label="RAMPilot")
    title.add_css_class("ram-title")
    title.set_xalign(0)
    labels.append(title)
    subtitle = Gtk.Label(label="Memory monitor")
    subtitle.add_css_class("ram-subtitle")
    subtitle.set_xalign(0)
    labels.append(subtitle)
    box.append(labels)
    return box
