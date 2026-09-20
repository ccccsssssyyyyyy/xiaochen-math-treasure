"""错题重做排程 / 掌握度计算的单元测试。"""

import datetime as dt

import pytest

from mathbank import redo_schedule as rs


def test_next_weekend_is_strictly_after_and_lands_on_saturday():
    # 2026-09-20 是周日
    sunday = dt.date(2026, 9, 20)
    assert sunday.weekday() == 6
    assert rs.next_weekend(sunday) == dt.date(2026, 9, 26)  # 下周六
    # 当天是周六时必须跳到下一个周六，而不是返回当天
    saturday = dt.date(2026, 9, 26)
    assert saturday.weekday() == 5
    assert rs.next_weekend(saturday) == dt.date(2026, 10, 3)


def test_snap_to_weekend_keeps_saturday_and_rolls_forward():
    assert rs.snap_to_weekend(dt.date(2026, 9, 26)) == dt.date(2026, 9, 26)
    assert rs.snap_to_weekend(dt.date(2026, 9, 27)) == dt.date(2026, 10, 3)  # 周日
    assert rs.snap_to_weekend(dt.date(2026, 9, 21)) == dt.date(2026, 9, 26)  # 周一


def test_due_date_always_lands_on_saturday_for_all_weekdays():
    base = dt.date(2026, 9, 14)  # 周一
    for delta in range(7):
        day = base + dt.timedelta(days=delta)
        assert rs.next_weekend(day).weekday() == 5
        assert rs.next_weekend(day) > day


def test_initial_schedule_marks_first_mistake():
    today = dt.date(2026, 9, 20)
    out = rs.initial_schedule(today)
    assert out["wrong_count"] == 1
    assert out["redo_correct_streak"] == 0
    assert out["mastery_status"] == rs.PENDING
    assert out["next_redo_due"] == dt.date(2026, 9, 26)
    assert out["next_redo_due"].weekday() == 5


def test_wrong_attempt_increments_wrong_count_and_resets_streak():
    out = rs.apply_attempt(
        result=rs.RESULT_INCORRECT,
        today=dt.date(2026, 9, 20),
        prev_wrong_count=3,
        prev_streak=1,
    )
    assert out["wrong_count"] == 4
    assert out["redo_correct_streak"] == 0
    assert out["mastery_status"] == rs.PENDING
    assert out["next_redo_due"] == dt.date(2026, 9, 26)


def test_first_correct_cross_day_becomes_redone_with_two_week_due():
    out = rs.apply_attempt(
        result=rs.RESULT_CORRECT,
        today=dt.date(2026, 9, 20),
        prev_wrong_count=2,
        prev_streak=0,
        prev_last_redo_date=None,
    )
    assert out["wrong_count"] == 2  # 判对不加错次
    assert out["redo_correct_streak"] == 1
    assert out["mastery_status"] == rs.REDONE
    # 今天 + 14 天 = 2026-10-04（周日）→ 顺延到周六 10-10
    assert out["next_redo_due"] == dt.date(2026, 10, 10)
    assert out["next_redo_due"].weekday() == 5


def test_second_correct_on_another_day_masters_and_clears_due():
    out = rs.apply_attempt(
        result=rs.RESULT_CORRECT,
        today=dt.date(2026, 10, 10),
        prev_wrong_count=2,
        prev_streak=1,
        prev_last_redo_date=dt.date(2026, 9, 20),  # 上一次是不同的一天
    )
    assert out["redo_correct_streak"] == 2
    assert out["mastery_status"] == rs.MASTERED
    assert out["next_redo_due"] is None


def test_same_day_double_correct_does_not_master():
    today = dt.date(2026, 9, 20)
    first = rs.apply_attempt(
        result=rs.RESULT_CORRECT,
        today=today,
        prev_wrong_count=1,
        prev_streak=0,
        prev_last_redo_date=None,
    )
    assert first["redo_correct_streak"] == 1
    assert first["mastery_status"] == rs.REDONE

    second = rs.apply_attempt(
        result=rs.RESULT_CORRECT,
        today=today,  # 同一天
        prev_wrong_count=first["wrong_count"],
        prev_streak=first["redo_correct_streak"],
        prev_last_redo_date=today,
    )
    assert second["redo_correct_streak"] == 1  # 不推进
    assert second["mastery_status"] == rs.REDONE
    assert second["next_redo_due"] is not None


def test_rejects_illegal_result():
    with pytest.raises(ValueError):
        rs.apply_attempt(result="maybe", today=dt.date(2026, 9, 20))


def test_interval_days_is_capped():
    assert rs.interval_days(0) == 14
    assert rs.interval_days(1) == 14
    assert rs.interval_days(3) == 30  # 14*4=56 → 封顶 30


# ---------------------------------------------------------------- 人工标注


def test_is_override_active_only_accepts_mastered():
    """脏值一律当「无标注」——否则库里一个手滑写的字符串就把掌握度钉死了。"""

    assert rs.is_override_active(rs.MASTERED) is True
    assert rs.is_override_active("MASTERED") is True  # 大小写不敏感
    assert rs.is_override_active("  mastered  ") is True
    for dirty in ("", None, "pending", "redone", "masterd", "1", "true", []):
        assert rs.is_override_active(dirty) is False, dirty


def test_apply_mastery_override_only_touches_mastery_and_due():
    """人工标注不改写「错过几次」这个已经发生的事实。"""

    result = rs.apply_mastery_override(prev_wrong_count=4, prev_streak=1)
    assert set(result) == {"wrong_count", "redo_correct_streak", "mastery_status", "next_redo_due"}
    assert result["mastery_status"] == rs.MASTERED
    assert result["next_redo_due"] is None, "已掌握的题不再安排重做"
    assert result["wrong_count"] == 4
    assert result["redo_correct_streak"] == 1


def test_apply_mastery_override_tolerates_missing_prev_values():
    result = rs.apply_mastery_override()
    assert result["wrong_count"] == 0
    assert result["redo_correct_streak"] == 0
    assert result["mastery_status"] == rs.MASTERED
