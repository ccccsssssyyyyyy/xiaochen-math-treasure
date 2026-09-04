"""MathBank shared backend support package."""

__version__ = "2.1.4"
# 当前是「小陈的数学宝藏」本地定制派生（fork from JudgePeach/math-question-bank）。
# 留作占位 —— 前端若以此向上游查询更新，会触发覆盖升级流程把 76 个本地提交全部清零，
# 且 fork 的 schema user_version=1004 > 上游 LATEST → `migrate_database` 直接抛 RuntimeError
# → **数据库拒绝打开**。开源前请改为自有仓库名 "<owner>/math-question-bank"。
GITHUB_REPO = "localfork/math-question-bank"
