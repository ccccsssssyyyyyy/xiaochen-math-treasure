"""首次启动引导 / 依赖引导 / 升级向导 的前端契约测试。

这些是"源码契约 + 真实 node 语法校验"型测试：前端没有构建步骤，改错了
很容易在浏览器里静默失效，所以把关键不变量钉在测试里。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"
ONBOARDING_JS = PROJECT_ROOT / "static" / "js" / "onboarding.js"


@pytest.fixture(scope="module")
def html():
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def onboarding():
    return ONBOARDING_JS.read_text(encoding="utf-8")


def test_onboarding_script_is_loaded_after_paper(html):
    assert '/static/js/onboarding.js' in html
    assert html.index('/static/js/onboarding.js') > html.index('/static/js/paper.js')


def test_index_exposes_onboarding_and_dependency_ui(html):
    for element_id in (
        'id="onboardingCard"',
        'id="onboardingKeyState"',
        'id="onboardingSampleBtn"',
        'id="onboardingSampleCount"',
        'id="depGuideModal"',
        'id="depStatusGrid"',
        'id="depSection-latex"',
        'id="depSection-soffice"',
        'id="depSection-pandoc"',
        'id="settingsEnvGrid"',
        'id="btnUpgradeBackup"',
    ):
        assert element_id in html, element_id


def test_index_wires_handlers_to_onboarding_module(html):
    for handler in (
        "openDepGuideModal('keys')",
        "openDepGuideModal('latex')",
        'onclick="importSampleQuestions()"',
        'onclick="dismissOnboarding(true)"',
        'onclick="runUpgradeBackup()"',
        'onclick="copyUpgradeChecklist(this)"',
        'onclick="resetOnboarding()"',
        'onclick="refreshDepStatus()"',
        'onclick="closeDepGuideModal()"',
    ):
        assert handler in html, handler


def test_update_modal_contains_guided_steps_and_backup(html):
    assert "升级向导：三步完成" in html
    assert "先做一次完整备份" in html
    assert "不要选「替换整个文件夹」" in html or "千万不要选「替换整个文件夹」" in html


def test_onboarding_module_exposes_expected_globals(onboarding):
    for symbol in (
        "window.refreshOnboarding",
        "window.dismissOnboarding",
        "window.resetOnboarding",
        "window.importSampleQuestions",
        "window.openDepGuideModal",
        "window.closeDepGuideModal",
        "window.toggleDepSection",
        "window.copyDepCommand",
        "window.refreshDepStatus",
        "window.refreshSettingsEnvGrid",
        "window.fetchEnvironmentStatus",
        "window.runUpgradeBackup",
        "window.copyUpgradeChecklist",
    ):
        assert symbol + " = " in onboarding, symbol


def test_onboarding_uses_the_real_api_routes(onboarding):
    assert "'/api/environment'" in onboarding
    assert "'/api/sample-questions'" in onboarding
    assert "'/api/sample-questions/import'" in onboarding
    assert "'/api/backup'" in onboarding


def test_onboarding_never_deletes_or_replaces_user_data(onboarding):
    """引导模块只做只读探测与用户主动触发的备份，不得出现破坏性调用。"""
    for forbidden in ("/api/questions/delete", "method: 'DELETE'", 'method: "DELETE"'):
        assert forbidden not in onboarding


def test_no_upstream_links_left_in_frontend():
    stale = "JudgePeach/math-question-bank"
    for path in (INDEX_HTML, PROJECT_ROOT / "static" / "js" / "api.js"):
        assert stale not in path.read_text(encoding="utf-8"), path.name


def test_onboarding_js_passes_node_syntax_check():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端语法校验")
    result = subprocess.run(
        [node, "--check", str(ONBOARDING_JS)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
