"""Live 60-second memory history graph."""

from __future__ import annotations

import math
import time

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo


_PALETTE = {
    "healthy": ((0.29, 0.87, 0.75), (0.22, 0.74, 0.91)),
    "elevated": ((0.99, 0.75, 0.29), (0.99, 0.58, 0.24)),
    "critical": ((0.98, 0.45, 0.53), (0.97, 0.29, 0.36)),
}


def _pango(cr, text: str, px: float, color, *, alpha: float = 1.0, bold: bool = False):
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription()
    desc.set_family("Cantarell")
    desc.set_absolute_size(px * Pango.SCALE)
    desc.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    layout.set_font_description(desc)
    layout.set_text(text, -1)
    cr.set_source_rgba(color[0], color[1], color[2], alpha)
    return layout


class HistoryGraph(Gtk.DrawingArea):
    def __init__(self, duration: float = 60.0) -> None:
        super().__init__()
        self.duration = duration
        self._points: tuple[tuple[float, float], ...] = ()
        self._now = time.monotonic()
        self._warning = 0.75
        self._critical = 0.90
        self._paused = False
        self._hover_x: float | None = None
        self.set_content_height(126)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        self.add_css_class("ram-history-graph")
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

    def set_data(
        self,
        points: tuple[tuple[float, float], ...],
        *,
        warning: float,
        critical: float,
        paused: bool = False,
    ) -> None:
        self._points = points
        self._now = time.monotonic()
        self._warning = warning
        self._critical = critical
        self._paused = paused
        self.queue_draw()

    def _on_motion(self, _controller, x: float, _y: float) -> None:
        self._hover_x = x
        self.queue_draw()

    def _on_leave(self, _controller) -> None:
        self._hover_x = None
        self.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        if width < 80 or height < 50:
            return
        left = 30.0
        right = 8.0
        top = 8.0
        bottom = 22.0
        plot_x = left
        plot_y = top
        plot_w = max(10.0, width - left - right)
        plot_h = max(10.0, height - top - bottom)

        cr.set_line_width(1.0)
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = plot_y + plot_h * (1.0 - fraction)
            cr.set_source_rgba(1, 1, 1, 0.045 if fraction not in (0.0, 1.0) else 0.075)
            cr.move_to(plot_x, y)
            cr.line_to(plot_x + plot_w, y)
            cr.stroke()
            if fraction in (0.0, 0.5, 1.0):
                label = _pango(cr, f"{int(fraction * 100)}%", 8.5, (0.48, 0.54, 0.63))
                _lw, lh = label.get_pixel_size()
                cr.move_to(left - 7 - _lw, y - lh / 2.0 - 0.5)
                PangoCairo.show_layout(cr, label)

        cr.set_line_width(1.0)
        cr.set_dash((3, 4))
        for value, color in (
            (self._warning, (0.99, 0.75, 0.29)),
            (self._critical, (0.98, 0.45, 0.53)),
        ):
            if value <= 0 or value >= 1.0:
                continue
            y = plot_y + plot_h * (1.0 - value)
            cr.set_source_rgba(color[0], color[1], color[2], 0.22)
            cr.move_to(plot_x, y)
            cr.line_to(plot_x + plot_w, y)
            cr.stroke()
        cr.set_dash(())

        # x-axis labels
        for text, x, align in (
            ("60s ago", plot_x, 0.0),
            ("now", plot_x + plot_w, 1.0),
        ):
            label = _pango(cr, text, 8.5, (0.45, 0.51, 0.6))
            lw, lh = label.get_pixel_size()
            cr.move_to(x - lw * align, height - lh - 2)
            PangoCairo.show_layout(cr, label)

        visible = [
            (ts, value)
            for ts, value in self._points
            if self._now - self.duration <= ts <= self._now + 0.01
        ]
        if len(visible) < 2:
            label = _pango(
                cr,
                "Collecting live samples…",
                10.5,
                (0.5, 0.56, 0.66),
            )
            lw, lh = label.get_pixel_size()
            cr.move_to(plot_x + (plot_w - lw) / 2, plot_y + (plot_h - lh) / 2)
            PangoCairo.show_layout(cr, label)
            return

        coords: list[tuple[float, float]] = []
        for ts, value in visible:
            x = plot_x + plot_w * (1.0 - (self._now - ts) / self.duration)
            x = max(plot_x, min(plot_x + plot_w, x))
            y = plot_y + plot_h * (1.0 - max(0.0, min(1.0, value)))
            coords.append((x, y))

        latest = visible[-1][1]
        if latest >= self._critical:
            color_a, color_b = _PALETTE["critical"]
        elif latest >= self._warning:
            color_a, color_b = _PALETTE["elevated"]
        else:
            color_a, color_b = _PALETTE["healthy"]

        self._smooth_path(cr, coords)
        cr.line_to(coords[-1][0], plot_y + plot_h)
        cr.line_to(coords[0][0], plot_y + plot_h)
        cr.close_path()
        gradient = cairo.LinearGradient(0, plot_y, 0, plot_y + plot_h)
        gradient.add_color_stop_rgba(0.0, color_a[0], color_a[1], color_a[2], 0.26)
        gradient.add_color_stop_rgba(0.75, color_a[0], color_a[1], color_a[2], 0.055)
        gradient.add_color_stop_rgba(1.0, color_a[0], color_a[1], color_a[2], 0.0)
        cr.set_source(gradient)
        cr.fill()

        self._smooth_path(cr, coords)
        line_gradient = cairo.LinearGradient(plot_x, 0, plot_x + plot_w, 0)
        line_gradient.add_color_stop_rgba(0.0, color_a[0], color_a[1], color_a[2], 0.82)
        line_gradient.add_color_stop_rgba(1.0, color_b[0], color_b[1], color_b[2], 1.0)
        cr.set_line_width(2.1)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_source(line_gradient)
        cr.stroke()

        end_x, end_y = coords[-1]
        cr.set_source_rgba(color_b[0], color_b[1], color_b[2], 0.16)
        cr.arc(end_x, end_y, 7.0, 0, math.tau)
        cr.fill()
        cr.set_source_rgba(0.9, 0.97, 0.97, 0.95)
        cr.arc(end_x, end_y, 2.6, 0, math.tau)
        cr.fill()

        if self._hover_x is not None and plot_x <= self._hover_x <= plot_x + plot_w:
            nearest = min(coords, key=lambda point: abs(point[0] - self._hover_x))
            index = coords.index(nearest)
            ts, value = visible[index]
            cr.set_source_rgba(1, 1, 1, 0.22)
            cr.set_line_width(1.0)
            cr.set_dash((2, 3))
            cr.move_to(nearest[0], plot_y)
            cr.line_to(nearest[0], plot_y + plot_h)
            cr.stroke()
            cr.set_dash(())
            cr.set_source_rgba(0.9, 0.97, 0.97, 0.95)
            cr.arc(nearest[0], nearest[1], 3.4, 0, math.tau)
            cr.fill()
            age = max(0, int(round(self._now - ts)))
            text = f"{value * 100:.0f}%  ·  {age}s ago"
            label = _pango(cr, text, 9.0, (0.88, 0.93, 0.99), bold=True)
            lw, lh = label.get_pixel_size()
            box_w, box_h = lw + 14, lh + 8
            bx = min(max(plot_x, nearest[0] - box_w / 2), plot_x + plot_w - box_w)
            by = plot_y + 2
            rounded_rectangle(cr, bx, by, box_w, box_h, 6)
            cr.set_source_rgba(0.08, 0.11, 0.15, 0.94)
            cr.fill_preserve()
            cr.set_source_rgba(1, 1, 1, 0.08)
            cr.set_line_width(1.0)
            cr.stroke()
            cr.move_to(bx + 7, by + 4)
            PangoCairo.show_layout(cr, label)

        if self._paused:
            label = _pango(cr, "PAUSED", 9.0, (0.9, 0.93, 0.97), bold=True)
            lw, lh = label.get_pixel_size()
            box_w, box_h = lw + 16, lh + 8
            bx = plot_x + plot_w - box_w - 4
            by = plot_y + 4
            rounded_rectangle(cr, bx, by, box_w, box_h, 7)
            cr.set_source_rgba(0.08, 0.11, 0.15, 0.86)
            cr.fill()
            cr.move_to(bx + 8, by + 4)
            PangoCairo.show_layout(cr, label)

    @staticmethod
    def _smooth_path(cr, points: list[tuple[float, float]]) -> None:
        if not points:
            return
        cr.move_to(points[0][0], points[0][1])
        if len(points) == 1:
            return
        for index in range(len(points) - 1):
            p0 = points[index - 1] if index > 0 else points[index]
            p1 = points[index]
            p2 = points[index + 1]
            p3 = points[index + 2] if index + 2 < len(points) else p2
            c1x = p1[0] + (p2[0] - p0[0]) / 6.0
            c1y = p1[1] + (p2[1] - p0[1]) / 6.0
            c2x = p2[0] - (p3[0] - p1[0]) / 6.0
            c2y = p2[1] - (p3[1] - p1[1]) / 6.0
            cr.curve_to(c1x, c1y, c2x, c2y, p2[0], p2[1])


def rounded_rectangle(cr, x: float, y: float, width: float, height: float, radius: float) -> None:
    radius = min(radius, width / 2.0, height / 2.0)
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()
