"""Linux memory accounting, read directly from /proc.

No simulated or estimated fallback values are used.  If a kernel interface is
unavailable the field is reported as ``None`` (or a documented conservative
fallback for MemAvailable on very old kernels).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import time

MEMINFO = Path(os.environ.get("RAMPILOT_MEMINFO", "/proc/meminfo"))
VMSTAT = Path(os.environ.get("RAMPILOT_VMSTAT", "/proc/vmstat"))
PSI_MEMORY = Path(os.environ.get("RAMPILOT_PSI", "/proc/pressure/memory"))

KIB = 1024
MIB = 1024 * 1024
GIB = 1024 * 1024 * 1024
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


class MemoryReadError(RuntimeError):
    """Raised when /proc/meminfo cannot be read or parsed."""


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    total: int
    free: int
    available: int
    buffers: int
    cached: int
    sreclaimable: int
    shmem: int
    swap_total: int
    swap_free: int
    dirty: int = 0
    writeback: int = 0
    timestamp: float = 0.0

    @property
    def used(self) -> int:
        """Memory not readily available to new applications.

        Linux's ``MemAvailable`` estimate is the most useful measure of memory
        pressure.  This is intentionally *not* the narrower "used" column from
        ``free``; reclaimable page cache is already accounted for in
        MemAvailable.  See README for the accounting explanation.
        """
        return max(0, self.total - self.available)

    @property
    def cached_total(self) -> int:
        """Page cache, buffers and reclaimable slab, as shown by ``free``."""
        return self.buffers + self.cached + self.sreclaimable

    @property
    def swap_used(self) -> int:
        return max(0, self.swap_total - self.swap_free)

    @property
    def utilization(self) -> float:
        """Fraction of RAM not available, in the range 0.0..1.0."""
        return self.used / self.total if self.total > 0 else 0.0

    @property
    def available_ratio(self) -> float:
        return self.available / self.total if self.total > 0 else 0.0

    @property
    def cache_ratio(self) -> float:
        return self.cached_total / self.total if self.total > 0 else 0.0


@dataclass(frozen=True, slots=True)
class MemoryReading:
    memory: MemorySnapshot
    swap_in_bps: int = 0
    swap_out_bps: int = 0
    psi_some_avg10: float | None = None
    psi_some_avg60: float | None = None
    psi_full_avg10: float | None = None


@dataclass(frozen=True, slots=True)
class SwapCounters:
    pswpin: int
    pswpout: int


def _parse_meminfo_text(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        parts = raw.strip().split()
        if not parts:
            continue
        try:
            amount = int(parts[0])
        except ValueError:
            continue
        # /proc/meminfo values are kB unless the key explicitly says otherwise.
        values[key.strip()] = amount * KIB
    return values


def read_meminfo(path: Path | str = MEMINFO) -> MemorySnapshot:
    """Read a MemorySnapshot from /proc/meminfo."""
    try:
        text = Path(path).read_text(encoding="ascii")
    except OSError as exc:
        raise MemoryReadError(f"Could not read {path}: {exc.strerror or exc}") from exc
    raw = _parse_meminfo_text(text)
    required = ("MemTotal", "MemFree")
    missing = [name for name in required if name not in raw]
    if missing:
        raise MemoryReadError(
            "The kernel memory interface is missing required fields: "
            + ", ".join(missing)
        )

    total = raw["MemTotal"]
    free = raw["MemFree"]
    buffers = raw.get("Buffers", 0)
    cached = raw.get("Cached", 0)
    sreclaimable = raw.get("SReclaimable", 0)
    # MemAvailable was added in Linux 3.14.  A conservative fallback keeps the
    # app useful on ancient kernels without pretending to know more than we do.
    available = raw.get("MemAvailable", free + buffers + cached + sreclaimable)
    available = max(0, min(available, total))
    return MemorySnapshot(
        total=total,
        free=free,
        available=available,
        buffers=buffers,
        cached=cached,
        sreclaimable=sreclaimable,
        shmem=raw.get("Shmem", 0),
        swap_total=raw.get("SwapTotal", 0),
        swap_free=raw.get("SwapFree", 0),
        dirty=raw.get("Dirty", 0),
        writeback=raw.get("Writeback", 0),
        timestamp=time.time(),
    )


def _parse_vmstat_text(text: str) -> SwapCounters:
    pswpin = pswpout = 0
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        key = parts[0]
        if key in ("pswpin", "pswpout"):
            try:
                value = int(parts[1])
            except ValueError:
                continue
            if key == "pswpin":
                pswpin = value
            else:
                pswpout = value
    return SwapCounters(pswpin, pswpout)


def read_swap_counters(path: Path | str = VMSTAT) -> SwapCounters | None:
    try:
        return _parse_vmstat_text(Path(path).read_text(encoding="ascii"))
    except OSError:
        return None


def _parse_psi(text: str) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or " " not in line:
            continue
        kind = parts[0]
        data: dict[str, float] = {}
        for token in parts[1:]:
            if "=" not in token:
                continue
            key, raw = token.split("=", 1)
            try:
                data[key] = float(raw)
            except ValueError:
                continue
        if data:
            result[kind] = data
    return result


def read_psi(path: Path | str = PSI_MEMORY) -> dict[str, dict[str, float]]:
    """Read /proc/pressure/memory if the kernel exposes PSI."""
    try:
        return _parse_psi(Path(path).read_text(encoding="ascii"))
    except OSError:
        return {}


class MemorySampler:
    """Stateful sampler for memory stats and swap activity rates."""

    def __init__(self, meminfo_path: Path | str = MEMINFO, vmstat_path: Path | str = VMSTAT):
        self._meminfo_path = Path(meminfo_path)
        self._vmstat_path = Path(vmstat_path)
        self._last_swap: SwapCounters | None = None
        self._last_time: float | None = None

    def reset_rates(self) -> None:
        """Forget the previous counters, e.g. after resuming from pause."""
        self._last_swap = None
        self._last_time = None

    def sample(self) -> MemoryReading:
        memory = read_meminfo(self._meminfo_path)
        now = time.monotonic()
        counters = read_swap_counters(self._vmstat_path)
        swap_in_bps = swap_out_bps = 0
        if counters is not None and self._last_swap is not None and self._last_time is not None:
            elapsed = now - self._last_time
            if elapsed >= 0.05:
                d_in = counters.pswpin - self._last_swap.pswpin
                d_out = counters.pswpout - self._last_swap.pswpout
                # Counters can only reset across reboot/suspend; avoid a huge spike.
                if d_in >= 0:
                    swap_in_bps = int(d_in * PAGE_SIZE / elapsed)
                if d_out >= 0:
                    swap_out_bps = int(d_out * PAGE_SIZE / elapsed)
        if counters is not None:
            self._last_swap = counters
        self._last_time = now

        psi = read_psi()
        some = psi.get("some", {})
        full = psi.get("full", {})
        return MemoryReading(
            memory=memory,
            swap_in_bps=swap_in_bps,
            swap_out_bps=swap_out_bps,
            psi_some_avg10=some.get("avg10"),
            psi_some_avg60=some.get("avg60"),
            psi_full_avg10=full.get("avg10"),
        )
