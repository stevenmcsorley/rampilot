"""Native desktop shell and bounded, asynchronous monitoring loop."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import signal
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from .config import ConfigStore
from .core.history import HistoryBuffer
from .core.meminfo import MemorySampler
from .core.processes import ProcessCollector
from .core.utils import format_bytes, format_rate
from .resources import install_resources, APP_ICON_NAME
from .widgets.common import StatTile, StatusPill, build_brand_header
from .widgets.gauge import MemoryGauge
from .widgets.graph import HistoryGraph
from .widgets.process_list import ProcessPanel


class Dashboard(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="RAMPilot", default_width=1040, default_height=860)
        self.add_css_class("rampilot-window")
        self.set_icon_name(APP_ICON_NAME)
        self.store = ConfigStore()
        self.config = self.store.load()
        self.history = HistoryBuffer()
        self.sampler = MemorySampler()
        self.collector = ProcessCollector()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rampilot-monitor")
        self.pending = False
        self.paused = False
        self.stopped = False
        self.processes = ()
        self.last_scan = 0.0
        self.last_sample = 0.0
        self.last_warning = 0.0
        self.level = "healthy"
        self.cleanup = None
        self.compact = False
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root)
        header = Adw.HeaderBar()
        header.add_css_class("rampilot-header")
        header.set_title_widget(build_brand_header())
        self.status = StatusPill()
        header.pack_start(self.status)
        self.pause_button = Gtk.Button(icon_name="media-playback-pause-symbolic", tooltip_text="Pause monitoring")
        self.pause_button.connect("clicked", self.toggle_pause)
        header.pack_end(self.pause_button)
        settings = Gtk.Button(icon_name="emblem-system-symbolic", tooltip_text="Settings")
        settings.connect("clicked", self.show_settings)
        header.pack_end(settings)
        compact = Gtk.Button(icon_name="view-restore-symbolic", tooltip_text="Toggle compact mode")
        compact.connect("clicked", self.toggle_compact)
        header.pack_end(compact)
        root.append(header)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        root.append(scroll)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for side in ("top", "bottom", "start", "end"):
            getattr(body, f"set_margin_{side}")(20)
        scroll.set_child(body)
        self.hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        self.hero.add_css_class("ram-hero-card")
        body.append(self.hero)
        self.gauge = MemoryGauge()
        self.gauge.set_tooltip_text("RAM in use = MemTotal − MemAvailable. Available includes memory Linux can reclaim from caches. This differs from total − free.")
        self.hero.append(self.gauge)
        summary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, valign=Gtk.Align.CENTER)
        summary.set_margin_end(20)
        self.hero.append(summary)
        title = Gtk.Label(label="Room to breathe", xalign=0)
        title.add_css_class("ram-cleanup-header")
        summary.append(title)
        self.summary = Gtk.Label(label="Reading Linux memory statistics…", xalign=0, wrap=True)
        self.summary.add_css_class("ram-dim")
        summary.append(self.summary)
        info = Gtk.Label(label="Linux puts spare RAM to work as filesystem cache.\nIt reclaims that memory automatically when applications need it.", xalign=0, wrap=True)
        info.add_css_class("ram-small")
        summary.append(info)
        self.clean_button = Gtk.Button(label="Free Up RAM", halign=Gtk.Align.START)
        self.clean_button.add_css_class("ram-free-button")
        self.clean_button.connect("clicked", self.open_cleanup)
        summary.append(self.clean_button)
        self.tiles = {}
        self.tile_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                     min_children_per_line=2, max_children_per_line=4,
                                     column_spacing=10, row_spacing=10)
        body.append(self.tile_flow)
        for key, title, tip in (
            ("total", "Installed RAM", "MemTotal: usable physical RAM reported by Linux; excludes hardware reservations."),
            ("used", "In use", "MemTotal minus MemAvailable, including memory that cannot be readily reclaimed."),
            ("available", "Available", "MemAvailable: kernel estimate of RAM available for new applications without swapping."),
            ("free", "Free", "MemFree: completely unused RAM. A low value alone does not indicate pressure."),
            ("cached", "Filesystem cache", "Cached plus SReclaimable. These overlap with available memory; do not add the tiles together."),
            ("buffers", "Buffers", "Buffers: kernel block-device buffers, usually reclaimable."),
            ("swap", "Swap", "Swap used and available. Swap usage alone does not mean current pressure."),
            ("activity", "Swap activity", "Rates from /proc/vmstat pswpin and pswpout. Sustained activity may indicate memory pressure."),
        ):
            tile = StatTile("drive-harddisk-symbolic", title, tip)
            self.tiles[key] = tile
            self.tile_flow.append(tile)
        graph_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        graph_card.add_css_class("ram-card")
        label = Gtk.Label(label="MEMORY HISTORY · LAST 60 SECONDS", xalign=0)
        label.add_css_class("ram-eyebrow")
        label.set_margin_start(16)
        label.set_margin_top(14)
        graph_card.append(label)
        self.graph = HistoryGraph()
        graph_card.append(self.graph)
        body.append(graph_card)
        self.panel = ProcessPanel()
        self.panel.set_size_request(-1, 280)
        body.append(self.panel)
        self.footer = Gtk.Label(label="Monitoring /proc · No root privileges required", xalign=0, wrap=True)
        self.footer.add_css_class("ram-small")
        self.footer.add_css_class("ram-dim")
        body.append(self.footer)
        self.connect("close-request", self.on_close)
        self.tray = None
        try:
            from .tray import TrayIndicator
            self.tray = TrayIndicator(self)
        except GLib.Error:
            pass
        self.timer = GLib.timeout_add(250, self.poll)
        self.poll()

    def poll(self):
        if self.stopped:
            return False
        now = time.monotonic()
        if self.paused or self.pending or now - self.last_sample < self.config.update_interval:
            return True
        self.last_sample = now
        self.pending = True
        scan = now - self.last_scan >= self.config.process_interval
        if scan:
            self.last_scan = now
        future = self.pool.submit(self.read_sample, scan)
        future.add_done_callback(lambda result: GLib.idle_add(self.deliver, result))
        return True

    def read_sample(self, scan):
        return self.sampler.sample(), self.collector.scan() if scan else None

    def deliver(self, future):
        self.pending = False
        if self.stopped or self.paused:
            return False
        try:
            reading, scan = future.result()
        except Exception as exc:
            self.footer.set_label(f"Could not refresh: {exc}")
            return False
        m = reading.memory
        pct = m.utilization * 100
        if self.tray:
            self.tray.update(pct)
        self.level = "critical" if pct >= self.config.critical_threshold else "elevated" if pct >= self.config.warning_threshold else "healthy"
        self.status.set_level(self.level, f"{pct:.1f}% in use, based on MemAvailable")
        self.gauge.set_value(m.utilization, m.used, m.total, self.level)
        self.summary.set_label(f"{format_bytes(m.available)} available of {format_bytes(m.total)}\n{pct:.1f}% of usable RAM is in use")
        for key in ("total", "used", "available", "free", "buffers"):
            self.tiles[key].set_value(format_bytes(getattr(m, key)))
        self.tiles["cached"].set_value(format_bytes(m.cached + m.sreclaimable), "Includes reclaimable slab")
        self.tiles["swap"].set_value(format_bytes(m.swap_used), f"{format_bytes(m.swap_free)} available" if m.swap_total else "No swap configured")
        self.tiles["activity"].set_value(f"↓ {format_rate(reading.swap_in_bps)}", f"↑ {format_rate(reading.swap_out_bps)} out")
        self.history.add(m.utilization)
        self.draw_graph()
        if scan is not None:
            self.processes = scan.processes
            self.panel.set_processes(self.processes, m.total)
        psi = f" · Memory stalls {reading.psi_some_avg10:.2f}%" if reading.psi_some_avg10 is not None else ""
        self.footer.set_label(f"Live · Memory every {self.config.update_interval}s · Processes every {self.config.process_interval}s{psi}")
        now = time.monotonic()
        if self.level != "healthy" and self.config.notifications and now - self.last_warning > 300:
            self.last_warning = now
            notification = Gio.Notification.new(f"RAMPilot: {self.level} memory pressure")
            notification.set_body(f"{pct:.0f}% in use; {format_bytes(m.available)} available. Review applications before closing them.")
            self.get_application().send_notification("memory-pressure", notification)
        return False

    def draw_graph(self):
        self.graph.set_data(tuple((p.time, p.value) for p in self.history.points),
                            warning=self.config.warning_threshold / 100,
                            critical=self.config.critical_threshold / 100, paused=self.paused)

    def toggle_pause(self, _button):
        self.paused = not self.paused
        self.pause_button.set_icon_name("media-playback-start-symbolic" if self.paused else "media-playback-pause-symbolic")
        self.pause_button.set_tooltip_text("Resume monitoring" if self.paused else "Pause monitoring")
        self.status.set_level("paused" if self.paused else self.level)
        self.draw_graph()
        if not self.paused:
            # Queue resets on the same worker as sampling to avoid data races.
            self.pool.submit(self.sampler.reset_rates)
            self.pool.submit(self.collector.reset_cpu)
            self.last_sample = self.last_scan = 0

    def toggle_compact(self, _button):
        self.compact = not self.compact
        self.panel.set_visible(not self.compact)
        self.tile_flow.set_visible(not self.compact)
        self.hero.set_orientation(Gtk.Orientation.VERTICAL if self.compact else Gtk.Orientation.HORIZONTAL)
        self.set_default_size(480 if self.compact else 1040, 650 if self.compact else 860)

    def open_cleanup(self, _button=None):
        from .cleanup import CleanupWindow
        if self.cleanup is not None:
            self.cleanup.present()
            return
        self.cleanup = CleanupWindow(self, self.processes, self.refresh_soon)
        self.cleanup.connect("destroy", lambda *_: setattr(self, "cleanup", None))
        self.cleanup.present()

    def refresh_soon(self):
        self.last_sample = self.last_scan = 0

    def show_settings(self, _button=None):
        window = Adw.PreferencesWindow(transient_for=self, modal=True, title="RAMPilot Settings", default_width=560, default_height=550)
        page = Adw.PreferencesPage()
        window.add(page)
        group = Adw.PreferencesGroup(title="Monitoring", description="Warnings use RAM in use: total minus MemAvailable.")
        page.add(group)
        def combo(title, values, current, key):
            row = Adw.ComboRow(title=title, model=Gtk.StringList.new([f"{v} seconds" for v in values]))
            row.set_selected(values.index(current))
            row.connect("notify::selected", lambda r, _: self.update_config(**{key: values[r.get_selected()]}))
            group.add(row)
        combo("Memory refresh", [1, 2, 5], self.config.update_interval, "update_interval")
        combo("Process refresh", [2, 3, 5], self.config.process_interval, "process_interval")
        for key, title in (("warning_threshold", "Elevated threshold (%)"), ("critical_threshold", "Critical threshold (%)")):
            row = Adw.ActionRow(title=title)
            spin = Gtk.SpinButton.new_with_range(40, 99, 1)
            spin.set_value(getattr(self.config, key))
            spin.set_valign(Gtk.Align.CENTER)
            spin.connect("value-changed", lambda s, k=key: self.update_config(**{k: s.get_value_as_int()}))
            row.add_suffix(spin)
            group.add(row)
        row = Adw.ActionRow(title="Low-memory notifications")
        switch = Gtk.Switch(active=self.config.notifications, valign=Gtk.Align.CENTER)
        switch.connect("notify::active", lambda s, _: self.update_config(notifications=s.get_active()))
        row.add_suffix(switch)
        group.add(row)
        supported = bool(self.tray and self.tray.available)
        support = Adw.PreferencesGroup(title="Desktop integration", description="Tray available: click its icon to reopen RAMPilot." if supported else "No StatusNotifier tray detected. Use your window manager’s Minimise action to monitor in the background. GNOME requires a tray extension.")
        page.add(support)
        row = Adw.ActionRow(title="Keep running when window closes", subtitle="Only enabled while a working tray is available")
        background = Gtk.Switch(active=self.config.keep_in_background, sensitive=supported, valign=Gtk.Align.CENTER)
        background.connect("notify::active", lambda s, _: self.update_config(keep_in_background=s.get_active()))
        row.add_suffix(background)
        support.add(row)
        quit_button = Gtk.Button(label="Quit RAMPilot", margin_top=12)
        quit_button.connect("clicked", lambda _: self.get_application().quit())
        support.add(quit_button)
        window.present()

    def update_config(self, **values):
        self.config = replace(self.config, **values).validated()
        if not self.store.save(self.config):
            self.footer.set_label(self.store.last_error)

    def on_close(self, _window):
        if self.config.keep_in_background and self.tray and self.tray.available:
            self.set_visible(False)
            return True
        self.stop()
        return False

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        if self.tray:
            self.tray.close()
        GLib.source_remove(self.timer)
        self.pool.shutdown(wait=False, cancel_futures=True)


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ICON_NAME, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None

    def do_activate(self):
        if self.window is None:
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            install_resources()
            self.window = Dashboard(self)
        self.window.present()

    def do_shutdown(self):
        if self.window:
            self.window.stop()
        Adw.Application.do_shutdown(self)


def main():
    app = Application()
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: (app.quit(), False)[1])
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda: (app.quit(), False)[1])
    return app.run(None)
