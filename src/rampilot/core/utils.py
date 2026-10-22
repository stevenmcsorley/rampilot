"""Formatting helpers shared by the UI and tests."""

from __future__ import annotations


def format_bytes(value: int | float, *, compact: bool = False, precision: int = 1) -> str:
    """Format a byte count using binary units (the convention shown by free -h)."""
    value = max(0.0, float(value))
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    unit = units[0]
    for candidate in units:
        unit = candidate
        if value < 1024 or candidate == units[-1]:
            break
        value /= 1024.0
    if unit == "B":
        return f"{int(round(value))} B"
    if compact:
        if value >= 100:
            return f"{value:.0f} {unit}"
        if value >= 10:
            return f"{value:.1f} {unit}"
        return f"{value:.2f} {unit}"
    return f"{value:.{precision}f} {unit}"


def format_rate(bps: int | float) -> str:
    value = max(0.0, float(bps))
    if value < 1024:
        return f"{value:.0f} B/s"
    if value < 1024 * 1024:
        return f"{value / 1024:.0f} KiB/s"
    if value < 1024 * 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MiB/s"
    return f"{value / (1024 * 1024 * 1024):.2f} GiB/s"


def format_percent(value: float, decimals: int = 0) -> str:
    return f"{value:.{decimals}f}%"


def shorten(text: str, length: int) -> str:
    clean = " ".join(str(text).replace("\x00", "").split())
    if len(clean) <= length:
        return clean
    return clean[: max(0, length - 1)].rstrip() + "…"
