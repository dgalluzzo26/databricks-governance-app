"""Helpers that map data-access settings onto the Databricks Job schedule."""

from __future__ import annotations

# Quartz */N for minutes only works cleanly when N divides 60.
_MINUTE_STEPS = (5, 6, 10, 12, 15, 20, 30)


def snap_interval_minutes(minutes: int) -> int:
    """Snap a requested interval to a cron-friendly value.

    Args:
        minutes: Requested interval (already validated >= 5 by the settings model).

    Returns:
        Minutes actually used for the Job cron (a divisor of 60, or a multiple of 60).
    """
    value = max(1, int(minutes))
    if value >= 60:
        return max(60, (value // 60) * 60)
    for step in reversed(_MINUTE_STEPS):
        if step <= value:
            return step
    return 5


def quartz_cron_for_interval_minutes(minutes: int) -> str:
    """Build a UTC quartz cron expression for the snapped interval.

    Args:
        minutes: Requested interval in minutes.

    Returns:
        Quartz cron string (6 fields, with seconds).
    """
    snapped = snap_interval_minutes(minutes)
    if snapped >= 60:
        hours = snapped // 60
        if hours <= 1:
            return "0 0 * * * ?"
        return f"0 0 */{hours} * * ?"
    return f"0 */{snapped} * * * ?"
