"""回归测试：免费模型路由的难度评估（free_model_routing.evaluate_document_difficulty）。

HIGH-2 修复前，评估模型若返回带 ```json 围栏的内容，旧的围栏剥离只去反引号、
残留 `json` 语言标签导致 json.loads 失败并抛 RuntimeError（难度判定静默失败 / 误回退）。
本测试用 mock 让 post_chat_completion 返回带围栏的 JSON，验证能被正确解析。
"""
from types import SimpleNamespace

import pytest

from mathbank.free_model_routing import evaluate_document_difficulty


class _FakeResponse:
    def __init__(self, content):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


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
