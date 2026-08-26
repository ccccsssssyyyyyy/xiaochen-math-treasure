"""免费/付费模型自动路由逻辑的单测（不依赖真实网络）。

通过 monkey-patch free_model_routing 的 resolve_text_provider / post_chat_completion / os.getenv，
覆盖：免费未配置、评估简单/困难、免费 Key 缺失、评估异常、分类路由 等分支。
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---- 控制 evaluate_document_difficulty 的返回（需在导入模块前定义）----
EVAL_DIFFICULTY = "medium"
EVAL_RAISE = False


class FakeResp:
    def json(self):
        return {"choices": [{"message": {"content": '{"difficulty":"%s","has_dense_formula":false,"has_many_images":false,"reason":"test"}' % EVAL_DIFFICULTY}}]}


def fake_post(*args, **kwargs):
    if EVAL_RAISE:
        raise RuntimeError("eval network error")
    return FakeResp()


# 用 fake 模块顶替 mathbank.ai_http，避免导入 requests 等重依赖
fake_ai_http = types.ModuleType("mathbank.ai_http")
fake_ai_http.post_chat_completion = fake_post
sys.modules["mathbank.ai_http"] = fake_ai_http


class FakeProvider:
    def __init__(self, api_key="k", model_name="m", provider_label="L",
                 provider_code="siliconflow", credential_label="C", reasoning_effort=None):
        self.api_key = api_key
        self.api_base = "https://example.com/v1"
        self.model_name = model_name
        self.provider_label = provider_label
        self.provider_code = provider_code
        self.credential_label = credential_label
        self.reasoning_effort = reasoning_effort


def make_provider(model):
    if model == "MISSING_KEY":
        return FakeProvider(api_key=None, model_name="x", provider_label="无Key", provider_code="siliconflow")
    name = model.split("/")[-1] if "/" in model else model
    code = "siliconflow" if "SILICONFLOW" in model else "deepseek"
    return FakeProvider(api_key="k", model_name=name, provider_label="L", provider_code=code)


def fake_resolve(model):
    return make_provider(model)


import mathbank.free_model_routing as mod

# 注入 fake
mod.resolve_text_provider = fake_resolve
mod.post_chat_completion = fake_post

# env 控制
_ENV = {}


def fake_getenv(name, default=None):
    return _ENV.get(name, default)


mod.os.getenv = fake_getenv

# ---- 断言工具 ----
passed = 0
failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}")


# ---- 测试用例 ----

# 1. 免费未配置 -> 全程付费
_ENV = {"PREFER_PARSE_MODEL": "deepseek-v4-flash"}
d = mod.decide_parse_model("some latex")
check("免费未配置时使用付费", d["used_free"] is False and "deepseek" in d["raw_model"])

# 2. 免费已配置 + 评估简单 -> 免费
_ENV = {
    "PREFER_PARSE_MODEL": "deepseek-v4-flash",
    "PREFER_FREE_PARSE_MODEL": "SILICONFLOW/deepseek-ai/DeepSeek-V3",
    "PREFER_FREE_EVAL_MODEL": "SILICONFLOW/deepseek-ai/DeepSeek-V3",
}
EVAL_DIFFICULTY = "simple"
d = mod.decide_parse_model("latex content")
check("评估简单时走免费", d["used_free"] is True and "DeepSeek-V3" in d["raw_model"])

# 3. 免费已配置 + 评估中等 -> 免费
EVAL_DIFFICULTY = "medium"
d = mod.decide_parse_model("latex content")
check("评估中等时走免费", d["used_free"] is True)

# 4. 免费已配置 + 评估困难 -> 付费
EVAL_DIFFICULTY = "hard"
d = mod.decide_parse_model("latex content")
check("评估困难时回退付费", d["used_free"] is False and "deepseek" in d["raw_model"])

# 5. 免费已配置但 Key 缺失 -> 安全回退付费
_ENV = {
    "PREFER_PARSE_MODEL": "deepseek-v4-flash",
    "PREFER_FREE_PARSE_MODEL": "MISSING_KEY",
    "PREFER_FREE_EVAL_MODEL": "SILICONFLOW/deepseek-ai/DeepSeek-V3",
}
EVAL_DIFFICULTY = "simple"
d = mod.decide_parse_model("latex content")
check("免费Key缺失时回退付费", d["used_free"] is False and "deepseek" in d["raw_model"])

# 6. 评估调用异常 -> 默认中等 -> 免费（不阻断）
EVAL_RAISE = True
_ENV = {
    "PREFER_PARSE_MODEL": "deepseek-v4-flash",
    "PREFER_FREE_PARSE_MODEL": "SILICONFLOW/deepseek-ai/DeepSeek-V3",
    "PREFER_FREE_EVAL_MODEL": "SILICONFLOW/deepseek-ai/DeepSeek-V3",
}
d = mod.decide_parse_model("latex content")
check("评估异常默认走免费不阻断", d["used_free"] is True)
EVAL_RAISE = False

# 7. 分类路由：use_free=False -> 默认
c = mod.decide_classify_model(False)
check("分类不请求免费时走默认", "deepseek" in c["raw_model"])

# 8. 分类路由：use_free=True + 免费已配置 -> 免费
_ENV = {
    "PREFER_CLASSIFY_MODEL": "deepseek-v4-flash",
    "PREFER_FREE_CLASSIFY_MODEL": "SILICONFLOW/deepseek-ai/DeepSeek-V3",
}
c = mod.decide_classify_model(True)
check("分类请求免费且已配置时走免费", "DeepSeek-V3" in c["raw_model"])

# 9. 分类路由：use_free=True + 免费Key缺失 -> 默认
_ENV = {
    "PREFER_CLASSIFY_MODEL": "deepseek-v4-flash",
    "PREFER_FREE_CLASSIFY_MODEL": "MISSING_KEY",
}
c = mod.decide_classify_model(True)
check("分类免费Key缺失时回退默认", "deepseek" in c["raw_model"])

# 10. 分类路由：use_free=True + 免费未配置 -> 默认
_ENV = {"PREFER_CLASSIFY_MODEL": "deepseek-v4-flash"}
c = mod.decide_classify_model(True)
check("分类免费未配置时走默认", "deepseek" in c["raw_model"])

print(f"\nSUMMARY: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
