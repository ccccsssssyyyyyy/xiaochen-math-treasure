"""设置 →「备份与还原」面板的契约测试（修复 4）。

背景：审查报告说「没有任何还原接口」，这句不准确——`restore_full_backup()` /
`verify_full_backup()` 早就躺在 `mathbank/backup.py` 里，`scripts/restore.py`
也包好了 CLI。真正缺的是「HTTP 端点 + 界面」：界面既列不出快照、也不显示上次备份
时间，用户手里有没有退路完全看不出来。

补这件事时撞上一个硬约束：题库服务启动时持有 runtime lock（`main.py`），而
`restore_full_backup()` 内部要拿同一把锁（`backup.py`）。flock 是「每个打开文件
描述」级别的，同进程再开一个 fd 也会撞 → 服务运行中在线上换库在技术上做不到，
除非拆掉那道锁。而那道锁挡的正是「服务一边写、库一边被整个换掉」这种会静默毁数据
的动作，所以没有拆，改成「延迟还原」：界面只登记请求 + 为当前库做一份完整备份，
真正的替换发生在题库下次启动时（`apply_pending_restore()`）。

本文件钉住四层不变量：

1. 运行期行为（真跑 backup.js）：列表渲染、显式选中、二次确认、失败归还按钮、
   断网不卡死、转义 —— 见 `js/backup_panel_check.js`，本文件只负责把它拉起来；
2. 结构：新 tab 真的并进了设置弹窗，且接线完整（三个硬编码白名单都不能漏）；
3. 安全边界：还原请求必须带二次确认；文件名不得穿越快照目录；服务运行中不许
   直接换库（在线端点里不能出现 restore_full_backup 调用）；
4. 退路可查：还原前必须先给当前库做备份，否则一次误操作就没有回头路。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_JS = PROJECT_ROOT / "static" / "js" / "backup.js"
API_JS = PROJECT_ROOT / "static" / "js" / "api.js"
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"
MAIN_PY = PROJECT_ROOT / "main.py"
BACKUP_PY = PROJECT_ROOT / "mathbank" / "backup.py"
PANEL_CHECK_JS = Path(__file__).resolve().parent / "js" / "backup_panel_check.js"
SETTINGS_TAB_CHECK_JS = Path(__file__).resolve().parent / "js" / "settings_tab_backup_check.js"


@pytest.fixture(scope="module")
def js():
    return BACKUP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def api():
    return API_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def html():
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def main_source():
    return MAIN_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def backup_source():
    return BACKUP_PY.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 1. 运行期行为
# --------------------------------------------------------------------------

def test_backup_js_passes_node_syntax_check():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端语法校验")
    result = subprocess.run([node, "--check", str(BACKUP_JS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_backup_panel_behaviour_runtime():
    """在 vm 沙箱里加载真实的 backup.js，跑通面板的完整生命周期。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端实跑校验")
    result = subprocess.run(
        [node, str(PANEL_CHECK_JS), str(BACKUP_JS)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        "备份面板前端实跑未通过:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "全部通过" in result.stdout, result.stdout


def test_frontend_has_no_regex_lookbehind(js):
    """项目禁用正则 lookbehind（Safari 旧版本会直接抛语法错误）。"""
    assert not re.search(r"\(\?<[=!]", js), "backup.js 里出现了 lookbehind"


def test_settings_tab_wiring_runtime():
    """把 api.js 里真实的 switchSettingsTab 抽出来，对着 index.html 的真实 id 跑一遍。

    硬编码白名单最怕两件事：id 拼错、漏进复位列表 —— 两者都不报错，只表现为「点了
    没反应」或「切走以后面板还留在屏幕上」。静态断言证明不了这些 id 真的存在于 HTML，
    所以这里实跑一次，并把「api.js 查了但 HTML 里没有的 id」直接判为失败。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端实跑校验")
    result = subprocess.run(
        [node, str(SETTINGS_TAB_CHECK_JS), str(API_JS), str(INDEX_HTML)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "设置 tab 接线实跑未通过:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "全部通过" in result.stdout, result.stdout


# --------------------------------------------------------------------------
# 2. 结构：新 tab 并进设置弹窗 + 接线完整
# --------------------------------------------------------------------------

def test_backup_tab_button_exists_next_to_the_other_tabs(html):
    assert 'id="btn-settings-backup"' in html
    assert "switchSettingsTab('backup')" in html
    # 必须和另外四个 tab 同处一排，否则会掉到弹窗外面
    anchor = html.index('id="btn-settings-free"')
    region = html[anchor - 2000: anchor + 2000]
    assert 'id="btn-settings-backup"' in region


def test_backup_panel_is_the_last_settings_tab(html):
    about_end = html.index("<!-- End of settings-tab-about -->")
    backup_start = html.index('id="settings-tab-backup"')
    scroll_end = html.index("<!-- End of Scrollable Settings Body -->")
    assert about_end < backup_start < scroll_end, \
        "备份面板没有接在设置面板体内，可能掉出了滚动区"


def test_backup_js_is_loaded_after_api_js(html):
    """面板渲染要用 window.MathBankSafe 转义，必须排在 api.js 之后。"""
    api_idx = html.index('src="/static/js/api.js')
    backup_idx = html.index('src="/static/js/backup.js')
    assert api_idx < backup_idx


def test_switch_settings_tab_wires_the_new_tab(api):
    """switchSettingsTab 是硬编码白名单：三处都不能漏，否则点了没反应。"""
    # api.js 里写的是 function(tabName)，function 与括号之间没有空格
    fn = re.search(r"window\.switchSettingsTab = function\s*\(tabName\) \{[\s\S]*?\n        \};", api)
    assert fn, "找不到 switchSettingsTab"
    body = fn.group(0)
    assert "const btnBackup = document.getElementById('btn-settings-backup');" in body
    assert "const tabBackup = document.getElementById('settings-tab-backup');" in body
    assert "[btnApi, btnMeta, btnAbout, btnFree, btnBackup].forEach" in body, \
        "新 tab 按钮没进复位列表：切走以后它的高亮不会取消"
    assert "[tabApi, tabMeta, tabAbout, tabFree, tabBackup].forEach" in body, \
        "新 tab 面板没进隐藏列表：切走以后它还会留在屏幕上"
    assert "tabName === 'backup'" in body, "没有 backup 分支，点 tab 不会显示面板"
    assert "window.loadBackupPanel" in body, "切到该 tab 时没有触发加载"


def test_save_button_is_hidden_on_backup_tab(api):
    """这个 tab 没有可保存的配置项，[保存配置] 留着只会让人误点。"""
    # api.js 里写的是 function(tabName)，function 与括号之间没有空格
    fn = re.search(r"window\.switchSettingsTab = function\s*\(tabName\) \{[\s\S]*?\n        \};", api)
    branch = re.search(r"tabName === 'backup'\) \{[\s\S]*?\n            \}", fn.group(0))
    assert branch, "找不到 backup 分支"
    assert "btnSave.classList.add('hidden')" in branch.group(0)


def test_every_inline_onclick_in_the_panel_is_exported(js, html):
    """index.html 里的 onclick="xxx()" 靠全局查找，没导出就抛 ReferenceError。"""
    panel = html[html.index('id="settings-tab-backup"'): html.index("<!-- End of settings-tab-backup -->")]
    modal = html[html.index('id="backupRestoreConfirmModal"'):]
    calls = set(re.findall(r'onclick="([A-Za-z_][A-Za-z0-9_]*)\(\)"', panel + modal[:4000]))
    assert calls, "面板里应该有内联 onclick，正则或写法可能已变"
    for fn in calls:
        assert f"window.{fn} =" in js, (
            f"内联 onclick 调用的 {fn} 没有导出到 window，点了会报 ReferenceError"
        )


def test_endpoint_paths_are_covered_by_the_local_token_patch(js, api):
    """写接口全靠 api.js 的 fetch 补丁自动补 X-Local-Token。

    补丁的条件是「同源 + 路径以 /api/ 开头 + 方法属于 POST/PUT/PATCH/DELETE」。
    端点路径一旦写成别的形式（相对路径、带完整 origin、漏掉 /api 前缀），请求就不会
    带令牌，被 main.py 的中间件挡成 403 —— 界面上只表现为「点了没反应」，很难查。
    """
    endpoints = re.findall(r"const ENDPOINT_[A-Z_]+ = '([^']+)'", js)
    assert len(endpoints) >= 4, f"端点常量变少了：{endpoints}"
    for path in endpoints:
        assert path.startswith("/api/"), f"{path} 不在令牌补丁的覆盖范围内"
    assert "/api/backup/restore" in endpoints

    patch = re.search(r"window\.fetch = async function[\s\S]*?\n            \};", api)
    assert patch, "api.js 里找不到 fetch 补丁"
    body = patch.group(0)
    assert "startsWith('/api/')" in body, "补丁的路径判定变了，需要重新确认覆盖范围"
    assert "['POST', 'PUT', 'PATCH', 'DELETE']" in body, "补丁覆盖的写方法变了"
    assert "X-Local-Token" in body


# --------------------------------------------------------------------------
# 3. 安全边界
# --------------------------------------------------------------------------

def test_restore_endpoint_requires_explicit_confirmation(main_source):
    """还原是破坏性操作：客户端必须显式带 confirm:true，缺了就拒绝。"""
    block = re.search(r'@app\.post\("/api/backup/restore"\)[\s\S]*?\n@app\.', main_source)
    assert block, "main.py 里找不到 /api/backup/restore"
    body = block.group(0)
    assert 'data.get("confirm") is not True' in body, \
        "二次确认被去掉了，一个误触就能登记换库"
    assert "400" in body


def test_live_service_never_replaces_the_database_in_place(main_source):
    """服务运行中不许直接换库 —— 必须走「登记请求 + 重启时落地」。"""
    block = re.search(r'@app\.post\("/api/backup/restore"\)[\s\S]*?\n@app\.', main_source)
    body = block.group(0)
    assert "restore_full_backup(" not in body, \
        "在线端点直接调了 restore_full_backup：它会撞 runtime lock，且等于允许边写边换库"
    assert "write_pending_restore(" in body, "没有登记待还原请求"
    assert "create_pre_restore_backup(" in body, "登记前没有为当前库留退路"


def test_pre_restore_backup_failure_aborts_the_request(main_source):
    """备份不出来就不许进入还原：否则误操作之后无处可回。"""
    block = re.search(r'@app\.post\("/api/backup/restore"\)[\s\S]*?\n@app\.', main_source)
    body = block.group(0)
    backup_idx = body.index("create_pre_restore_backup(")
    pending_idx = body.index("write_pending_restore(")
    assert backup_idx < pending_idx, "先登记了请求才做安全备份，顺序反了"
    assert "已中止" in body or "500" in body, "安全备份失败时没有中止请求"


def test_snapshot_name_cannot_escape_the_backup_directory(backup_source):
    """外部传来的文件名必须过白名单正则 + 解析后父目录校验。"""
    fn = re.search(r"def resolve_full_backup\([\s\S]*?\n\n\ndef ", backup_source)
    assert fn, "找不到 resolve_full_backup"
    body = fn.group(0)
    assert "_FULL_BACKUP_NAME.match(name)" in body, "没有按白名单正则校验文件名"
    assert "candidate.parent != directory" in body, "没有校验解析后的父目录，存在路径穿越风险"


def test_restore_cli_still_requires_explicit_apply(backup_source, main_source):
    """CLI 通道保留为排障出口，且同样需要显式 --apply。"""
    cli = (PROJECT_ROOT / "scripts" / "restore.py")
    assert cli.is_file(), "scripts/restore.py 不见了，排障通道断了"
    source = cli.read_text(encoding="utf-8")
    assert "--apply" in source and "--yes" in source, \
        "CLI 的两个确认开关被去掉，一条命令就能换库"


# --------------------------------------------------------------------------
# 4. 文案与后端行为一致
# --------------------------------------------------------------------------

def test_expiry_is_enforced_by_the_backend_not_just_the_ui(backup_source):
    """界面写着「30 分钟内重启才生效」，过期判定必须真的在后端。"""
    assert "PENDING_RESTORE_TTL_SECONDS" in backup_source
    fn = re.search(r"def read_pending_restore\([\s\S]*?\n\n\ndef ", backup_source)
    assert fn, "找不到 read_pending_restore"
    body = fn.group(0)
    assert "expires_at <= (now or _utc_now())" in body, "没有做过期判定，界面承诺失效"
    assert "请求已过期" in body, "过期时没有清理请求文件"


def test_missing_snapshot_invalidates_the_request(backup_source):
    """目标快照被删/被轮转掉时请求要作废，否则每次重启都重放一次失败。"""
    assert "def apply_pending_restore(" in backup_source, "找不到 apply_pending_restore"
    # 它是 backup.py 的最后一个函数，后面没有下一个 def，只能切到文件末尾
    body = backup_source[backup_source.index("def apply_pending_restore("):]
    assert "目标快照已不可用" in body
    assert "还原失败，避免反复重试" in body
