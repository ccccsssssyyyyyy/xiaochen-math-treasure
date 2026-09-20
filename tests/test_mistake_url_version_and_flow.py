"""块图 URL 版本号 + 错题流程的三条新契约（2026-09-15）。

背景：`_mistake_block_url()` 为修「同名图被浏览器复用」把 ``?v=<mtime_ns>`` 挂上
URL，又把这个带版本号的 URL **直接写进了** ``mistake_records.image_block``。而资产
安全层明确拒绝含 ``?`` 的引用，于是识别取图 100% 失败、整批标 ``failed``、题面全空
（排查见 .workbuddy/memory/2026-09-15.md 第六轮）。

这里钉住三件事：

1. **版本指纹只出现在出口**：入库存裸路径、下发时才挂；历史脏数据有兼容层；
2. **题卡只管选题**：编辑题面 / 重新识别 / 补图 / 单题入库都不在卡片上；
3. **进审校页自动识别**：只跑「题面为空且没失败过」的题，失败的留给手动重试。
"""

import re
from pathlib import Path

import pytest

from mathbank import db_migrations

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"
MISTAKE_JS = PROJECT_ROOT / "static" / "js" / "mistake.js"
MAIN_PY = PROJECT_ROOT / "main.py"
MIGRATIONS_PY = PROJECT_ROOT / "mathbank" / "db_migrations.py"


@pytest.fixture(scope="module")
def html():
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js():
    return MISTAKE_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def main_source():
    return MAIN_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def migrations_source():
    return MIGRATIONS_PY.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    """粗略取一个顶层函数的函数体（从 def/function 到下一个同级 def/function）。"""

    pattern = re.compile(
        r"(?:^def |^    function )" + re.escape(name) + r"\b.*?(?=\n(?:def |    function )|\Z)",
        re.S | re.M,
    )
    match = pattern.search(source)
    return match.group(0) if match else ""


# ---------------------------------------------------------------- 1) URL 版本号


def test_block_url_version_is_not_persisted(main_source):
    """写进库的必须是裸 URL：带 ?v= 的引用会被资产安全层拒绝。"""

    assert main_source.count("image_block=_mistake_block_web_url(") == 2, \
        "两处重建记录（合并 / 重切）都该存裸路径"
    assert "_mistake_block_url(" not in main_source, \
        "旧的「入库存带版本号 URL」入口必须彻底消失，留着就会有人再调它"


def test_block_url_version_is_added_at_the_edge(main_source):
    """版本指纹只在对外下发时挂，且幂等。"""

    assert "def _version_stored_block_url(" in main_source
    assert 'bare = raw.split("?", 1)[0].split("#", 1)[0]' in main_source, \
        "对已经带过指纹的 URL 再挂一次不能变成两个 ?v="
    assert "def _mistake_record_client_payload(" in main_source
    # 两个下发点：批次详情、单条更新返回
    assert "_mistake_record_client_payload(record, batch_id)" in main_source
    assert "_mistake_record_client_payload(record, record.batch_id)" in main_source
    # 导出用途必须拿裸路径：那边是按磁盘路径找图，不是给浏览器看
    export_body = _function_body(main_source, "export_mistake_handout")
    assert "_version_stored_block_url" not in export_body, \
        "导出流程不能挂指纹"


def test_recognize_image_path_tolerates_legacy_dirty_rows(main_source):
    """取图函数要剥离 query（兼容历史脏数据），并把两类故障分开报。"""

    body = _function_body(main_source, "_mistake_record_image_path")
    assert body, "函数不该被改名"
    assert 'raw.split("?", 1)[0]' in body, "历史脏数据（带 ?v=）必须能取到图"
    assert "require_file=False" in body, \
        "require_file=True 会把「文件不存在」伪装成安全错误，两类故障就分不开了"
    assert "题块图不存在" in body and "安全校验拒绝" in body


def test_v1009_migration_cleans_block_urls(migrations_source):
    """已有批次要能被洗干净，否则修完新代码它们依旧识别不了。"""

    # 版本号随迁移链继续上推 —— 这条断言只保证「v1009 仍在版本链上」，
    # 硬编具体数字会在每次新增迁移时误红（1009 → 1010 红过一轮，1010 → 1012 又红一轮）。
    assert (
        f"LATEST_SCHEMA_VERSION = {db_migrations.LATEST_SCHEMA_VERSION}"
        in migrations_source
    )
    assert "def _strip_block_url_version_v1009(" in migrations_source
    assert "elif current == 1008:" in migrations_source
    assert "elif current == 1009:" in migrations_source, "v1009 → v1010 的步骤必须挂上，否则新库停在 1009"

    body = _function_body(migrations_source, "_strip_block_url_version_v1009")
    assert "block_images" in body, "合并组的成员图 URL 也要一起洗"
    assert "PRAGMA user_version=1009" in body, "迁移结束要把版本号推上去"


# ---------------------------------------------------------------- 2) 题卡只选题


def test_card_buttons_moved_out_of_the_card(js):
    """卡片上不再有那五个入口 —— 它们属于入库前审校与入库后维护。"""

    for handler in (
        'onclick="editMistakeContent(',
        'onclick="recognizeOneMistake(',
        'onclick="importOneMistake(',
        'onchange="uploadMistakeAsset(',
    ):
        assert handler not in js, f"题卡不该再挂 {handler}"

    # 卡片仍要保留选题必需的东西，别删过头
    for keep in ("toggleMistakeGrad(", "setMistakeRecordType(", "setMistakeInclude("):
        assert keep in js, f"选题要用的 {keep} 不该被一起删掉"


def test_review_page_owns_the_editing_entries(html, js):
    """审校页接管：重新识别 / 原卷框选补图形 / 补解析截图。"""

    assert 'id="mrRetryOneBtn"' in html
    assert 'onclick="recognizeCurrentReviewRecord()"' in html
    assert 'onclick="openMistakeCropModal(\'figure_images\')"' in html
    assert 'onclick="openMistakeCropModal(\'answer_images\')"' in html
    for name in ("recognizeCurrentReviewRecord", "openMistakeCropModal"):
        assert "function " + name + "(" in js
        assert "window." + name + " = " + name + ";" in js, "静态内联 handler 必须挂 window"


def test_recognize_retry_hint_points_at_review_page(js):
    """那条会指路的提示文案不能再把人指回卡片。"""

    assert "可在卡片上点「重新识别」" not in js
    assert "可在审校页点「重新识别这道」重试" in js


# ---------------------------------------------------------------- 3) 自动识别


def test_review_page_auto_starts_recognition(js):
    """进审校页就自动补识别 —— 识别是工具的活，不是用户的步骤。"""

    assert "function autoRecognizeOnReviewOpen()" in js
    body = _function_body(js, "openMistakeReview")
    assert body, "openMistakeReview 不该被改名"
    assert "autoRecognizeOnReviewOpen();" in body, "打开审校页要真的调用它"


def test_auto_recognize_skips_failed_and_running(js):
    """失败的不自动重跑（省额度），已经在跑的不重复发起（否则抢同一批记录）。"""

    body = _function_body(js, "autoRecognizeOnReviewOpen")
    assert body
    assert "state.recognizeLive.active" in body and "return null" in body
    assert "!== 'failed'" in body, "failed 的题不该进自动识别的队列"


def test_review_gate_surfaces_recognize_failure(js):
    """失败原因要浮到审校页：任务条在批次详情页，人站在这边看不到。"""

    assert "state.recognizeLive.errors" in js
    assert "failNote" in js
    body = _function_body(js, "updateReviewButtons")
    assert "failNote" in body and "emptyNote" in body, \
        "门禁条要同时承载「题面为空」与「上次识别失败」两种提示"


# ------------------------------------------------- 4) 原卷框选补图（2026-09-15）


def test_crop_figure_endpoint_only_accepts_normalized_boxes(main_source):
    """裁图端点在，且只收 0–1 归一化框选、拒绝空框与超小框。"""

    assert '@app.post("/api/mistakes/batches/{batch_id}/pages/{page_no}/crop-figure")' in main_source
    body = _function_body(main_source, "crop_mistake_page_figure")
    assert "crop_region(" in body, "复用切块同一套裁图实现，别自己再写一份 PIL 裁剪"
    assert "0.0 <= xmin < xmax <= 1.0" in body
    assert "框选区域太小了" in body, "手滑点一下会框出 2px 的框，必须挡掉"
    assert "figures" in body, "人工补的图不能落进 blocks/（重切时会被整体重置）"
    assert '_mistake_web_url(batch_id, "figures", crop_name)' in body
    # 产物文件名带随机后缀 → 不存在「同名覆盖 → 浏览器复用旧位图」，因此出口也不该挂指纹；
    # 更不能把 ?v= 的 URL 写进记录（第六轮那个坑）。
    assert "_version_stored_block_url(" not in body
    assert "_mistake_block_web_url(" not in body


def test_page_url_carries_fingerprint_on_the_way_out(main_source):
    """页图是同名覆盖：出口要挂指纹，否则框选看到的是上一版页面。"""

    assert "def _page_web_url(batch_id: int, page_name: str) -> str:" in main_source
    assert '"url": _page_web_url(batch_id, page_file.name),' in main_source
    helper = _function_body(main_source, "_page_web_url")
    assert 'f"{bare}?v={stamp}"' in helper


def test_local_file_picker_is_gone_from_the_review_page(html, js):
    """「补图形 / 补解析截图」不再走文件选择器，本地选图入口整体撤掉。"""

    assert "uploadCurrentReviewAsset" not in html
    assert "uploadCurrentReviewAsset" not in js
    assert "uploadMistakeAsset" not in js
    # 审校页工具条（「重新识别这道」到题面输入框之间）里不该再有 file input。
    # 页面别处还有历史遗留的 file input，所以按区域卡，不做全局断言。
    toolbar = html.split('id="mrRetryOneBtn"')[1].split('id="mrContent"')[0]
    assert '<input type="file"' not in toolbar
    for label in ("figure_images", "answer_images"):
        assert f"openMistakeCropModal('{label}')" in html, f"{label} 的入口按钮不在"


def test_single_record_import_wrapper_is_gone_but_the_endpoint_stays(html, js, main_source):
    """「单题入库」的前端包装器是死代码，但后端端点仍被撞车强制入库用着。

    这两条必须一起断言：只删前端、不删后端。撞车时用户是**逐题**决定「跳过 / 仍入库」
    的（``askDuplicatesSequentially``），批量端点那个 ``force`` 是整批通吃
    （``main.py`` 把同一个 force 传给每一条），替代不了单题端点。
    """

    assert "function importOneMistake(" not in js
    assert "window.importOneMistake" not in js
    assert "importOneMistake(" not in html

    assert '@app.post("/api/mistakes/records/{record_id}/import-to-bank")' in main_source, \
        "端点还被 askDuplicatesSequentially 调用，不能删"
    assert "function askDuplicatesSequentially(" in js, "撞车强制入库路径不能丢"
    assert "'/api/mistakes/records/' + item.record_id + '/import-to-bank'" in js


def test_crop_modal_handlers_are_exported(html, js):
    """框选弹窗的静态内联 handler 必须挂 window，否则点了没反应。"""

    assert 'id="mrCropModal"' in html
    for name in (
        "openMistakeCropModal",
        "closeMistakeCropModal",
        "mrCropGotoPage",
        "mrCropStepPage",
        "zoomMrCropIn",
        "zoomMrCropOut",
        "resetMrCropZoom",
        "clearMrCropSelection",
        "submitMrCrop",
        "removeMistakeFigure",
        "removeInlineMistakeImage",
        "openMistakeCropModalFromPlaceholder",
        "clearMistakePlaceholders",
    ):
        assert "function " + name + "(" in js, f"缺少 {name}"
        assert "window." + name + " = " + name + ";" in js, f"{name} 必须挂 window"


def test_placeholder_becomes_a_static_marker(js):
    """未补的占位符在预览里是**静态标记**，不再可点（2026-09-18）。

    预览只渲染，不改写正文文本 —— 正文一旦被预览层改写，导出链
    （``clean_content_for_latex``）就还原不出图片的位置，那正是这一轮改成「图写进正文」
    的前提。补图入口已搬到源码编辑区，见
    ``test_crop_insert_position.py::test_source_editor_placeholder_is_the_way_in``。
    """

    body = _function_body(js, "contentPreviewHtml")
    assert "figures" in body
    assert "figureList[figureCursor]" in body, "老配图仍按出现顺序配对，序号要对得上"
    assert "mrPlaceholderChipHtml(label)" in body
    assert "插图待补" in body, "没配图时要显示标记，不能凭空长图"
    assert "openMistakeCropModalFromPlaceholder" not in body, "补图入口已搬去源码区"

    chip = _function_body(js, "mrPlaceholderChipHtml")
    assert "openMistakeCropModalFromPlaceholder(" not in chip, "预览标记不可点"


def test_review_inline_image_is_rendered_and_removable(js):
    """框选补进来的图写在正文里（markdown 图片），预览要渲染出来并且能撤掉。"""

    body = _function_body(js, "contentPreviewHtml")
    assert "![插图](" in body or "inlineTarget" in body
    assert "removeInlineMistakeImage(" in body
