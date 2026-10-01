"""Unit conventions. Data rates are shown in megabits per second (Mb/s) throughout the app."""

BITS_PER_BYTE = 8
BITS_PER_MEGABIT = 1_000_000


def bytes_per_second_to_mbps(bytes_per_second: float) -> float:
    """Mb/s = bytes/s x 8 / 1,000,000 (decimal megabits, as used for network links)."""
    return bytes_per_second * BITS_PER_BYTE / BITS_PER_MEGABIT


def format_mbps(mbps: float) -> str:
    return f"{mbps:,.1f} Mb/s"
