"""回归测试：免费模型路由的难度评估（free_model_routing.evaluate_document_difficulty）。

HIGH-2 修复前，评估模型若返回带 ```json 围栏的内容，旧的围栏剥离只去反引号、
残留 `json` 语言标签导致 json.loads 失败并抛 RuntimeError（难度判定静默失败 / 误回退）。
本测试用 mock 让 post_chat_completion 返回带围栏的 JSON，验证能被正确解析。
"""
from types import SimpleNamespace

import pytest

from mathbank.free_model_routing import decide_parse_model, evaluate_document_difficulty


class _FakeResponse:
    def __init__(self, content):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _install_fake_providers(monkeypatch):
    """把 resolve_text_provider / post_chat_completion 换成假的，避免真实网络调用。

    返回 {"posts": n}，记录 LLM 请求次数（用于验证 force_paid 是否跳过评估）。
    """
    calls = {"n": 0}

    def _resolve(model):
        return SimpleNamespace(
            model_name=model,
            api_key="fake-key",
            api_base="",
            provider_code="fake",
            provider_label="fake",
            reasoning_effort=None,
            credential_label="fake",
        )

    def _fake_post(prov, data, timeout=None, provider_name=None):
        calls["n"] += 1
        return _FakeResponse(
            '{"difficulty":"simple","has_dense_formula":false,'
            '"has_many_images":false,"reason":"简单卷"}'
        )

    monkeypatch.setattr("mathbank.free_model_routing.resolve_text_provider", _resolve)
    monkeypatch.setattr("mathbank.free_model_routing.post_chat_completion", _fake_post)
    monkeypatch.setenv("PREFER_PARSE_MODEL", "PAID/deepseek-v4-flash")
    monkeypatch.setenv("PREFER_FREE_PARSE_MODEL", "FREE/deepseek-v3")
    return calls


def test_force_paid_selects_paid_model_and_skips_evaluation(monkeypatch):
    """Word 链路强制付费：直接选付费模型，且不发起难度评估请求。

    免费模型对 Word 公式锁协议（[[Mn]]）遵守率偏低，故 Word 默认走付费；
    此处额外锁定「短路发生在评估调用之前」，顺带省掉一次评估 token。
    """
    calls = _install_fake_providers(monkeypatch)
    result = decide_parse_model("短试卷内容" * 10, force_paid=True)

    assert result["used_free"] is False
    assert result["raw_model"] == "PAID/deepseek-v4-flash"
    assert result["provider"].model_name == "PAID/deepseek-v4-flash"
    assert calls["n"] == 0, "force_paid 不应触发任何难度评估请求"


def test_default_force_paid_false_keeps_difficulty_routing(monkeypatch):
    """force_paid 默认 False：PDF 等链路仍按难度评估路由到免费模型，行为不变。"""
    calls = _install_fake_providers(monkeypatch)
    result = decide_parse_model("短试卷内容" * 10)

    assert result["used_free"] is True
    assert result["raw_model"] == "FREE/deepseek-v3"
    assert calls["n"] == 1, "默认路径应正常发起一次难度评估"



def test_real_exam_paper_length_routes_to_paid(monkeypatch):
    """实测失败样本长度（10846 字符的高三联测评卷）必须被判 hard 走付费。

    该卷落在原 15000 门槛之下被判给免费模型，免费模型全部返回空，整卷报
    「所有块均解析失败」。门槛下调至 10000 后应直接短路到付费模型，
    且不再发起难度评估（省一次评估 token）。
    """
    calls = _install_fake_providers(monkeypatch)
    result = decide_parse_model("x" * 10846)

    assert result["used_free"] is False
    assert result["raw_model"] == "PAID/deepseek-v4-flash"
    assert result["difficulty"] == "hard"
    assert calls["n"] == 0, "长度判 hard 应在评估调用之前短路"


def test_hard_threshold_boundary_is_exclusive(monkeypatch):
    """门槛为严格大于：正好等于 10000 仍走免费，10001 起走付费。"""
    from mathbank.free_model_routing import HARD_LENGTH_THRESHOLD

    _install_fake_providers(monkeypatch)
    assert HARD_LENGTH_THRESHOLD == 10000
    assert decide_parse_model("x" * 10000)["used_free"] is True
    assert decide_parse_model("x" * 10001)["used_free"] is False


def test_short_document_still_uses_free_model(monkeypatch):
    """小测/周练（实测 <= 8000 字符）仍走免费模型，不被误伤。"""
    calls = _install_fake_providers(monkeypatch)
    result = decide_parse_model("x" * 5000)

    assert result["used_free"] is True
    assert result["raw_model"] == "FREE/deepseek-v3"
    assert calls["n"] == 1


@pytest.fixture
def fake_eval(monkeypatch):
    provider = SimpleNamespace(
        model_name="fake-eval-model",
        api_key="fake-key",
        api_base="",
        provider_code="siliconflow",
        provider_label="eval",
        reasoning_effort=None,
        credential_label="eval",
    )
    monkeypatch.setattr(
        "mathbank.free_model_routing.resolve_text_provider",
        lambda model: provider,
    )
    state = {"raw": ""}

    def _fake_post(prov, data, timeout=None, provider_name=None):
        return _FakeResponse(state["raw"])

    monkeypatch.setattr(
        "mathbank.free_model_routing.post_chat_completion", _fake_post
    )
    return state


def test_evaluate_strips_json_fence(fake_eval):
    fake_eval["raw"] = (
        '```json\n'
        '{"difficulty":"simple","has_dense_formula":true,"has_many_images":false,'
        '"reason":"公式密度低"}\n'
        "```"
    )
    result = evaluate_document_difficulty("设 $x=1$，求 $f(x)$。", "fake-eval-model")
    assert result["difficulty"] == "simple"
    assert result["has_dense_formula"] is True
    assert result["has_many_images"] is False
    assert "公式密度低" in result["reason"]


def test_evaluate_handles_plain_json(fake_eval):
    fake_eval["raw"] = (
        '{"difficulty":"hard","has_dense_formula":false,'
        '"has_many_images":true,"reason":"图片多"}'
    )
    result = evaluate_document_difficulty("长篇试卷含多图", "fake-eval-model")
    assert result["difficulty"] == "hard"
    assert result["has_many_images"] is True


def test_evaluate_rejects_empty_content(fake_eval):
    fake_eval["raw"] = ""
    with pytest.raises(RuntimeError):
        evaluate_document_difficulty("x", "fake-eval-model")


def test_default_paid_parse_model_is_deepseek_flash(monkeypatch):
    """付费拆解模型默认值必须是 deepseek-flash。

    DeepSeek V4.1 Flash（2026-09-10 发布）官方模型 ID 为 deepseek-flash；
    旧名 deepseek-v4-flash 已下线，仅临时兼容路由，撤掉后将 404。
    此测试锁死默认值，防止回退到已下线名称。
    """
    import mathbank.free_model_routing as fmr

    monkeypatch.delenv("PREFER_PARSE_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_PARSE_MODEL", raising=False)
    paid = fmr.resolve_text_provider(fmr.PAID_PARSE_MODEL_DEFAULT)
    assert paid.model_name == "deepseek-flash"


def test_paid_parse_default_constant_matches_official_id():
    """常量即文档：默认付费模型名与官方 /models 列表保持一致。"""
    import mathbank.free_model_routing as fmr

    assert fmr.PAID_PARSE_MODEL_DEFAULT == "deepseek-flash"


def test_no_hardcoded_retired_model_name_in_runtime_code():
    """已下线模型名不得再出现在运行时代码与配置中（测试夹具除外）。

    deepseek-v4-flash 已被官方下线，仅临时兼容路由；新代码一律使用
    PAID_PARSE_MODEL_DEFAULT（deepseek-flash）或官方其他在售名称。
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    scanned = [
        *root.glob("main.py"),
        *(root / "mathbank").glob("*.py"),
        *(root / "static" / "js").glob("*.js"),
        root / ".env",
    ]
    # 匹配旧名但排除官方 V4 Pro（仍在售）与 SiliconFlow 的 DeepSeek-V4 系列
    # 仅匹配 DeepSeek 官方已下线的裸模型名；排除 SiliconFlow 托管的
    # "deepseek-ai/DeepSeek-V4-Flash"（不同供应商的模型 ID，不在下线范围）。
    retired = re.compile(r"(?<!deepseek-ai/)deepseek-v4-flash", re.IGNORECASE)
    offenders = []
    for f in scanned:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if retired.search(text):
            offenders.append(str(f.relative_to(root)))
    assert not offenders, f"发现已下线模型名 deepseek-v4-flash 残留: {offenders}"
