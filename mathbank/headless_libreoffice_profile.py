"""为 docx 转 PDF 链路提供项目独立的 LibreOffice headless profile。

背景
----
macOS LibreOffice 26 headless 把 docx 转 PDF 时,如果文档里的字体名
(SimSun / 宋体 / SimHei / 黑体 / Microsoft YaHei / 微软雅黑 / Arial Unicode MS)
在系统不存在,LibreOffice 会按其内置 fallback 链找到替代字体。Arial Unicode
MS 在部分 Unicode 扩展字符上字形缺失,导致原卷预览图出现"中文空白/方框"。

修复思路
--------
项目专属 LibreOffice profile,内置字体替换对,把 Windows 常见中文名映射到
macOS 自带且字形覆盖更全的字体(STHeiti Medium / Hiragino Sans GB)。
profile 通过 ``-env:UserInstallation=file://...`` 在主入口注入,不污染
用户的全局 ``~/Library/Application Support/LibreOffice/4/user/`` 配置。

与 mathbank.choice_recovery / free_model_routing 同属"零 token 成本、本地
确定"的兜底模块,只改 运行时 环境配置,不引入 AI 调用或网络请求。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 项目根下的 LibreOffice profile 容器(.system_generated/ 已在 .gitignore,不入库)
_SYSTEM_GENERATED_DIRNAME = ".system_generated"
_LIBREOFFICE_PROFILE_DIRNAME = "libreoffice-profile"
_USER_DIRNAME = "user"
_REGISTRY_XCU_FILENAME = "registrymodifications.xcu"

# 唯一识别哨兵:写入 xcu 的第一行 HTML/XML 注释。启动脚本幂等扫描时查找这个串
# 以判断"我之前是否已经写过了"。
_PROFILE_MARKER = "mathbank-font-fallback:v1"

# Windows 常见中文字体名 → macOS 自带、字形覆盖更全的字体
# Arial Unicode MS 单列是因为部分扩展字符字形缺失(而 SimSun 等基本汉字 OK,
# 一并替换是稳妥做法)。Microsoft YaHei 走 Hiragino Sans GB(苹方简体)是因
# Hiragino 字形几何更接近微软雅黑;STHeiti Medium 偏传统黑体,与之差距大。
_FONT_PAIR_DEFINITIONS: Tuple[Tuple[str, str], ...] = (
    ("SimSun", "STHeiti Medium"),
    ("宋体", "STHeiti Medium"),
    ("NSimSun", "STHeiti Medium"),
    ("新宋体", "STHeiti Medium"),
    ("SimHei", "STHeiti Medium"),
    ("黑体", "STHeiti Medium"),
    ("Arial Unicode MS", "STHeiti Medium"),
    ("Microsoft YaHei", "Hiragino Sans GB"),
    ("微软雅黑", "Hiragino Sans GB"),
    ("KaiTi", "STHeiti Medium"),
    ("楷体", "STHeiti Medium"),
)


def _resolve_project_root() -> Path:
    """返回项目根路径:mathbank/ 的父目录。"""
    return Path(__file__).resolve().parents[1]


def get_profile_user_dir() -> Path:
    """返回项目独立 LibreOffice profile 的 user/ 目录(不存在则创建)。"""
    project_root = _resolve_project_root()
    profile_user_dir = project_root / _SYSTEM_GENERATED_DIRNAME / _LIBREOFFICE_PROFILE_DIRNAME / _USER_DIRNAME
    profile_user_dir.mkdir(parents=True, exist_ok=True)
    return profile_user_dir


def get_registry_xcu_path() -> Path:
    """返回 ``user/registrymodifications.xcu`` 的绝对路径(不保证文件存在)。"""
    return get_profile_user_dir() / _REGISTRY_XCU_FILENAME


def _xml_escape(s: str) -> str:
    """XML 字符数据转义(用于 prop 文本)。"""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _serialize_registrymodifications_xml(font_pairs) -> str:
    """生成 profile 用的完整 ``registrymodifications.xcu`` 文本。

    内容包含两层:
    1. 启用 ``Font/Substitution`` 的全局开关 ``Replacement=true`` 与
       ``AlwaysReplace=true``(后者强制覆盖,即原字体存在也走替换);
    2. 每条 (原字体名 → 替换字体名) 一个 FontPairs_N 节点,Always=true,
       OnScreenOnly=true(只在屏显与导出时替换,不污染文档保存的原字体声明)。
    """
    items: list[str] = []
    items.append(
        '<item oor:path="/org.openoffice.Office.Common/Font/Substitution">'
        '<prop oor:name="Replacement" oor:op="fuse"><value>true</value></prop>'
        '</item>'
    )
    items.append(
        '<item oor:path="/org.openoffice.Office.Common/Font/Substitution">'
        '<prop oor:name="AlwaysReplace" oor:op="fuse"><value>true</value></prop>'
        '</item>'
    )
    for idx, (replace_font, substitute_font) in enumerate(font_pairs, 1):
        items.append(
            f'<item oor:path="/org.openoffice.Office.Common/Font/Substitution/FontPairs_{idx}">'
            f'<prop oor:name="ReplaceFont" oor:op="fuse"><value>{_xml_escape(replace_font)}</value></prop>'
            f'<prop oor:name="SubstituteFont" oor:op="fuse"><value>{_xml_escape(substitute_font)}</value></prop>'
            f'<prop oor:name="Always" oor:op="fuse"><value>true</value></prop>'
            f'<prop oor:name="OnScreenOnly" oor:op="fuse"><value>true</value></prop>'
            f'</item>'
        )
    body = "\n  ".join(items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        f'  <!-- {_PROFILE_MARKER} -->\n'
        f'  {body}\n'
        '</oor:items>\n'
    )


def ensure_profile_xcu(force: bool = False) -> Path:
    """确保 ``registrymodifications.xcu`` 存在且内容是 mathbank 字体替换表。

    - 不存在时写入完整 xcu。
    - 存在但不含 mathbank 哨兵时:**追加** 缺失条目(避免覆盖用户手动加的
      其他配置)。重复哨兵:跳过。
    - ``force=True`` 时直接覆盖(测试用)。
    """
    xcu_path = get_registry_xcu_path()
    xcu_path.parent.mkdir(parents=True, exist_ok=True)

    if xcu_path.exists() and not force:
        try:
            existing = xcu_path.read_text(encoding="utf-8")
        except Exception:
            existing = ""
        if _PROFILE_MARKER in existing:
            return xcu_path
        # 已存在但不含哨兵:在尾部追加新 entries(简单字符串拼接即可,
        # 我们的格式自闭合且 LibreOffice 允许多次 oor:items 段)。
        additional = []
        additional.append(
            f'<!-- {_PROFILE_MARKER} -->\n'
            '<item oor:path="/org.openoffice.Office.Common/Font/Substitution">'
            '<prop oor:name="Replacement" oor:op="fuse"><value>true</value></prop>'
            '</item>'
        )
        for idx, (replace_font, substitute_font) in enumerate(_FONT_PAIR_DEFINITIONS, 1):
            additional.append(
                f'<item oor:path="/org.openoffice.Office.Common/Font/Substitution/FontPairs_{idx}">'
                f'<prop oor:name="ReplaceFont" oor:op="fuse"><value>{_xml_escape(replace_font)}</value></prop>'
                f'<prop oor:name="SubstituteFont" oor:op="fuse"><value>{_xml_escape(substitute_font)}</value></prop>'
                f'<prop oor:name="Always" oor:op="fuse"><value>true</value></prop>'
                f'<prop oor:name="OnScreenOnly" oor:op="fuse"><value>true</value></prop>'
                f'</item>'
            )
        if "</oor:items>" not in existing:
            existing = existing.rstrip() + "\n" + "\n".join(additional) + "\n</oor:items>\n"
        else:
            existing = existing.replace(
                "</oor:items>", "\n" + "\n".join(additional) + "\n</oor:items>", 1
            )
        xcu_path.write_text(existing, encoding="utf-8")
        logger.info("已追加 mathbank 字体替换项到现有 registrymodifications.xcu")
        return xcu_path

    xcu_path.write_text(
        _serialize_registrymodifications_xml(_FONT_PAIR_DEFINITIONS),
        encoding="utf-8",
    )
    logger.info(f"已创建项目独立 LibreOffice profile: {xcu_path}")
    return xcu_path


def get_user_installation_url() -> str:
    """返回 ``-env:UserInstallation=file://...`` 参数的值。

    使用 ``file://`` 协议(绝对路径),LibreOffice 启动时按它解析 profile 根,
    user/ 子目录作为本次会话的 user-config 容器。
    """
    profile_user_dir = ensure_profile_xcu().parent
    # URL 规范:空格需转 %20,特殊字符按标准 RFC3986 编码
    abs_path = profile_user_dir.resolve()
    as_url = abs_path.as_uri()  # pathlib 提供,自带 file:// 前缀
    return as_url


def build_soffice_command(soffice_path: str, *args: str) -> list:
    """拼出 LibreOffice headless 命令行,把 ``-env:UserInstallation`` 插到首位。

    位置约束:必须放在所有开关参数(``--headless`` / ``--convert-to`` 等)之前,
    否则 LibreOffice 启动时已加载默认 profile,切换不生效。这是 LibreOffice
    文档明示的硬规则。
    """
    return [soffice_path, f"-env:UserInstallation={get_user_installation_url()}", *args]


def find_soffice() -> Optional[str]:
    """定位 LibreOffice 可执行路径(返回 ``None`` 表示未安装)。

    查找顺序与 main.py 旧实现保持一致,但返回 ``Optional`` 让调用方自己判断。
    """
    candidates = []
    if sys.platform == "darwin":
        candidates.append("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    elif os.name == "nt":
        candidates += [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
    else:
        candidates += [
            "/opt/homebrew/bin/soffice",
            "/usr/local/bin/soffice",
            "/usr/bin/soffice",
        ]
    for c in candidates:
        if Path(c).exists():
            return c
    return shutil.which("soffice")


__all__ = [
    "_FONT_PAIR_DEFINITIONS",
    "_PROFILE_MARKER",
    "build_soffice_command",
    "ensure_profile_xcu",
    "find_soffice",
    "get_profile_user_dir",
    "get_registry_xcu_path",
    "get_user_installation_url",
    "install_global_libreoffice_fallback",
]


# 平台全局 LibreOffice profile 路径(不同平台分别探测)
_GLOBAL_PROFILE_CANDIDATES = (
    # macOS(项目最常用平台)
    Path.home() / "Library" / "Application Support" / "LibreOffice" / "4" / _USER_DIRNAME / _REGISTRY_XCU_FILENAME,
    # Linux(常见于服务器/CI)
    Path.home() / ".config" / "libreoffice" / "4" / _USER_DIRNAME / _REGISTRY_XCU_FILENAME,
)


def _discover_global_xcu_path() -> Optional[Path]:
    """探测本机**全局** LibreOffice xcu 路径(只关心已存在的那一个)。

    不强制创建(避免在用户的 Linux 服务器上凭空创建 ~/.config 目录):
    如果 LibreOffice 没装过,返回 ``None``——这种情况下 ``install_global_libreoffice_fallback``
    也走 no-op,只对**已跑过 LibreOffice** 的本机生效。
    """
    for candidate in _GLOBAL_PROFILE_CANDIDATES:
        if candidate.parent.exists():
            return candidate
    return None


def install_global_libreoffice_fallback() -> Optional[Path]:
    """幂等把 mathbank 字体替换表写入**用户全局** LibreOffice xcu(用作 fallback)。

    与 :func:`ensure_profile_xcu` 的差别:

    - :func:`ensure_profile_xcu` 写到项目下的独立 profile,只对项目**主动调用**
      的 soffice 命令起作用(必须通过 :func:`build_soffice_command` 注入
      ``-env:UserInstallation``)。完全隔离,零污染。
    - 本函数写到用户级 LibreOffice 配置,作用范围更大:用户**绕过项目代码**
      直接用 LibreOffice 转 PDF 时也会带上替换规则——是兜底层。

    幂等保证:
    - 已含 ``_PROFILE_MARKER`` 标记 → no-op,返回现有路径;
    - 不存在或不含标记 → 仅追加 mathbank 字体替换项,不覆盖任何用户既有项。
    """
    xcu_path = _discover_global_xcu_path()
    if xcu_path is None:
        logger.info("未发现全局 LibreOffice profile;跳过全局 fallback 配置")
        return None
    existing_text = ""
    if xcu_path.exists():
        try:
            existing_text = xcu_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning(f"读取 {xcu_path} 失败(权限不足?): {exc};跳过")
            return None
    if _PROFILE_MARKER in existing_text:
        return xcu_path

    # 复制既有内容、追加 mathbank 项、收尾闭合
    additional = []
    additional.append(
        f'<!-- {_PROFILE_MARKER} -->\n'
        '<item oor:path="/org.openoffice.Office.Common/Font/Substitution">'
        '<prop oor:name="Replacement" oor:op="fuse"><value>true</value></prop>'
        '</item>'
    )
    for idx, (replace_font, substitute_font) in enumerate(_FONT_PAIR_DEFINITIONS, 1):
        additional.append(
            f'<item oor:path="/org.openoffice.Office.Common/Font/Substitution/FontPairs_{idx}">'
            f'<prop oor:name="ReplaceFont" oor:op="fuse"><value>{_xml_escape(replace_font)}</value></prop>'
            f'<prop oor:name="SubstituteFont" oor:op="fuse"><value>{_xml_escape(substitute_font)}</value></prop>'
            f'<prop oor:name="Always" oor:op="fuse"><value>true</value></prop>'
            f'<prop oor:name="OnScreenOnly" oor:op="fuse"><value>true</value></prop>'
            f'</item>'
        )
    if "</oor:items>" in existing_text:
        new_text = existing_text.replace(
            "</oor:items>", "\n" + "\n".join(additional) + "\n</oor:items>", 1
        )
    else:
        # 残留/损坏 xcu:重写为标准骨架并带上我们项;这种情况很少见,打印警告。
        logger.warning(f"{xcu_path} 缺少 </oor:items> 闭合,正在重建")
        new_text = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
            'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
            + existing_text.rstrip()
            + "\n"
            + "\n".join(additional)
            + "\n</oor:items>\n"
        )
    try:
        xcu_path.write_text(new_text, encoding="utf-8")
    except Exception as exc:
        logger.warning(f"写入 {xcu_path} 失败(权限不足?): {exc};跳过")
        return None
    logger.info(f"已在全局 LibreOffice profile 追加 mathbank 字体替换: {xcu_path}")
    return xcu_path
