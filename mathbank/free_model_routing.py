"""免费/付费模型自动路由：用免费模型评估文档难度，再决定用哪类模型拆解。

设计要点：
- 评估读取 LaTeX 源码（公式/图片/版面信号在 LaTeX 中以显式标记存在），
  比「看渲染图猜测」更可靠，且对 PDF 与纯 LaTeX 两条导入链路统一生效。
- 免费模型未配置、或其 API Key 缺失、或评估判定为「困难」时，安全回退到付费模型，
  绝不裸奔。
"""

import json
import os

from mathbank.ai_providers import (
    resolve_text_provider,
    inject_reasoning_effort,
    apply_bailian_thinking_policy,
)
from mathbank.ai_http import post_chat_completion
from mathbank.ai_json import _strip_markdown_fence


def evaluate_document_difficulty(latex_content: str, eval_model: str) -> dict:
    """用免费模型评估试卷/习题 LaTeX 源码的拆解难度，返回结构化结论。

    返回 {"difficulty": simple|medium|hard, "has_dense_formula": bool,
          "has_many_images": bool, "reason": str}。
    """

    provider = resolve_text_provider(eval_model)
    if not provider.api_key:
        raise RuntimeError(f"评估模型未配置 API Key ({provider.credential_label})")

    system_prompt = (
        "你是数学试卷拆解难度评估器。给定一份数学试卷或习题的 LaTeX 源码，"
        "请判断其智能拆解难度。重点考察："
        "1) 公式密度：行内与行间公式的数量与复杂度；"
        "2) 图片/图形数量：配图、坐标系、几何图形；"
        "3) 版面复杂度：多栏、跨页表格、嵌套环境。"
        "只输出一个 JSON 对象，不要任何额外解释："
        '{"difficulty":"simple|medium|hard","has_dense_formula":true|false,'
        '"has_many_images":true|false,"reason":"简短中文说明"}'
    )

    # 仅取前 4000 字符即可判断密度，避免评估请求体过大
    sample = (latex_content or "")[:4000]
    data = {
        "model": provider.model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": sample},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 256,
    }

    is_deepseek = (
        ("deepseek" in provider.model_name.lower() or "deepseek" in (provider.api_base or "").lower())
        and "deepseek-chat" not in provider.model_name.lower()
        and "deepseek-reasoner" not in provider.model_name.lower()
    )
    if is_deepseek and provider.reasoning_effort in {None, "default"}:
        data["thinking"] = {"type": "disabled"}
    data = inject_reasoning_effort(data, provider.reasoning_effort)
    data = apply_bailian_thinking_policy(
        data,
        provider_code=provider.provider_code,
        model_name=provider.model_name,
        task="classify",
    )

    response = post_chat_completion(provider, data, timeout=30, provider_name=provider.provider_label)
    res_json = response.json()
    ai_text = res_json.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

    if not ai_text:
        raise RuntimeError("评估模型返回了空内容，无法判断难度。")

    # 部分模型在 json_object 之外仍会包裹 ```json 围栏，需用通用围栏提取
    # （仅去反引号会残留 `json` 语言标签导致 json.loads 失败）。
    ai_text = _strip_markdown_fence(ai_text)

    try:
        result = json.loads(ai_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"评估模型返回了非 JSON 内容：{ai_text[:120]}") from exc
    difficulty = result.get("difficulty", "medium")
    if difficulty not in ("simple", "medium", "hard"):
        difficulty = "medium"
    return {
        "difficulty": difficulty,
        "has_dense_formula": bool(result.get("has_dense_formula", False)),
        "has_many_images": bool(result.get("has_many_images", False)),
        "reason": str(result.get("reason", ""))[:200],
    }


def _difficulty_severity(level: str) -> int:
    """simple<medium<hard 的严重程度量化，便于取两者中的较严重者。"""
    return {"simple": 0, "medium": 1, "hard": 2}.get(level, 1)


def _length_based_difficulty(text: str) -> str:
    """仅凭文档规模（字数 + 图片/公式密度）粗判难度，避免大文档被误判为简单。

    - 字数很多或图片极多 → hard（直接走付费，保证质量与时效）
    - 字数较多或图片较多 → medium
    - 否则 → simple
    """
    length = len(text or "")
    img_markers = (text or "").count("![](") + (text or "").count("\\includegraphics")
    # 经验阈值：免费模型（SiliconFlow 等）对大文档响应极慢、易超时，
    # 故 1.5 万字符以上（或图片极多）直接判 hard 走付费，从源头规避超时。
    if length > 15000 or img_markers > 18:
        return "hard"
    if length > 8000 or img_markers > 8:
        return "medium"
    return "simple"


def decide_parse_model(latex_content: str) -> dict:
    """根据免费模型难度评估，决定本次拆解使用免费还是付费模型。

    返回 {"raw_model", "provider", "paid_raw_model", "paid_provider",
          "difficulty", "reason", "used_free"}。
    provider 为本次实际选用的 TextProviderConfig；paid_provider 恒为付费模型，
    供调用方在免费模型超时时自动回退使用。
    """

    paid_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv("DEEPSEEK_PARSE_MODEL", "deepseek-v4-flash")
    paid_provider = resolve_text_provider(paid_model)
    free_model = os.getenv("PREFER_FREE_PARSE_MODEL") or ""

    def _paid_result(difficulty, reason):
        return {
            "raw_model": paid_model,
            "provider": paid_provider,
            "paid_raw_model": paid_model,
            "paid_provider": paid_provider,
            "difficulty": difficulty,
            "reason": reason,
            "used_free": False,
        }

    if not free_model:
        return _paid_result(None, "未配置免费拆解模型，使用付费模型")

    # 长度感知优先：明显又长又密的文档直接判 hard 走付费，跳过评估调用，
    # 既避免误路由到响应慢的免费模型，也省去一次多余的评估请求。
    length_level = _length_based_difficulty(latex_content)
    if length_level == "hard":
        return _paid_result(
            "hard",
            f"文档较大/图片较多（{len(latex_content)} 字符），直接走付费模型以保证质量",
        )

    eval_model = os.getenv("PREFER_FREE_EVAL_MODEL") or "SILICONFLOW/deepseek-ai/DeepSeek-V3"
    difficulty = "medium"
    reason = ""
    try:
        eval_result = evaluate_document_difficulty(latex_content, eval_model)
        difficulty = eval_result["difficulty"]
        reason = eval_result["reason"]
    except Exception as exc:  # 评估失败不应阻断拆解，默认走免费
        reason = f"难度评估失败，默认使用免费模型：{exc}"

    # 取「评估难度」与「长度难度」中较严重的一档，避免大文档被评估器低估为简单。
    if _difficulty_severity(length_level) > _difficulty_severity(difficulty):
        difficulty = length_level
        reason = (reason + "；" if reason else "") + f"文档较长（{len(latex_content)} 字符），已上调难度档位"

    if difficulty == "hard":
        return _paid_result(difficulty, reason or "复杂文档，自动使用付费模型以保证质量")

    free_provider = resolve_text_provider(free_model)
    if not free_provider.api_key:
        return _paid_result(difficulty, "免费模型未配置对应 Key，回退付费模型")
    return {
        "raw_model": free_model,
        "provider": free_provider,
        "paid_raw_model": paid_model,
        "paid_provider": paid_provider,
        "difficulty": difficulty,
        "reason": reason,
        "used_free": True,
    }


def decide_classify_model(use_free: bool) -> dict:
    """为 AI 分类选择模型：use_free 为真且免费分类模型已配置则走免费，否则付费。

    返回 {"raw_model", "provider"}。
    """

    default_model = (
        os.getenv("PREFER_CLASSIFY_MODEL")
        or os.getenv("DEEPSEEK_CLASSIFY_MODEL")
        or os.getenv("PREFER_PARSE_MODEL")
        or "deepseek-v4-flash"
    )
    default_provider = resolve_text_provider(default_model)

    if not use_free:
        return {"raw_model": default_model, "provider": default_provider}

    free_model = os.getenv("PREFER_FREE_CLASSIFY_MODEL") or ""
    if not free_model:
        return {"raw_model": default_model, "provider": default_provider}

    free_provider = resolve_text_provider(free_model)
    if not free_provider.api_key:
        return {"raw_model": default_model, "provider": default_provider}
    return {"raw_model": free_model, "provider": free_provider}
