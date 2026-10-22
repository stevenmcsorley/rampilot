"""Animated circular memory gauge drawn with Cairo."""

from __future__ import annotations

import math
import time

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib, Gtk, Pango, PangoCairo

from ..core.utils import format_bytes


_COLORS = {
    "healthy": ((0.42, 0.95, 0.78), (0.24, 0.82, 0.95)),
    "elevated": ((0.99, 0.80, 0.38), (0.99, 0.56, 0.24)),
    "critical": ((0.99, 0.55, 0.60), (0.98, 0.30, 0.37)),
    "paused": ((0.64, 0.69, 0.78), (0.48, 0.54, 0.64)),
}


def _layout(cr, text: str, size_px: float, *, bold: bool = False, color=(0.91, 0.94, 0.97), alpha=1.0):
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription()
    desc.set_family("Cantarell")
    desc.set_absolute_size(size_px * Pango.SCALE)
    desc.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    layout.set_font_description(desc)
    layout.set_text(text, -1)
    cr.set_source_rgba(color[0], color[1], color[2], alpha)
    return layout


def _show_centered(cr, layout, cx: float, y: float) -> None:
    width, _height = layout.get_pixel_size()
    cr.move_to(cx - width / 2.0, y)
    PangoCairo.show_layout(cr, layout)


class MemoryGauge(Gtk.DrawingArea):
    """A 270° ring gauge with a restrained animation between values."""

    def __init__(self) -> None:
        super().__init__()
        self.set_content_width(254)
        self.set_content_height(254)
        self.set_draw_func(self._draw)
        self._value = 0.0
        self._target = 0.0
        self._level = "healthy"
        self._used = 0
        self._total = 0
        self._anim_from = 0.0
        self._anim_started = 0.0
        self._timer_id: int | None = None
        self._duration = 0.42

    def set_value(
        self,
        fraction: float,
        used: int,
        total: int,
        level: str = "healthy",
        *,
        animate: bool = True,
    ) -> None:
        target = max(0.0, min(1.0, float(fraction)))
        self._used = used
        self._total = total
        self._level = level if level in _COLORS else "healthy"
        if not animate or abs(target - self._value) < 0.001:
            self._target = target
            self._value = target
            self.queue_draw()
            return
        self._target = target
        if self._timer_id is None:
            self._anim_from = self._value
            self._anim_started = time.monotonic()
            self._timer_id = GLib.timeout_add(16, self._tick)
        else:
            # Re-target from wherever the ring currently is.
            self._anim_from = self._value
            self._anim_started = time.monotonic()
        self.queue_draw()

    def _tick(self) -> bool:
        elapsed = time.monotonic() - self._anim_started
        progress = min(1.0, elapsed / self._duration)
        eased = 1.0 - (1.0 - progress) ** 3
        self._value = self._anim_from + (self._target - self._anim_from) * eased
        if progress >= 1.0:
            self._value = self._target
            self._timer_id = None
            self.queue_draw()
            return False
        self.queue_draw()
        return True

    def _draw(self, _area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        size = min(width, height)
        cx, cy = width / 2.0, height / 2.0
        radius = max(20.0, size / 2.0 - 24.0)
        start = math.radians(135)
        sweep = math.radians(270)
        line_width = max(7.0, size * 0.045)
        color_a, color_b = _COLORS.get(self._level, _COLORS["healthy"])

        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)

        # Tick ring, subtle and instrument-like.
        tick_outer = radius + 12
        for index in range(29):
            angle = start + sweep * index / 28.0
            inner = tick_outer - (4.5 if index % 7 == 0 else 2.5)
            cr.set_line_width(1.1)
            cr.set_source_rgba(1, 1, 1, 0.17 if index % 7 == 0 else 0.08)
            cr.move_to(cx + math.cos(angle) * inner, cy + math.sin(angle) * inner)
            cr.line_to(cx + math.cos(angle) * tick_outer, cy + math.sin(angle) * tick_outer)
            cr.stroke()

        # Track.
        cr.set_line_width(line_width)
        cr.set_source_rgba(1, 1, 1, 0.075)
        cr.arc(cx, cy, radius, start, start + sweep)
        cr.stroke()

        if self._value > 0.001:
            # Soft glow beneath the progress arc.
            cr.set_line_width(line_width * 2.0)
            cr.set_source_rgba(color_a[0], color_a[1], color_a[2], 0.09)
            cr.arc(cx, cy, radius, start, start + sweep * self._value)
            cr.stroke()

            cr.set_line_width(line_width)
            gradient = cairo.LinearGradient(
                cx - radius, cy + radius, cx + radius, cy - radius
            )
            gradient.add_color_stop_rgba(0.0, color_a[0], color_a[1], color_a[2], 1.0)
            gradient.add_color_stop_rgba(1.0, color_b[0], color_b[1], color_b[2], 1.0)
            cr.set_source(gradient)
            cr.arc(cx, cy, radius, start, start + sweep * self._value)
            cr.stroke()

            # End cap dot.
            end = start + sweep * self._value
            dot_x = cx + math.cos(end) * radius
            dot_y = cy + math.sin(end) * radius
            cr.set_source_rgba(0.94, 1.0, 0.99, 0.95)
            cr.arc(dot_x, dot_y, max(2.0, line_width * 0.20), 0, 2 * math.pi)
            cr.fill()

        percent = round(self._value * 100)
        percent_layout = _layout(cr, f"{percent}%", size * 0.175, bold=True)
        pwidth, pheight = percent_layout.get_pixel_size()
        _show_centered(cr, percent_layout, cx, cy - pheight * 0.72)

        label_layout = _layout(
            cr, "RAM IN USE", size * 0.036, bold=True, color=(0.56, 0.63, 0.72)
        )
        lwidth, lheight = label_layout.get_pixel_size()
        _show_centered(cr, label_layout, cx, cy + pheight * 0.34)

        memory_layout = _layout(
            cr,
            f"{format_bytes(self._used, compact=True)} of {format_bytes(self._total, compact=True)}",
            size * 0.04,
            color=(0.72, 0.78, 0.86),
        )
        mwidth, mheight = memory_layout.get_pixel_size()
        _show_centered(cr, memory_layout, cx, cy + pheight * 0.34 + lheight + 5)
