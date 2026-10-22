"""Interactive, explicitly-confirmed memory cleanup workflow."""

from __future__ import annotations

from dataclasses import dataclass
import time
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, GLib, Gtk, Pango

from .core.meminfo import MemoryReadError, read_meminfo
from .core.processes import ProcessInfo
from .core.safety import same_process_alive
from .core.terminator import SafeTerminator, TerminationResult, TerminationTarget
from .core.utils import format_bytes, format_percent
from .resources import humanize_process_name
from .widgets.common import AppIconTile


@dataclass
class _Candidate:
    info: ProcessInfo
    check: Gtk.CheckButton
    row: Gtk.ListBoxRow


class CleanupWindow(Adw.Window):
    """A modal cleanup panel.  It never signals anything without confirmation."""

    def __init__(
        self,
        parent: Gtk.Window,
        processes: tuple[ProcessInfo, ...],
        on_finished=None,
    ) -> None:
        super().__init__(
            application=parent.get_application(),
            transient_for=parent,
            modal=True,
        )
        self.set_title("Free Up RAM")
        self.set_default_size(780, 720)
        self.set_size_request(640, 540)
        self.add_css_class("rampilot-window")
        self._on_finished = on_finished
        self._closed = False
        self._busy = False
        self._timers: set[int] = set()
        self._terminator = SafeTerminator()
        self._candidates: list[_Candidate] = []
        self._selected: set[tuple[int, float]] = set()
        self._available_before: int | None = None
        self._available_at_open = 0
        self._total_memory = 0
        self._initial_available: int | None = None
        self._memory_error: str | None = None
        self._all_results: list[TerminationResult] = []
        self._signalled_targets: list[TerminationTarget] = []
        self._force_used = False
        self._survivors: list[TerminationTarget] = []
        self._stage_force = False
        self._settle_until: float | None = None
        self._wait_deadline = 0.0
        self._alert: Adw.AlertDialog | None = None
        self._stack: Gtk.Stack | None = None
        self._select_page: Gtk.Widget | None = None
        self._working_page: Gtk.Widget | None = None
        self._result_page: Gtk.Widget | None = None

        try:
            memory = read_meminfo()
            self._initial_available = memory.available
            self._available_at_open = memory.available
            self._total_memory = memory.total
        except MemoryReadError as exc:
            self._memory_error = str(exc)

        header = Adw.HeaderBar()
        header.add_css_class("rampilot-header")
        title = Adw.WindowTitle(title="Free Up RAM", subtitle="Review before closing")
        header.set_title_widget(title)
        self._header = header

        self._build_ui(processes)
        self.connect("close-request", self._on_close_request)

    # ------------------------------------------------------------------ UI
    def _build_ui(self, processes: tuple[ProcessInfo, ...]) -> None:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_margin_top(4)
        content.set_margin_bottom(4)
        self.set_content(content)
        content.append(self._header)
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(160)
        self._stack.set_vexpand(True)
        self._stack.set_hexpand(True)
        content.append(self._stack)

        self._select_page = self._build_select_page(processes)
        self._stack.add_named(self._select_page, "select")

        working = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        working.set_valign(Gtk.Align.CENTER)
        working.set_halign(Gtk.Align.CENTER)
        working.set_vexpand(True)
        spinner = Gtk.Spinner()
        spinner.set_size_request(40, 40)
        spinner.set_halign(Gtk.Align.CENTER)
        spinner.start()
        working.append(spinner)
        self._working_title = Gtk.Label(label="Asking applications to close…")
        self._working_title.add_css_class("ram-cleanup-header")
        working.append(self._working_title)
        self._working_text = Gtk.Label(
            label="Sending SIGTERM and waiting for processes to exit."
        )
        self._working_text.add_css_class("ram-dim")
        self._working_text.set_wrap(True)
        self._working_text.set_justify(Gtk.Justification.CENTER)
        self._working_text.set_max_width_chars(58)
        working.append(self._working_text)
        self._working_page = working
        self._stack.add_named(working, "working")

        self._result_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._stack.add_named(self._result_page, "result")
        self._stack.set_visible_child_name("select")

    def _build_select_page(self, processes: tuple[ProcessInfo, ...]) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=13)
        page.set_margin_start(20)
        page.set_margin_end(20)
        page.set_margin_top(14)
        page.set_margin_bottom(16)

        heading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        heading = Gtk.Label(label="Reclaim memory safely")
        heading.add_css_class("ram-cleanup-header")
        heading.set_xalign(0)
        heading_box.append(heading)
        subtitle = Gtk.Label(
            label=(
                "Choose applications to close. RAMPilot sends SIGTERM only after you confirm, "
                "then measures MemAvailable before and after."
            )
        )
        subtitle.add_css_class("ram-dim")
        subtitle.set_xalign(0)
        subtitle.set_wrap(True)
        subtitle.set_max_width_chars(96)
        heading_box.append(subtitle)
        page.append(heading_box)

        metrics = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        metrics.set_homogeneous(True)
        self._available_metric = self._make_metric(
            "AVAILABLE NOW",
            format_bytes(self._available_at_open, compact=True)
            if self._initial_available is not None
            else "Unavailable",
            "MemAvailable",
        )
        self._estimate_metric = self._make_metric(
            "SELECTED ESTIMATE", "0 B", "Upper bound (RSS sum)"
        )
        self._selected_metric = self._make_metric("SELECTED", "0 apps", "Nothing is auto-selected")
        metrics.append(self._available_metric)
        metrics.append(self._estimate_metric)
        metrics.append(self._selected_metric)
        page.append(metrics)

        notice = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        notice.add_css_class("ram-notice")
        notice_icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        notice_icon.set_pixel_size(15)
        notice_icon.set_valign(Gtk.Align.START)
        notice_icon.set_margin_top(2)
        notice.append(notice_icon)
        notice_label = Gtk.Label()
        notice_label.set_wrap(True)
        notice_label.set_xalign(0)
        notice_label.set_hexpand(True)
        notice_label.add_css_class("ram-small")
        notice_label.set_label(
            "Estimates sum each process's resident memory (RSS) and may count shared memory "
            "more than once. Linux also uses spare RAM for filesystem cache: that cache is normal, "
            "reclaimable and is not included here. RAMPilot never flushes system caches."
        )
        notice.append(notice_label)
        page.append(notice)

        if self._memory_error:
            error = Gtk.Label(label=self._memory_error)
            error.add_css_class("ram-warning-text")
            error.set_wrap(True)
            error.set_xalign(0)
            page.append(error)

        candidates_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        candidates_card.add_css_class("ram-card")
        candidates_card.set_vexpand(True)
        list_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        list_header.set_margin_top(12)
        list_header.set_margin_start(14)
        list_header.set_margin_end(14)
        list_title = Gtk.Label(label="Your applications")
        list_title.add_css_class("ram-section-title")
        list_title.set_xalign(0)
        list_title.set_hexpand(True)
        list_header.append(list_title)
        self._candidate_count = Gtk.Label(label="")
        self._candidate_count.add_css_class("ram-dim")
        self._candidate_count.add_css_class("ram-small")
        list_header.append(self._candidate_count)
        candidates_card.append(list_header)
        safety_hint = Gtk.Label(
            label="System services, other users' processes, session processes and RAMPilot are excluded."
        )
        safety_hint.add_css_class("ram-dim")
        safety_hint.add_css_class("ram-small")
        safety_hint.set_xalign(0)
        safety_hint.set_margin_start(14)
        safety_hint.set_margin_end(14)
        safety_hint.set_ellipsize(Pango.EllipsizeMode.END)
        candidates_card.append(safety_hint)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.add_css_class("ram-cleanup-list")
        listbox.set_margin_start(6)
        listbox.set_margin_end(6)
        listbox.set_margin_top(4)
        self._candidate_rows: list[_Candidate] = []
        candidates = [
            info
            for info in processes
            if info.cleanup_eligible and info.rss > 0
        ]
        candidates.sort(key=lambda info: (info.rss, info.name.casefold()), reverse=True)
        for info in candidates:
            row = self._build_candidate_row(info)
            self._candidate_rows.append(row)
            listbox.append(row.row)
        self._candidates = self._candidate_rows
        self._candidate_count.set_label(
            f"{len(candidates)} closable app{'s' if len(candidates) != 1 else ''}"
        )
        if not candidates:
            empty = Gtk.Label(
                label="No closable applications were found. "
                "System services and protected session processes are never offered."
            )
            empty.add_css_class("ram-dim")
            empty.set_wrap(True)
            empty.set_margin_top(20)
            empty.set_margin_bottom(24)
            empty.set_margin_start(20)
            empty.set_margin_end(20)
            listbox.append(empty)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_has_frame(False)
        scroller.set_vexpand(True)
        scroller.set_min_content_height(210)
        scroller.set_child(listbox)
        candidates_card.append(scroller)
        page.append(candidates_card)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        footer.set_margin_top(2)
        self._footer_summary = Gtk.Label(label="Select applications to close")
        self._footer_summary.add_css_class("ram-dim")
        self._footer_summary.add_css_class("ram-small")
        self._footer_summary.set_xalign(0)
        self._footer_summary.set_hexpand(True)
        self._footer_summary.set_ellipsize(Pango.EllipsizeMode.END)
        footer.append(self._footer_summary)
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("ram-subtle-button")
        cancel.connect("clicked", lambda _b: self.close())
        footer.append(cancel)
        self._close_button = Gtk.Button(label="Close 0 apps…")
        self._close_button.add_css_class("destructive-action")
        self._close_button.set_sensitive(False)
        self._close_button.connect("clicked", self._on_close_clicked)
        footer.append(self._close_button)
        page.append(footer)

        if self._memory_error:
            self._close_button.set_sensitive(False)
        return page

    def _make_metric(self, title: str, value: str, subtitle: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.add_css_class("ram-cleanup-metric")
        title_label = Gtk.Label(label=title)
        title_label.add_css_class("ram-cleanup-metric-title")
        title_label.set_xalign(0)
        box.append(title_label)
        value_label = Gtk.Label(label=value)
        value_label.add_css_class("ram-cleanup-metric-value")
        value_label.set_xalign(0)
        box.append(value_label)
        sub = Gtk.Label(label=subtitle)
        sub.add_css_class("ram-stat-sub")
        sub.set_xalign(0)
        box.append(sub)
        box.value_label = value_label  # type: ignore[attr-defined]
        return box

    def _build_candidate_row(self, info: ProcessInfo) -> _Candidate:
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        row.add_css_class("ram-cleanup-row")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(2)
        box.set_margin_bottom(2)
        check = Gtk.CheckButton()
        check.set_valign(Gtk.Align.CENTER)
        check.set_tooltip_text(
            "Select this process for closing. It will only be signalled after confirmation."
        )
        check.connect("toggled", self._on_selection_changed, info)
        box.append(check)
        icon = AppIconTile()
        box.append(icon)
        icon.update(info.exe, info.name, info.is_service)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        labels.set_hexpand(True)
        labels.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(label=humanize_process_name(info.name))
        name.add_css_class("ram-process-name")
        name.set_xalign(0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        labels.append(name)
        sub = Gtk.Label(
            label=f"{info.username or 'unknown'} · PID {info.pid}"
        )
        sub.add_css_class("ram-process-sub")
        sub.set_xalign(0)
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        labels.append(sub)
        box.append(labels)

        mem_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        mem_box.set_valign(Gtk.Align.CENTER)
        mem = Gtk.Label(label=format_bytes(info.rss, compact=True))
        mem.add_css_class("ram-process-mem")
        mem.set_xalign(1)
        mem_box.append(mem)
        pct = info.rss / self._total_memory * 100 if self._total_memory else 0
        pct_label = Gtk.Label(label=format_percent(pct, 1 if pct < 10 else 0))
        pct_label.add_css_class("ram-process-pct")
        pct_label.set_xalign(1)
        mem_box.append(pct_label)
        box.append(mem_box)

        row.set_child(box)
        command = info.cmdline or info.exe or ""
        row.set_tooltip_text(
            f"{humanize_process_name(info.name)}\nOwner: {info.username}   PID: {info.pid}\n"
            f"RSS: {format_bytes(info.rss)}\n{command[:300]}"
        )
        return _Candidate(info=info, check=check, row=row)

    # ------------------------------------------------------------ selection
    def _on_selection_changed(self, check: Gtk.CheckButton, info: ProcessInfo) -> None:
        key = (info.pid, info.create_time)
        if check.get_active():
            self._selected.add(key)
        else:
            self._selected.discard(key)
        selected = [
            candidate
            for candidate in self._candidates
            if (candidate.info.pid, candidate.info.create_time) in self._selected
        ]
        estimate = sum(candidate.info.rss for candidate in selected)
        count = len(selected)
        self._estimate_metric.value_label.set_label(format_bytes(estimate, compact=True))
        self._selected_metric.value_label.set_label(
            f"{count} app{'s' if count != 1 else ''}"
        )
        self._close_button.set_label(
            f"Close {count} app{'s' if count != 1 else ''}…"
        )
        self._close_button.set_sensitive(count > 0 and self._memory_error is None)
        if count:
            self._footer_summary.set_label(
                f"{count} selected · estimated upper bound {format_bytes(estimate)}"
            )
        else:
            self._footer_summary.set_label("Select applications to close")

    def _selected_candidates(self) -> list[_Candidate]:
        return [
            candidate
            for candidate in self._candidates
            if (candidate.info.pid, candidate.info.create_time) in self._selected
        ]

    def _on_close_clicked(self, _button: Gtk.Button) -> None:
        selected = self._selected_candidates()
        if not selected:
            return
        estimate = sum(candidate.info.rss for candidate in selected)
        names = [humanize_process_name(candidate.info.name) for candidate in selected]
        preview = ", ".join(names[:4])
        if len(names) > 4:
            preview += f" and {len(names) - 4} more"
        available_text = (
            format_bytes(self._initial_available) if self._initial_available is not None else "unknown"
        )
        alert = Adw.AlertDialog(
            heading=f"Close {len(selected)} application{'s' if len(selected) != 1 else ''}?",
            body=(
                f"{preview}.\n\n"
                "RAMPilot will ask these processes to exit with SIGTERM. Unsaved work may be lost.\n\n"
                f"Estimated upper bound: {format_bytes(estimate)} (shared memory may be counted twice).\n"
                f"Available memory before cleanup: {available_text}.\n\n"
                "Only your own application processes are included. Nothing is closed automatically."
            ),
        )
        alert.add_response("cancel", "Cancel")
        alert.add_response("close", "Close Applications")
        alert.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_default_response("cancel")
        alert.set_close_response("cancel")
        alert.connect("response", self._on_cleanup_confirmed, selected)
        self._alert = alert
        alert.present(self)

    def _on_cleanup_confirmed(self, _dialog, response: str, selected: list[_Candidate]) -> None:
        self._alert = None
        if response != "close":
            return
        targets = [
            TerminationTarget(
                pid=candidate.info.pid,
                name=humanize_process_name(candidate.info.name),
                create_time=candidate.info.create_time,
                rss=candidate.info.rss,
            )
            for candidate in selected
        ]
        self._begin_cleanup(targets, forced=False)

    # ------------------------------------------------------------- execution
    def _begin_cleanup(self, targets: list[TerminationTarget], *, forced: bool) -> None:
        if self._busy or self._closed:
            return
        try:
            before = read_meminfo().available
        except MemoryReadError:
            before = None
        if self._available_before is None and before is not None:
            self._available_before = before
        self._stage_force = forced
        self._busy = True
        self._force_used = self._force_used or forced
        self._set_busy_ui(True)
        self._working_title.set_label(
            "Force-closing applications…" if forced else "Asking applications to close…"
        )
        self._working_text.set_label(
            (
                "Sending SIGKILL and waiting for processes to exit. "
                "Unsaved work is being discarded."
            )
            if forced
            else (
                "Sending SIGTERM and waiting up to 5 seconds. "
                "RAMPilot will measure available memory when this finishes."
            )
        )
        if self._stack is not None:
            self._stack.set_visible_child_name("working")

        def work():
            results = [self._terminator.terminate(target, force=forced) for target in targets]
            GLib.idle_add(self._signals_finished, targets, results)
        threading.Thread(target=work, name="rampilot-cleanup", daemon=True).start()

    def _signals_finished(self, targets, results):
        if self._closed:
            return False
        self._all_results.extend(results)
        signalled = [
            target
            for target, result in zip(targets, results)
            if result.signalled
        ]
        # Targets are tracked by PID + create_time, so a recycled PID can never
        # be mistaken for the original process.
        for target in signalled:
            if target not in self._signalled_targets:
                self._signalled_targets.append(target)
        self._wait_deadline = time.monotonic() + 5.0
        self._settle_until = None
        self._survivors = []
        self._poll_id = GLib.timeout_add(300, self._poll_cleanup)
        self._timers.add(self._poll_id)
        return False

    def _poll_cleanup(self) -> bool:
        if self._closed:
            return False
        now = time.monotonic()
        alive = [
            target
            for target in self._signalled_targets
            if same_process_alive(target.pid, target.create_time)
        ]
        self._survivors = alive
        if alive:
            self._working_text.set_label(
                f"Waiting for {len(alive)} process{'es' if len(alive) != 1 else ''} to exit…"
            )
            if now >= self._wait_deadline:
                self._finalize()
                return False
            return True
        if self._settle_until is None:
            self._settle_until = now + 0.65
            self._working_text.set_label("Processes closed. Measuring available memory…")
            return True
        if now >= self._settle_until:
            self._finalize()
            return False
        return True

    def _finalize(self) -> None:
        if self._closed:
            return
        self._busy = False
        self._timers.discard(getattr(self, "_poll_id", 0))
        try:
            after = read_meminfo().available
        except MemoryReadError:
            after = None
        alive = [
            target
            for target in self._signalled_targets
            if same_process_alive(target.pid, target.create_time)
        ]
        self._survivors = alive
        self._set_busy_ui(False)
        self._build_result_page(after)
        if self._stack is not None:
            self._stack.set_visible_child_name("result")
        if self._on_finished is not None:
            try:
                self._on_finished()
            except Exception:
                pass

    def _set_busy_ui(self, busy: bool) -> None:
        for candidate in self._candidate_rows:
            candidate.check.set_sensitive(not busy)
        if self._close_button is not None:
            self._close_button.set_sensitive(not busy and bool(self._selected))
        self._busy = busy

    def _build_result_page(self, after: int | None) -> None:
        page = self._result_page
        assert page is not None
        while child := page.get_first_child():
            page.remove(child)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_has_frame(False)
        scroller.set_vexpand(True)
        scroller.set_child(self._build_result_content(after))
        page.append(scroller)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        footer.set_margin_start(20)
        footer.set_margin_end(20)
        footer.set_margin_top(8)
        footer.set_margin_bottom(16)
        footer.set_hexpand(True)
        note = Gtk.Label(
            label="Available memory can change for reasons unrelated to this cleanup."
        )
        note.add_css_class("ram-dim")
        note.add_css_class("ram-small")
        note.set_xalign(0)
        note.set_hexpand(True)
        note.set_wrap(True)
        footer.append(note)
        if self._survivors and not self._force_used:
            force = Gtk.Button(
                label=f"Force close {len(self._survivors)} remaining…"
            )
            force.add_css_class("destructive-action")
            force.connect("clicked", self._on_force_clicked)
            footer.append(force)
        done = Gtk.Button(label="Done")
        done.add_css_class("suggested-action")
        done.connect("clicked", lambda _b: self.close())
        footer.append(done)
        page.append(footer)

    def _build_result_content(self, after: int | None) -> Gtk.Widget:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=13)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(14)
        content.set_margin_bottom(12)

        signalled_alive = [
            target
            for target in self._signalled_targets
            if same_process_alive(target.pid, target.create_time)
        ]
        closed_count = len(self._signalled_targets) - len(signalled_alive)
        failures = [
            result
            for result in self._all_results
            if result.outcome in ("refused", "error")
        ]
        already_gone = [
            result for result in self._all_results if result.outcome == "exited"
        ]
        heading_text = "Cleanup finished" if closed_count else "No applications were closed"
        heading = Gtk.Label(label=heading_text)
        heading.add_css_class("ram-cleanup-header")
        heading.set_xalign(0)
        content.append(heading)
        summary = Gtk.Label(
            label=(
                f"{closed_count} process{'es' if closed_count != 1 else ''} stopped"
                + (
                    f" · {len(signalled_alive)} still running"
                    if signalled_alive
                    else ""
                )
                + (
                    f" · {len(failures)} could not be closed"
                    if failures
                    else ""
                )
            )
        )
        summary.add_css_class("ram-dim")
        summary.set_xalign(0)
        content.append(summary)

        result_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        result_box.add_css_class("ram-result-box")
        if self._available_before is None or after is None:
            delta_text = "Unavailable"
            delta_class = "neutral"
            message = (
                "Available memory could not be measured from /proc/meminfo, so RAMPilot "
                "cannot report how much memory changed."
            )
        else:
            delta = after - self._available_before
            if delta > 0:
                delta_text = f"+{format_bytes(delta)}"
            elif delta < 0:
                delta_text = f"−{format_bytes(-delta)}"
            else:
                delta_text = "0 B"
            if delta > 16 * 1024 * 1024:
                delta_class = "positive"
                message = (
                    "Available memory increased by a measured "
                    f"{format_bytes(delta)}. Some of this may be reclaimed cache or memory "
                    "that was already becoming available, so the process RSS estimate is not "
                    "a guarantee of recovered memory."
                )
            elif delta < -16 * 1024 * 1024:
                delta_class = "negative"
                message = (
                    f"Available memory decreased by {format_bytes(-delta)}. Other activity on "
                    "the system outweighed any memory released by the closed apps. RAMPilot "
                    "does not claim memory was freed without a measured increase."
                )
            else:
                delta_class = "neutral"
                message = (
                    "Available memory did not change measurably (within 16 MiB). Closed apps' "
                    "memory may have been reused, or the kernel may not have updated its estimate "
                    "yet. RAMPilot does not claim memory was freed without a measured increase."
                )
        delta_label = Gtk.Label(label=delta_text)
        delta_label.add_css_class("ram-result-delta")
        delta_label.add_css_class(delta_class)
        delta_label.set_xalign(0)
        result_box.append(delta_label)
        change_caption = Gtk.Label(label="Measured change in MemAvailable")
        change_caption.add_css_class("ram-stat-sub")
        change_caption.set_xalign(0)
        result_box.append(change_caption)
        message_label = Gtk.Label(label=message)
        message_label.set_wrap(True)
        message_label.set_xalign(0)
        message_label.add_css_class("ram-small")
        message_label.add_css_class("ram-dim")
        result_box.append(message_label)
        content.append(result_box)

        metrics = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        metrics.set_homogeneous(True)
        before_value = (
            format_bytes(self._available_before, compact=True)
            if self._available_before is not None
            else "Unavailable"
        )
        after_value = format_bytes(after, compact=True) if after is not None else "Unavailable"
        metrics.append(self._make_metric("BEFORE CLEANUP", before_value, "MemAvailable"))
        metrics.append(self._make_metric("AFTER CLEANUP", after_value, "MemAvailable"))
        metrics.append(
            self._make_metric(
                "STOPPED",
                f"{closed_count} process{'es' if closed_count != 1 else ''}",
                "Actual process state",
            )
        )
        content.append(metrics)

        if signalled_alive:
            warning = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            warning.add_css_class("ram-result-box")
            warning.add_css_class("warn")
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            icon.set_pixel_size(16)
            icon.set_valign(Gtk.Align.START)
            warning.append(icon)
            warning_text = Gtk.Label()
            warning_text.set_wrap(True)
            warning_text.set_xalign(0)
            warning_text.set_hexpand(True)
            warning_text.set_label(
                f"{len(signalled_alive)} process{'es' if len(signalled_alive) != 1 else ''} "
                "ignored SIGTERM or is still shutting down. You can wait, or use the separate "
                "force-close action if an app is unresponsive. Forced termination can lose unsaved data."
            )
            warning_text.add_css_class("ram-small")
            warning.append(warning_text)
            content.append(warning)

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        details_title = Gtk.Label(label="Details")
        details_title.add_css_class("ram-section-title")
        details_title.set_xalign(0)
        details.set_margin_top(2)
        details.append(details_title)
        shown = 0
        if self._signalled_targets:
            for target in self._signalled_targets:
                if shown >= 24:
                    break
                alive = same_process_alive(target.pid, target.create_time)
                state = "Still running" if alive else "Closed"
                row = self._result_row(target.name, f"PID {target.pid} · {state}", not alive)
                details.append(row)
                shown += 1
        for result in failures:
            if shown >= 24:
                break
            details.append(
                self._result_row(result.name, f"PID {result.pid} · {result.message}", False)
            )
            shown += 1
        for result in already_gone:
            if shown >= 24:
                break
            details.append(
                self._result_row(result.name, f"PID {result.pid} · already exited", True)
            )
            shown += 1
        total_rows = len(self._signalled_targets) + len(failures) + len(already_gone)
        if total_rows > shown:
            more = Gtk.Label(label=f"+ {total_rows - shown} more")
            more.add_css_class("ram-dim")
            more.add_css_class("ram-small")
            more.set_xalign(0)
            details.append(more)
        content.append(details)

        caveat = Gtk.Label(
            label=(
                "Linux uses unused RAM for cache to speed up the system. That cache is automatically "
                "reclaimed when applications need memory. RAMPilot does not clear caches, drop_caches "
                "or run privileged memory operations."
            )
        )
        caveat.add_css_class("ram-dim")
        caveat.add_css_class("ram-small")
        caveat.set_wrap(True)
        caveat.set_xalign(0)
        content.append(caveat)
        return content

    def _result_row(self, name: str, subtitle: str, success: bool) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon = Gtk.Image.new_from_icon_name(
            "emblem-ok-symbolic" if success else "dialog-warning-symbolic"
        )
        icon.set_pixel_size(13)
        icon.set_valign(Gtk.Align.CENTER)
        if success:
            icon.add_css_class("success")
        row.append(icon)
        name_label = Gtk.Label(label=name)
        name_label.add_css_class("ram-process-name")
        name_label.set_xalign(0)
        row.append(name_label)
        sub = Gtk.Label(label=subtitle)
        sub.add_css_class("ram-process-sub")
        sub.set_xalign(0)
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        sub.set_hexpand(True)
        row.append(sub)
        return row

    # ---------------------------------------------------------------- force
    def _on_force_clicked(self, _button: Gtk.Button) -> None:
        alive = [
            target
            for target in self._signalled_targets
            if same_process_alive(target.pid, target.create_time)
        ]
        if not alive:
            self._survivors = []
            try:
                after = read_meminfo().available
            except MemoryReadError:
                after = None
            self._build_result_page(after)
            if self._stack is not None:
                self._stack.set_visible_child_name("result")
            return
        alert = Adw.AlertDialog(
            heading=f"Force close {len(alive)} process{'es' if len(alive) != 1 else ''}?",
            body=(
                "This is a separate action. SIGKILL cannot be caught or ignored, so these "
                "applications will stop immediately and any unsaved work will be lost. "
                "Only continue if an application is unresponsive.\n\n"
                + ", ".join(target.name for target in alive[:6])
                + ("…" if len(alive) > 6 else "")
            ),
        )
        warning = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        checkbox = Gtk.CheckButton(
            label="I understand that unsaved work in these applications will be lost"
        )
        checkbox.set_active(False)
        warning.append(checkbox)
        alert.set_extra_child(warning)
        alert.add_response("cancel", "Cancel")
        alert.add_response("force", "Force Close")
        alert.set_response_appearance("force", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_default_response("cancel")
        alert.set_close_response("cancel")
        alert.set_response_enabled("force", False)
        checkbox.connect(
            "toggled",
            lambda button: alert.set_response_enabled("force", button.get_active()),
        )
        alert.connect("response", self._on_force_confirmed, alive)
        self._alert = alert
        alert.present(self)

    def _on_force_confirmed(self, _dialog, response: str, alive: list[TerminationTarget]) -> None:
        self._alert = None
        if response != "force":
            return
        # Revalidate one final time; a process may have exited while the warning
        # dialog was open.
        targets = [
            target
            for target in alive
            if same_process_alive(target.pid, target.create_time)
        ]
        if not targets:
            self._finalize()
            return
        self._begin_cleanup(targets, forced=True)

    # -------------------------------------------------------------- lifecycle
    def _on_close_request(self, _window) -> bool:
        if self._busy:
            return True
        self._closed = True
        for timer in list(self._timers):
            try:
                GLib.source_remove(timer)
            except GLib.Error:
                pass
        self._timers.clear()
        if self._on_finished is not None:
            try:
                self._on_finished()
            except Exception:
                pass
        return False
