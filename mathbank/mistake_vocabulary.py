"""错因词表的加载与归一。

与 ``mathbank.curriculums.normalize_tag_list`` 的分工：那个管知识点 / 解题方法
标签，这个管「为什么做错」。两者共用「标准名 + 别名」的 JSON 结构，二期可并入
统一词表体系（见实施计划 §10 二期）。

归一侧重「不丢用户输入」：能对上别名的收敛到标准取值，对不上的原样保留 ——
用户想临时记一句「草稿纸算到一半接电话了」也不该被系统丢掉。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

VOCABULARY_FILE = Path(__file__).resolve().parent / "resources" / "mistake_reason_vocabulary.json"

#: 一期固定的错因标准取值（与 vocabulary json 的 reasons[].value 一致）
FALLBACK_REASONS = ["概念不清", "审题失误", "计算失误", "方法错误", "时间不足", "粗心"]


@lru_cache(maxsize=1)
def load_mistake_reason_vocabulary() -> dict:
    """读取错因词表；文件损坏时退回内置取值，不阻断功能。"""

    try:
        with VOCABULARY_FILE.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        payload = {}
    reasons = payload.get("reasons")
    if not isinstance(reasons, list) or not reasons:
        payload = {
            "version": "fallback",
            "reasons": [{"value": value, "aliases": []} for value in FALLBACK_REASONS],
        }
    return payload


def list_mistake_reasons() -> list[dict]:
    """返回 ``[{"value": ..., "aliases": [...]}, ...]``，供前端出 chip。"""

    reasons = load_mistake_reason_vocabulary().get("reasons") or []
    cleaned: list[dict] = []
    for item in reasons:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        aliases = [str(alias).strip() for alias in (item.get("aliases") or []) if str(alias).strip()]
        cleaned.append({"value": value, "aliases": aliases})
    return cleaned


def canonical_reason_values() -> list[str]:
    return [item["value"] for item in list_mistake_reasons()]


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for item in list_mistake_reasons():
        value = item["value"]
        index[_normalize_token(value)] = value
        for alias in item["aliases"]:
            index[_normalize_token(alias)] = value
    return index


def _normalize_token(token: str) -> str:
    """比对用归一：去空白与常见标点，大小写不敏感。"""

    text = str(token or "").strip().lower()
    for char in " \t\u3000·、,.。；;：:（）()【】[]":
        text = text.replace(char, "")
    return text


def normalize_mistake_reason_list(raw) -> str:
    """把错因归一为逗号分隔的字符串（去重、保序、别名收敛、未知项保留）。

    接受 ``None`` / 字符串 / 列表；空值返回空字符串。逗号兼容中英文。
    """

    if raw is None:
        return ""
    if isinstance(raw, (list, tuple, set)):
        tokens = [str(item) for item in raw]
    else:
        text = str(raw)
        for separator in ("，", "、", ";", "；", "|"):
            text = text.replace(separator, ",")
        tokens = text.split(",")

    index = _alias_index()
    ordered: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        cleaned = str(token).strip()
        if not cleaned:
            continue
        key = _normalize_token(cleaned)
        if not key:
            continue
        resolved = index.get(key, cleaned)
        dedupe_key = _normalize_token(resolved)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        ordered.append(resolved)
    return ",".join(ordered)


def split_mistake_reasons(value) -> list[str]:
    """把落库的逗号串拆回列表（前端渲染用）。"""

    normalized = normalize_mistake_reason_list(value)
    return [item for item in normalized.split(",") if item]
