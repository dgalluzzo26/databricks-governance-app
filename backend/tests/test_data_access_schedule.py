from __future__ import annotations

from backend.app.services.data_access_schedule import quartz_cron_for_interval_minutes, snap_interval_minutes


def test_snap_interval_minutes_to_cron_friendly_steps() -> None:
    assert snap_interval_minutes(5) == 5
    assert snap_interval_minutes(7) == 6
    assert snap_interval_minutes(14) == 12
    assert snap_interval_minutes(15) == 15
    assert snap_interval_minutes(45) == 30
    assert snap_interval_minutes(60) == 60
    assert snap_interval_minutes(90) == 60
    assert snap_interval_minutes(120) == 120


def test_quartz_cron_for_interval_minutes() -> None:
    assert quartz_cron_for_interval_minutes(5) == "0 */5 * * * ?"
    assert quartz_cron_for_interval_minutes(15) == "0 */15 * * * ?"
    assert quartz_cron_for_interval_minutes(60) == "0 0 * * * ?"
    assert quartz_cron_for_interval_minutes(120) == "0 0 */2 * * ?"
