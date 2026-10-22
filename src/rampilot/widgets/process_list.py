"""Virtualised, searchable process list."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gio, GObject, Gtk, Pango

from ..core.processes import ProcessInfo
from ..core.utils import format_bytes, format_percent
from ..resources import humanize_process_name
from .common import AppIconTile


class ProcessItem(GObject.Object):
    __gtype_name__ = "RampilotProcessItem"
    __gsignals__ = {"updated": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, info: ProcessInfo) -> None:
        super().__init__()
        self.info = info


class ProcessRow(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.add_css_class("ram-process-row")
        self.set_valign(Gtk.Align.CENTER)
        self._icon = AppIconTile()
        self.append(self._icon)

        name_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        name_box.set_hexpand(True)
        name_box.set_valign(Gtk.Align.CENTER)
        name_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._name = Gtk.Label()
        self._name.set_xalign(0)
        self._name.set_ellipsize(Pango.EllipsizeMode.END)
        self._name.add_css_class("ram-process-name")
        name_line.append(self._name)
        self._badge = Gtk.Label(label="App")
        self._badge.add_css_class("ram-badge")
        self._badge.set_valign(Gtk.Align.CENTER)
        name_line.append(self._badge)
        name_box.append(name_line)
        self._subtitle = Gtk.Label()
        self._subtitle.set_xalign(0)
        self._subtitle.set_ellipsize(Pango.EllipsizeMode.END)
        self._subtitle.add_css_class("ram-process-sub")
        name_box.append(self._subtitle)
        self.append(name_box)

        self._memory = Gtk.Label()
        self._memory.set_xalign(1)
        self._memory.set_size_request(74, -1)
        self._memory.add_css_class("ram-process-mem")
        self._memory.set_valign(Gtk.Align.CENTER)
        self.append(self._memory)

        self._ram_pct = Gtk.Label()
        self._ram_pct.set_xalign(1)
        self._ram_pct.set_size_request(46, -1)
        self._ram_pct.add_css_class("ram-process-pct")
        self._ram_pct.set_valign(Gtk.Align.CENTER)
        self.append(self._ram_pct)

        cpu_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        cpu_box.set_size_request(52, -1)
        cpu_box.set_halign(Gtk.Align.END)
        cpu_box.set_valign(Gtk.Align.CENTER)
        self._cpu = Gtk.Label()
        self._cpu.add_css_class("ram-cpu-pill")
        self._cpu.set_xalign(0.5)
        cpu_box.append(self._cpu)
        self.append(cpu_box)

        self._signature = None

    def update(self, info: ProcessInfo, total_memory: int) -> None:
        signature = (info.pid, info.create_time, info.name, info.rss, round(info.cpu_percent, 1), total_memory)
        if signature == self._signature:
            return
        self._signature = signature
        display_name = humanize_process_name(info.name)
        self._name.set_label(display_name)
        owner = info.username or f"uid {info.uid}"
        self._subtitle.set_label(f"{owner} · PID {info.pid}")
        self._badge.set_label("System" if info.is_service else "App")
        for css in ("service",):
            self._badge.remove_css_class(css)
        if info.is_service:
            self._badge.add_css_class("service")
        self._icon.update(info.exe, info.name, info.is_service)

        if info.accessible:
            self._memory.set_label(format_bytes(info.rss, compact=True))
            pct = info.rss / total_memory * 100 if total_memory > 0 else 0.0
            self._ram_pct.set_label(format_percent(pct, 1 if pct < 10 else 0))
        else:
            self._memory.set_label("—")
            self._ram_pct.set_label("—")
        if info.accessible:
            cpu = info.cpu_percent
            if 0 < cpu < 1:
                self._cpu.set_label("<1%")
            else:
                self._cpu.set_label(f"{cpu:.0f}%")
            self._cpu.remove_css_class("busy")
            if cpu >= 25:
                self._cpu.add_css_class("busy")
        else:
            self._cpu.set_label("—")

        name_text = display_name
        type_text = "System service" if info.is_service else "Application"
        command = info.cmdline or info.exe or ""
        tooltip = (
            f"{name_text}  ·  {type_text}\n"
            f"Owner: {owner}   PID: {info.pid}\n"
            f"Memory (RSS): {format_bytes(info.rss, precision=1)}"
        )
        if info.accessible:
            pct = info.rss / total_memory * 100 if total_memory > 0 else 0.0
            tooltip += f"  ·  {pct:.1f}% of RAM\nCPU: {info.cpu_percent:.1f}% (of one core)"
        if command:
            tooltip += f"\n{command[:240]}"
        self.set_tooltip_text(tooltip)


class ProcessPanel(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("ram-card")
        self.add_css_class("ram-process-card")
        self.set_vexpand(True)
        self._total_memory = 0
        self._query = ""
        self._sort_mode = 0
        self._processes: tuple[ProcessInfo, ...] = ()

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_margin_top(15)
        header.set_margin_start(16)
        header.set_margin_end(16)
        header.set_margin_bottom(8)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title_box.set_hexpand(True)
        title_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Top memory users")
        title.add_css_class("ram-section-title")
        title.set_xalign(0)
        title_line.append(title)
        self._count = Gtk.Label(label="0 processes")
        self._count.add_css_class("ram-badge")
        self._count.set_valign(Gtk.Align.CENTER)
        title_line.append(self._count)
        title_box.append(title_line)
        subtitle = Gtk.Label(label="Applications and system services, refreshed live")
        subtitle.add_css_class("ram-dim")
        subtitle.add_css_class("ram-small")
        subtitle.set_xalign(0)
        title_box.append(subtitle)
        header.append(title_box)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Search processes")
        self._search.set_size_request(160, -1)
        self._search.add_css_class("ram-search")
        self._search.set_tooltip_text(
            "Search by process name, command, owner or PID. System services are included."
        )
        self._search.connect("search-changed", self._on_search_changed)
        header.append(self._search)

        sort_options = Gtk.StringList.new(
            ["Memory", "CPU", "Name", "PID"]
        )
        self._sort = Gtk.DropDown(model=sort_options)
        self._sort.add_css_class("ram-sort")
        self._sort.set_size_request(118, -1)
        self._sort.set_tooltip_text("Sort the process list")
        self._sort.connect("notify::selected", self._on_sort_changed)
        header.append(self._sort)
        self.append(header)

        column_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        column_header.set_margin_start(58)
        column_header.set_margin_end(26)
        column_header.set_margin_bottom(2)
        process_label = Gtk.Label(label="PROCESS")
        process_label.add_css_class("ram-eyebrow")
        process_label.set_xalign(0)
        process_label.set_hexpand(True)
        column_header.append(process_label)
        for text, width in (("MEMORY", 74), ("RAM", 46), ("CPU", 52)):
            label = Gtk.Label(label=text)
            label.add_css_class("ram-eyebrow")
            label.set_xalign(1)
            label.set_size_request(width, -1)
            column_header.append(label)
        self.append(column_header)

        self._store = Gio.ListStore(item_type=ProcessItem)
        self._filter = Gtk.CustomFilter.new(self._matches)
        self._filter_model = Gtk.FilterListModel(model=self._store, filter=self._filter)
        self._filter_model.connect("items-changed", self._on_visible_changed)
        self._sorter = Gtk.CustomSorter.new(self._compare)
        self._sort_model = Gtk.SortListModel(model=self._filter_model, sorter=self._sorter)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        factory.connect("unbind", self._unbind_row)
        self._list = Gtk.ListView(model=Gtk.NoSelection(model=self._sort_model), factory=factory)
        self._list.add_css_class("ram-process-list")
        self._list.set_show_separators(False)
        self._list.set_single_click_activate(False)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_has_frame(False)
        scroller.set_vexpand(True)
        scroller.set_min_content_height(250)
        scroller.set_child(self._list)

        self._empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._empty.set_halign(Gtk.Align.CENTER)
        self._empty.set_valign(Gtk.Align.CENTER)
        self._empty.set_can_target(False)
        empty_icon = Gtk.Image.new_from_icon_name("system-search-symbolic")
        empty_icon.set_pixel_size(24)
        empty_icon.add_css_class("dim-label")
        self._empty.append(empty_icon)
        self._empty_title = Gtk.Label(label="Waiting for process data…")
        self._empty_title.add_css_class("ram-empty-title")
        self._empty.append(self._empty_title)
        self._empty_text = Gtk.Label(label="The first scan takes a moment.")
        self._empty_text.add_css_class("ram-empty-text")
        self._empty.append(self._empty_text)

        overlay = Gtk.Overlay()
        overlay.set_child(scroller)
        overlay.add_overlay(self._empty)
        overlay.set_vexpand(True)
        overlay.set_margin_bottom(8)
        self.append(overlay)

        footer = Gtk.Label(
            label="Only processes owned by you can be closed. Shared memory may be counted more than once."
        )
        footer.add_css_class("ram-dim")
        footer.add_css_class("ram-small")
        footer.set_margin_start(16)
        footer.set_margin_end(16)
        footer.set_margin_top(4)
        footer.set_margin_bottom(8)
        footer.set_xalign(0)
        footer.set_ellipsize(Pango.EllipsizeMode.END)
        self.append(footer)

    def _setup_row(self, _factory, list_item: Gtk.ListItem) -> None:
        list_item.set_child(ProcessRow())

    def _bind_row(self, _factory, list_item: Gtk.ListItem) -> None:
        item = list_item.get_item()
        row = list_item.get_child()
        if item is not None and row is not None:
            row.update(item.info, self._total_memory)
            list_item._update_handler = item.connect("updated", lambda obj: row.update(obj.info, self._total_memory))

    def _unbind_row(self, _factory, list_item):
        item = list_item.get_item()
        handler = getattr(list_item, "_update_handler", None)
        if item is not None and handler:
            item.disconnect(handler)
        list_item._update_handler = None

    def _matches(self, item: ProcessItem) -> bool:
        info = item.info
        if not self._query:
            return True
        query = self._query
        haystack = " ".join(
            (
                info.name,
                info.cmdline,
                info.exe or "",
                info.username,
                str(info.pid),
                "system service" if info.is_service else "application",
            )
        ).lower()
        return all(part in haystack for part in query.split())

    def _compare(self, item_a: ProcessItem, item_b: ProcessItem, _data=None) -> int:
        a, b = item_a.info, item_b.info
        if self._sort_mode == 1:
            if a.cpu_percent != b.cpu_percent:
                return -1 if a.cpu_percent > b.cpu_percent else 1
        elif self._sort_mode == 2:
            an, bn = a.name.casefold(), b.name.casefold()
            if an != bn:
                return -1 if an < bn else 1
        elif self._sort_mode == 3:
            if a.pid != b.pid:
                return -1 if a.pid < b.pid else 1
        if a.rss != b.rss:
            return -1 if a.rss > b.rss else 1
        if a.pid != b.pid:
            return -1 if a.pid < b.pid else 1
        return 0

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text().strip().lower()
        if query == self._query:
            return
        self._query = query
        self._filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_empty_state()

    def _on_sort_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        selected = dropdown.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION:
            return
        if selected == self._sort_mode:
            return
        self._sort_mode = selected
        self._sorter.changed(Gtk.SorterChange.DIFFERENT)

    def _on_visible_changed(self, *_args) -> None:
        self._update_empty_state()

    def _update_empty_state(self) -> None:
        try:
            count = self._filter_model.get_n_items()
        except AttributeError:
            count = 0
        if count == 0:
            if self._query:
                self._empty_title.set_label("No matching processes")
                self._empty_text.set_label("Try a different name, owner or PID.")
            elif self._processes:
                self._empty_title.set_label("No processes to show")
                self._empty_text.set_label("All processes have been filtered out.")
            self._empty.set_visible(True)
        else:
            self._empty.set_visible(False)

    def set_processes(self, processes: tuple[ProcessInfo, ...], total_memory: int) -> None:
        self._processes = processes
        self._total_memory = max(0, int(total_memory))
        old = list(self._store)
        # Keep the source order stable so only real arrivals/exits replace rows.
        wanted = {(p.pid, p.create_time): p for p in processes if not p.is_kernel and p.rss > 0}
        removed = []
        for index, item in enumerate(old):
            key = (item.info.pid, item.info.create_time)
            info = wanted.pop(key, None)
            if info is not None:
                item.info = info
                item.emit("updated")
            else:
                removed.append(index)
        for index in reversed(removed):
            self._store.remove(index)
        if wanted:
            self._store.splice(self._store.get_n_items(), 0, [ProcessItem(info) for info in wanted.values()])
        count = self._store.get_n_items()
        self._count.set_label(
            f"{count} process{'es' if count != 1 else ''}"
        )
        if self._query:
            self._filter.changed(Gtk.FilterChange.DIFFERENT)
        self._sorter.changed(Gtk.SorterChange.DIFFERENT)
        self._update_empty_state()

    def set_loading(self) -> None:
        self._empty_title.set_label("Scanning processes…")
        self._empty_text.set_label("The first scan takes a moment.")
        self._empty.set_visible(True)

    def clear_search(self) -> None:
        self._search.set_text("")
