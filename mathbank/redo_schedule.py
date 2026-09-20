"""错题重做闭环的排程与掌握度计算。

纯函数、无 I/O、无 ORM 依赖 —— 便于单测，也便于接口层直接调用。所有日期都是
``datetime.date``，调用方负责传入「今天」（便于测试与跨日判定）。

口径（与 ``docs/错题重做闭环-实施计划-2026-09-20.md`` 一致）：

- 重做间隔自适应，但**建议重做日一律落在周六**（学生只有周末能打印带走）。
- 判错 → 连续做对清零、回到 ``pending``、建议重做日 = 最近的周六。
- 判对且**跨日** → 连对计数 +1；跨日连对 ≥ 2 → ``mastered``（不再安排重做）；
  否则 ``redone``，建议重做日 = 今天 + 14 天后再取周六。
- **同一日内重复做对不推进连对计数**（防止一天猛做两遍假装掌握）。
- 老师可以**人工标注**「已掌握」（``apply_mastery_override``）：它不是做题，而是
  按日期插进上面那条历史序列的一条事件，所以之后**更晚**的录入能重新接管状态。
  同日冲突时人工标注优先 —— 详细口径见 ``apply_mastery_override``。
"""

from __future__ import annotations

import datetime as dt

PENDING = "pending"
REDONE = "redone"
MASTERED = "mastered"
MASTERY_VALUES = (PENDING, REDONE, MASTERED)

RESULT_CORRECT = "correct"
RESULT_INCORRECT = "incorrect"
RESULT_VALUES = (RESULT_CORRECT, RESULT_INCORRECT)

_CORRECT_INTERVAL_DAYS = 14  # 连对 1 次后拉长到两周
MAX_INTERVAL_DAYS = 30
MASTER_STREAK = 2  # 跨日连对达到该值即 mastered

_SATURDAY = 5


def next_weekend(anchor: dt.date) -> dt.date:
    """返回**严格晚于** ``anchor`` 的第一个周六（weekday=5）。"""

    offset = (_SATURDAY - anchor.weekday()) % 7
    if offset == 0:
        offset = 7
    return anchor + dt.timedelta(days=offset)


def snap_to_weekend(day: dt.date) -> dt.date:
    """把 ``day`` 顺延到当天或之后的第一个周六。"""

    offset = (_SATURDAY - day.weekday()) % 7
    return day + dt.timedelta(days=offset)


def interval_days(streak: int) -> int:
    """连对 ``streak`` 次后的下次间隔天数（封顶 ``MAX_INTERVAL_DAYS``）。"""

    if streak <= 1:
        return _CORRECT_INTERVAL_DAYS
    return min(_CORRECT_INTERVAL_DAYS * (2 ** (streak - 1)), MAX_INTERVAL_DAYS)


def initial_schedule(today: dt.date) -> dict:
    """一道题**首次**成为错题时的初始排程。

    用于「错题入库 / 加入重做池」的时刻：记一次错、未掌握、建议重做日取最近的周六。
    """

    return {
        "wrong_count": 1,
        "redo_correct_streak": 0,
        "mastery_status": PENDING,
        "next_redo_due": next_weekend(today),
    }


def apply_attempt(
    *,
    result: str,
    today: dt.date,
    prev_wrong_count: int = 0,
    prev_streak: int = 0,
    prev_last_redo_date: dt.date | None = None,
) -> dict:
    """把一次重做结果应用到题目状态，返回新的状态字段。

    参数：
        result: ``correct`` / ``incorrect``。
        today: 本次重做的日期（用于跨日判定与顺延）。
        prev_wrong_count: 本次之前的累计做错次数。
        prev_streak: 本次之前的连续做对次数。
        prev_last_redo_date: 该题上一次重做的日期（用于「同日不算」）。

    返回：``wrong_count`` / ``redo_correct_streak`` / ``mastery_status`` /
    ``next_redo_due`` 四个键（**不含** ``last_redo_at``，由调用方写 ``today``）。
    """

    if result not in RESULT_VALUES:
        raise ValueError(f"非法 result: {result!r}")

    wrong_count = int(prev_wrong_count or 0)
    streak = int(prev_streak or 0)

    if result == RESULT_INCORRECT:
        return {
            "wrong_count": wrong_count + 1,
            "redo_correct_streak": 0,
            "mastery_status": PENDING,
            "next_redo_due": next_weekend(today),
        }

    crossed_day = prev_last_redo_date is None or prev_last_redo_date != today
    if crossed_day:
        streak += 1

    if crossed_day and streak >= MASTER_STREAK:
        return {
            "wrong_count": wrong_count,
            "redo_correct_streak": streak,
            "mastery_status": MASTERED,
            "next_redo_due": None,
        }

    due = snap_to_weekend(today + dt.timedelta(days=interval_days(streak)))
    return {
        "wrong_count": wrong_count,
        "redo_correct_streak": streak,
        "mastery_status": REDONE,
        "next_redo_due": due,
    }


def is_override_active(override: str | None) -> bool:
    """该题是否有人工标注。

    目前只支持标为「已掌握」；**取消标注是清空该列**，不是写别的值。所以这里
    只需要判「等于 mastered」，其余（空串 / None / 脏值）一律按「无标注」处理 ——
    脏值绝不能凭猜生效，否则库里一个手滑写的字符串就会把掌握度钉死。
    """

    return str(override or "").strip().lower() == MASTERED


def apply_mastery_override(*, prev_wrong_count: int = 0, prev_streak: int = 0) -> dict:
    """人工标注「已掌握」这一步的状态跃迁。

    与 ``apply_attempt`` 同形（返回同样四个键），调用方可以统一 ``state.update()``。

    **不动 ``wrong_count`` / ``redo_correct_streak``**：人工标注是老师对「她还会不会
    做」下的判断，不该篡改「错过几次」这个已经发生的事实；错次还要用于「按错次降序
    重点复习」。

    与其它事件的时间关系（这是本函数之外、由调用方排序实现的，此处只记口径）：

    - 标注**之前**的录入照样算数（它们先把状态推到当时的样子）；
    - 标注**之后**的录入重新接管 —— 又做错了就回到 ``pending``、重进重做池；
    - **同日冲突时人工标注优先**。因为录入的时间戳一律取当天 12:00（补录防午夜
      边界，见 ``main.py`` 的 grade 接口），若按分钟比较，「上午标、下午录」与
      「下午标、上午录」会给出相反结果 —— 老师无法预期。按日期比较则免疫。
    """

    return {
        "wrong_count": int(prev_wrong_count or 0),
        "redo_correct_streak": int(prev_streak or 0),
        "mastery_status": MASTERED,
        "next_redo_due": None,
    }
