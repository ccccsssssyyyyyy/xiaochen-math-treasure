"""题目来源（source）的 LLM 兜底归一。

定位：``mathbank.source_normalize`` 的 **Tier 3**。前置的确定层（Tier 1 精确映射 /
Tier 1.2 幂等 / Tier 1.5 周练 / Tier 1.6 模拟联考 / Tier 2 噪声清理）对**见过结构**的
来源足够快且准；只有遇到全新来源（陌生学校文件名、没见过的联考名）时，规则引擎
只能做噪声清理、无法推断出「哪一段是学校、哪一段是学年」，这时才由本模块交给
小模型按规约加工一次，并把结果**沉淀回映射表**，下次同类来源零成本命中。

设计约束（与本项目"免费、彻底、可端到端验证"的排障原则一致）：

- **默认关闭** —— 只有调用方显式传 ``allow_ai=True`` 才会触发，手动保存等路径
  保持纯规则（零延迟、零不确定性、无网络依赖）。
- **结果必须过规约校验** —— LLM 返回值经 ``is_canonical_source`` 判定，不合规
  或为空一律视为失败，退回规则结果，绝不把脏值带进库。
- **失败静默降级** —— 无 Key / 超时 / 网络异常 / JSON 解析失败都不抛穿，
  最坏情况只是"跟没开一样"，绝不阻断入库。
- **缓存 + 落盘** —— 进程内字典立即生效（无需重启）；同时原子写回
  ``data/source_canonical_map.json``，用户可查看、可手工改，重启后依然有效。
  落盘目标由 ``_resolve_writable_map_paths()`` 决定，**不含**只读覆盖
  ``MATHBANK_SOURCE_MAP``（那是测试/CI 指向仓库模板用的，写入会污染被提交文件）。

可用环境变量：

- ``SOURCE_AI_FALLBACK=0`` —— 全局关闭兜底（紧急熔断）。
- ``MATHBANK_SOURCE_MAP_WRITE=<path>`` —— 重定向落盘位置（测试指向临时文件）。
"""

import json
import logging
import os
import re
import threading
from pathlib import Path

from mathbank.ai_http import post_chat_completion
from mathbank.free_model_routing import decide_classify_model
from mathbank.source_normalize import (
    CANONICAL_MAP,
    UNKNOWN,
    _resolve_writable_map_paths,
    build_source_rule_prompt,
    is_canonical_source,
)

# 单次兜底调用的上限（这是几十字的字符串加工任务，20s 足够）
_TIMEOUT_SECONDS = 20

# 进程内缓存：原始来源 -> 规范值（None 表示"试过但没规整成功"，同样缓存以避免反复调）
_CACHE: dict[str, "str | None"] = {}
_CACHE_LOCK = threading.Lock()
_FILE_LOCK = threading.Lock()


def _enabled() -> bool:
    return os.getenv("SOURCE_AI_FALLBACK", "1").strip().lower() not in ("0", "false", "no", "off")


def _clean_output(text: str) -> str:
    """清洗模型输出：去代码块 / 包裹引号 / 解释性后续行 / 多余空白。"""
    if not text:
        return ""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"```$", "", t).strip()
    # 只要第一行：模型常在规范值后面追加「（理由：…）」之类解释
    if "\n" in t:
        t = t.splitlines()[0].strip()
    t = t.strip().strip("`").strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"', "「", "『"):
        t = t[1:-1].strip()
    return re.sub(r"\s+", " ", t)


def _call_llm(raw: str, fallback_title=None):
    """调一次小模型把原始来源加工成规约写法；失败返回 None。"""
    decision = decide_classify_model(use_free=True)
    provider = decision.get("provider")
    if provider is None or not getattr(provider, "api_key", None):
        return None

    model_name = provider.model_name
    api_base = getattr(provider, "api_base", "") or ""
    user_lines = [f"待加工的原始来源：{raw}"]
    if fallback_title and str(fallback_title).strip() != raw:
        user_lines.append(f"该卷的试卷标题（可作为推断依据）：{fallback_title}")
    user_lines.append(
        "只输出一个符合规约的来源字符串。不要解释、不要引号、不要代码块、"
        "不要输出 JSON。无法确定就输出空字符串。"
    )

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": build_source_rule_prompt()},
            {"role": "user", "content": "\n".join(user_lines)},
        ],
        "temperature": 0,
        "max_tokens": 256,
    }
    # DeepSeek 系默认开启思考，会吃掉 max_tokens 并拖慢响应，显式关闭
    if "deepseek" in model_name.lower() or "deepseek" in api_base.lower():
        payload["thinking"] = {"type": "disabled"}

    try:
        response = post_chat_completion(
            provider,
            payload,
            timeout=_TIMEOUT_SECONDS,
            provider_name=getattr(provider, "credential_label", "ai"),
        )
        content = (
            response.json()
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
    except Exception as exc:  # noqa: BLE001 - 兜底链路不得抛穿
        logging.warning("source_ai_fallback: LLM 调用失败 (%s)", exc)
        return None

    candidate = _clean_output(content or "")
    if not candidate or candidate == UNKNOWN:
        return None
    # 关键闸门：不合规约的结果一律丢弃，宁可退回规则结果也不污染题库
    if not is_canonical_source(candidate):
        logging.warning("source_ai_fallback: 输出不合规则已丢弃 -> %r", candidate)
        return None
    return candidate


def _user_map_path():
    """返回**可写**的用户映射文件路径；不存在则不落盘（仅内存缓存生效）。

    刻意走 ``_resolve_writable_map_paths()`` 而非读取路径：``MATHBANK_SOURCE_MAP``
    只是只读覆盖（测试/CI 指向仓库根的示例模板），若拿它当落盘目标，AI 学到的
    条目会被写进被提交的模板文件——本模块早期就踩过这个坑。
    """
    for p in _resolve_writable_map_paths():
        if p.is_file():
            return p
    return None


def _persist(raw: str, canonical: str) -> None:
    """把新学到的映射写回用户配置文件（原子写，保留注释字段，不覆盖已有条目）。"""
    path = _user_map_path()
    if path is None:
        return
    with _FILE_LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logging.warning("source_ai_fallback: 读取映射文件失败 %s (%s)", path, exc)
            return
        if not isinstance(data, dict):
            return
        entries = data.get("school_entries")
        if not isinstance(entries, dict):
            return
        if raw in entries:
            return  # 用户已手工维护过该条目，不覆盖
        entries[raw] = canonical
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, path)
        except OSError as exc:
            logging.warning("source_ai_fallback: 写回映射文件失败 %s (%s)", path, exc)


def ai_normalize_source(raw, fallback_title=None):
    """把规则引擎无力规整的全新来源交给 LLM 加工。

    成功返回规范写法（已写回运行时映射表与用户配置文件），
    失败 / 不适用一律返回 ``None``，由调用方退回规则结果。
    """
    if not _enabled() or raw is None:
        return None
    key = str(raw).strip()
    if not key or key == UNKNOWN:
        return None
    # 已合规的来源不需要兜底（调用方通常已过滤，此处为独立调用时的防御）
    if is_canonical_source(key):
        return None

    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]

    result = _call_llm(key, fallback_title)

    with _CACHE_LOCK:
        _CACHE[key] = result

    if result:
        # 运行时立即生效，本次进程内后续同来源不再调 LLM，也无需重启
        CANONICAL_MAP[key] = result
        CANONICAL_MAP.setdefault(result, result)
        _persist(key, result)
    return result


def reset_cache() -> None:
    """清空进程内缓存（仅供测试使用）。"""
    with _CACHE_LOCK:
        _CACHE.clear()
