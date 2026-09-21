"""上传图片引用的唯一权威口径（题目 / 错题记录 → 仍在用的图）。

为什么单独成模块：**完整备份**（``mathbank.backup``）与**启动孤儿图清理**
（``main.clean_orphaned_images``）都要判断「哪些图还被数据库引用」。两边若各写
一份引用来源清单，迟早分叉 —— 一边保留、另一边删除同一张文件，题目配图就会
被清理器悄悄删掉。这里把「去哪儿找引用」固化成一份契约，两个出口共用。

新增任何承载图片的列时，必须同步登记进 :data:`REFERENCE_TABLES`，否则清理器
会把它当孤儿删掉。
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Iterator, Mapping

DEFAULT_UPLOAD_PREFIX = "/static/uploads"

# 「表 → {列: 存法}」。存法有两种：
#   "json" —— 该列是 JSON 数组，逐条当作图片路径；
#   "text" —— 该列是自由文本，从中扫出 /static/uploads/... 形式的 URL。
# 这是备份与清理共同遵守的唯一来源清单。
REFERENCE_TABLES: Mapping[str, Mapping[str, str]] = {
    "questions": {
        "image_paths": "json",
        "content": "text",
        "answer_markdown": "text",
    },
    "mistake_records": {
        "image_block": "text",
        "block_images": "json",
        "image_figure": "json",
        "answer_image": "json",
    },
}


def _compile_pattern(url_prefix: str) -> re.Pattern[str]:
    prefix = "/" + url_prefix.strip("/") + "/"
    if prefix == DEFAULT_UPLOAD_PREFIX + "/":
        # 与历史实现逐字一致，避免大小写/字符类漂移改变匹配集合。
        return re.compile(r"/static/uploads/[A-Za-z0-9][A-Za-z0-9_.\-/]*")
    return re.compile(re.escape(prefix) + r"[A-Za-z0-9][A-Za-z0-9_.\-/]*")


def _rows_runner(source):
    """Return a callable ``sql -> result`` that works for a SQLAlchemy
    ``Connection`` or a raw ``sqlite3.Connection``.

    SQLAlchemy 2.0 rejects bare-string ``Connection.execute``; ``exec_driver_sql``
    takes the plain string.  Raw sqlite3 connections have no such method.
    """

    runner = getattr(source, "exec_driver_sql", None)
    if runner is not None:
        return runner
    return source.execute


def iter_upload_references(
    source,
    *,
    tables: Mapping[str, Mapping[str, str]] = REFERENCE_TABLES,
    url_prefix: str = DEFAULT_UPLOAD_PREFIX,
    strict: bool = False,
) -> Iterator[str]:
    """Yield every upload URL that *source* still references.

    ``source`` is a SQLAlchemy ``Connection`` or a ``sqlite3.Connection``.

    ``strict=True``（备份用，fail-closed）在结构化列 JSON 损坏或类型异常时抛
    ``RuntimeError``；``strict=False``（清理用）跳过坏行 —— 清理绝不能因为一行
    脏数据而中止整个启动，更不能因此拿到一个偏小的引用集去删文件。
    """

    run = _rows_runner(source)
    pattern = _compile_pattern(url_prefix)
    for table, columns in tables.items():
        try:
            present = {
                row[1]
                for row in run(f'PRAGMA table_info("{table}")').fetchall()
            }
        except sqlite3.Error:  # pragma: no cover - 表不存在等
            if strict:
                raise
            continue
        if not present:
            continue
        for column, kind in columns.items():
            if column not in present:
                continue
            try:
                rows = run(f'SELECT "{column}" FROM "{table}"').fetchall()
            except sqlite3.Error:
                if strict:
                    raise
                continue
            for row in rows:
                value = row[0]
                if value is None:
                    continue
                if kind == "json":
                    try:
                        parsed = json.loads(value)
                    except (TypeError, json.JSONDecodeError) as exc:
                        if strict:
                            raise RuntimeError(
                                f"表 {table} 的 {column} JSON 已损坏"
                            ) from exc
                        continue
                    if not isinstance(parsed, list):
                        if strict:
                            raise RuntimeError(f"表 {table} 的 {column} 不是数组")
                        continue
                    for item in parsed:
                        if isinstance(item, str):
                            yield item
                        elif strict:
                            raise RuntimeError(
                                f"表 {table} 的 {column} 含非字符串条目"
                            )
                    continue
                if isinstance(value, str):
                    for match in pattern.finditer(value):
                        yield match.group(0)


def collect_live_upload_paths(
    source,
    *,
    url_prefix: str = DEFAULT_UPLOAD_PREFIX,
) -> set[str]:
    """Return referenced uploads as lower-cased relative paths.

    The result is the comparison form the orphan cleanup builds for files on
    disk (``static/uploads/xxx.png``): no leading slash, lower-cased, query and
    fragment stripped.
    """

    prefix = "/" + url_prefix.strip("/") + "/"
    live: set[str] = set()
    for reference in iter_upload_references(source, url_prefix=url_prefix):
        normalized = reference.strip().split("?", 1)[0].split("#", 1)[0]
        if normalized.startswith(prefix):
            live.add(normalized.lstrip("/").lower())
    return live
