import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from main import (
    correct_tikz_endpoint,
    draw_tikz_via_high_model,
    ocr_pdf_page_image,
    ocr_via_provider,
)
from mathbank.ai_providers import resolve_ocr_provider
from mathbank.prompts import COMMON_OCR_PROMPT, ILLUSTRATION_BOX_PROMPT


def test_ocr_request_uses_resolved_multimodal_provider(tmp_path):
    image_path = tmp_path / "question.png"
    image_path.write_bytes(b"fake-image-bytes")
    provider = resolve_ocr_provider(
        "zhongzhan_gpt",
        {
            "ZHONGZHAN_GPT_API_KEY": "ocr-key",
            "ZHONGZHAN_GPT_BASE_URL": "https://vision.example/v1",
            "ZHONGZHAN_GPT_OCR_MODEL": "gpt-5.6-luna:high",
        },
    )
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [{"message": {"content": "识别结果 $x=1$"}}]
    }

    with patch(
        "mathbank.ai_http.robust_request_post", return_value=response
    ) as mock_post:
        result = ocr_via_provider(
            str(image_path), provider, include_illustration_box=True
        )

    assert result == "识别结果 $x=1$"
    args, kwargs = mock_post.call_args
    assert args[0] == "https://vision.example/v1/chat/completions"
    assert kwargs["json"]["model"] == "gpt-5.6-luna"
    assert kwargs["json"]["reasoning_effort"] == "high"
    assert kwargs["json"]["enable_thinking"] is True
    image_item = kwargs["json"]["messages"][0]["content"][1]
    assert image_item["image_url"]["url"].startswith("data:image/png;base64,")


def test_bailian_ocr_explicitly_disables_thinking(tmp_path):
    image_path = tmp_path / "question.png"
    image_path.write_bytes(b"fake-image-bytes")
    provider = resolve_ocr_provider(
        "bailian",
        {
            "ALI_BAILIAN_API_KEY": "ocr-key",
            "ALI_BAILIAN_OCR_MODEL": "qwen3.7-flash",
        },
    )
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [{"message": {"content": "识别结果 $x=1$"}}]
    }

    with patch(
        "mathbank.ai_http.robust_request_post", return_value=response
    ) as mock_post:
        result = ocr_via_provider(str(image_path), provider)

    assert result == "识别结果 $x=1$"
    payload = mock_post.call_args.kwargs["json"]
    assert payload["enable_thinking"] is False
    assert payload["max_completion_tokens"] == 16384
    assert "thinking_budget" not in payload
    assert "reasoning_effort" not in payload


def test_ocr_read_timeout_is_not_retried(tmp_path):
    image_path = tmp_path / "question.png"
    image_path.write_bytes(b"fake-image-bytes")
    provider = resolve_ocr_provider(
        "zhongzhan_gpt",
        {
            "ZHONGZHAN_GPT_API_KEY": "ocr-key",
            "ZHONGZHAN_GPT_BASE_URL": "https://vision.example/v1",
            "ZHONGZHAN_GPT_OCR_MODEL": "gpt-5.6-luna",
        },
    )

    with patch(
        "mathbank.ai_http.robust_request_post",
        side_effect=requests.exceptions.ReadTimeout("unknown provider state"),
    ) as mock_post:
        with pytest.raises(requests.exceptions.ReadTimeout):
            ocr_via_provider(str(image_path), provider)

    mock_post.assert_called_once()


def test_pdf_ocr_does_not_fallback_after_ambiguous_read_timeout():
    providers = [MagicMock(provider_label="first"), MagicMock(provider_label="second")]

    with patch("main.resolve_ocr_fallbacks", return_value=providers), patch(
        "main.ocr_via_provider",
        side_effect=requests.exceptions.ReadTimeout("unknown provider state"),
    ) as mock_ocr:
        with pytest.raises(RuntimeError, match="避免重复计费"):
            ocr_pdf_page_image("/tmp/page.png")

    mock_ocr.assert_called_once()


def test_draw_request_strips_siliconflow_provider_prefix(tmp_path):
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"fake-diagram-bytes")
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": (
                        "\\begin{tikzpicture}"
                        "\\draw (0,0)--(1,1);"
                        "\\end{tikzpicture}"
                    )
                }
            }
        ]
    }

    with patch.dict(os.environ, {"SILICONFLOW_API_KEY": "sf-key"}):
        with patch(
            "mathbank.ai_http.robust_request_post", return_value=response
        ) as mock_post:
            result = draw_tikz_via_high_model(
                str(image_path),
                "SILICONFLOW/Qwen/Qwen3-VL-32B-Instruct",
                latex_content="三角形 ABC",
            )

    assert result.startswith("\\begin{tikzpicture}")
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.siliconflow.cn/v1/chat/completions"
    assert kwargs["json"]["model"] == "Qwen/Qwen3-VL-32B-Instruct"
    assert isinstance(kwargs["json"]["messages"][0]["content"], list)


def test_draw_request_injects_configured_reasoning_effort(tmp_path):
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"fake-diagram-bytes")
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [{"message": {"content": "\\begin{tikzpicture}\\end{tikzpicture}"}}]
    }
    provider_env = {
        "ZHONGZHAN_GPT_API_KEY": "draw-key",
        "ZHONGZHAN_GPT_BASE_URL": "https://draw.example/v1",
    }

    with patch.dict(os.environ, provider_env):
        with patch("mathbank.ai_http.robust_request_post", return_value=response) as mock_post:
            draw_tikz_via_high_model(
                str(image_path),
                "ZHONGZHAN_GPT/gpt-5.6-luna:high",
                latex_content="三角形 ABC",
            )

    payload = mock_post.call_args.kwargs["json"]
    assert payload["model"] == "gpt-5.6-luna"
    assert payload["reasoning_effort"] == "high"
    assert payload["enable_thinking"] is True


def test_pdf_ocr_uses_claude_provider_when_selected():
    provider_env = {
        "OCR_PREFER_ENGINE": "zhongzhan_claude",
        "ZHONGZHAN_CLAUDE_API_KEY": "claude-key",
        "ZHONGZHAN_CLAUDE_OCR_MODEL": "claude-vision",
        "SILICONFLOW_API_KEY": "",
        "ALI_BAILIAN_API_KEY": "",
        "ZHONGZHAN_GPT_API_KEY": "",
        "ZHONGZHAN_API_KEY": "",
    }

    with patch.dict(os.environ, provider_env):
        with patch("main.ocr_via_provider", return_value="整页识别结果") as mock_ocr:
            result = ocr_pdf_page_image("/tmp/page.png")

    assert result == "整页识别结果"
    resolved_provider = mock_ocr.call_args.args[1]
    assert resolved_provider.provider_code == "zhongzhan_claude"
    assert resolved_provider.model_name == "claude-vision"


def test_ocr_prompt_keeps_illustration_placeholders_and_narrows_autodraw():
    """识图提示词：多图保留占位、自动绘图触发条件收窄。

    旧规则是「只要包含几何/函数插图就追加 ILLUSTRATION_BOX」，模型在多图或
    表格内插图时也会整页自动重绘 TikZ（含把 TikZ 片段误当来源输出的幻觉）。
    新规则要求多图/表内图在原位留下 `[插图待补: 图N]`，且只有在「全图恰一幅、
    位于表格外、独立几何/函数图」三个条件同时满足时才输出 ILLUSTRATION_BOX。
    """
    # 主 OCR 规则：表格结构保留 + 多图占位，且明确禁止模型描述/重绘
    assert "[插图待补: 图1]" in COMMON_OCR_PROMPT
    assert "tabular" in COMMON_OCR_PROMPT
    assert "multicolumn" in COMMON_OCR_PROMPT and "multirow" in COMMON_OCR_PROMPT
    assert "勿描述、猜测或重绘" in COMMON_OCR_PROMPT

    # 自动绘图触发条件收窄
    assert "仅当全图恰有一幅" in ILLUSTRATION_BOX_PROMPT
    assert "位于表格外" in ILLUSTRATION_BOX_PROMPT
    assert "多图或无图时绝不输出" in ILLUSTRATION_BOX_PROMPT
    # 旧措辞「务必在文末追加」会诱导模型无条件输出，必须已移除
    assert "务必在文末追加" not in ILLUSTRATION_BOX_PROMPT

    # 拼接后编号连续（主规则 1-7，自动绘图为 8）
    assert "7. 纯净符号" in COMMON_OCR_PROMPT
    assert ILLUSTRATION_BOX_PROMPT.startswith("\n8. 自动绘图")


def test_ocr_request_injects_new_rules_into_prompt(tmp_path):
    """include_illustration_box=True 时，新规则确实进入实际请求 payload。"""
    image_path = tmp_path / "question.png"
    image_path.write_bytes(b"fake-image-bytes")
    provider = resolve_ocr_provider(
        "zhongzhan_gpt",
        {
            "ZHONGZHAN_GPT_API_KEY": "ocr-key",
            "ZHONGZHAN_GPT_BASE_URL": "https://vision.example/v1",
            "ZHONGZHAN_GPT_OCR_MODEL": "gpt-5.6-luna",
        },
    )
    response = MagicMock(status_code=200)
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}

    with patch("mathbank.ai_http.robust_request_post", return_value=response) as mock_post:
        ocr_via_provider(str(image_path), provider, include_illustration_box=True)

    prompt = mock_post.call_args.kwargs["json"]["messages"][0]["content"][0]["text"]
    assert "插图待补" in prompt
    assert "仅当全图恰有一幅" in prompt

    # 未开启自动绘图时，不应携带 ILLUSTRATION_BOX 规则
    with patch("mathbank.ai_http.robust_request_post", return_value=response) as mock_post2:
        ocr_via_provider(str(image_path), provider, include_illustration_box=False)

    prompt2 = mock_post2.call_args.kwargs["json"]["messages"][0]["content"][0]["text"]
    assert "ILLUSTRATION_BOX" not in prompt2


def test_tikz_correction_uses_resolved_bailian_provider(tmp_path):
    upload_dir = tmp_path / "static" / "uploads"
    upload_dir.mkdir(parents=True)
    original_path = upload_dir / "original.png"
    original_path.write_bytes(b"original-image")
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "```latex\n\\begin{tikzpicture}\n\\end{tikzpicture}\n```"
                }
            }
        ]
    }
    provider_env = {
        "PREFER_DRAW_MODEL": "BAILIAN/qwen3.7-max:medium",
        "ALI_BAILIAN_API_KEY": "bailian-key",
        "ALI_BAILIAN_API_BASE": "https://draw.example/v1",
    }

    with patch.dict(os.environ, provider_env):
        with patch("main.UPLOAD_DIR", str(upload_dir)), patch(
            "main.UPLOAD_DIR_REL", "static/uploads"
        ):
            with patch(
                "main.compile_tikz_to_png", side_effect=RuntimeError("compile failed")
            ):
                with patch(
                    "mathbank.ai_http.robust_request_post", return_value=response
                ) as mock_post:
                    result = correct_tikz_endpoint(
                        tikz_code="\\begin{tikzpicture}bad\\end{tikzpicture}",
                        original_image_path="/static/uploads/original.png",
                        user_prompt="修正线条",
                    )

    assert result["status"] == "success"
    args, kwargs = mock_post.call_args
    assert args[0] == "https://draw.example/v1/chat/completions"
    assert kwargs["json"]["model"] == "qwen3.7-max"
    assert kwargs["json"]["enable_thinking"] is True
    assert kwargs["json"]["thinking_budget"] == 8192
    assert kwargs["json"]["max_completion_tokens"] == 16384
    assert "reasoning_effort" not in kwargs["json"]
    assert len(kwargs["json"]["messages"][0]["content"]) == 2
