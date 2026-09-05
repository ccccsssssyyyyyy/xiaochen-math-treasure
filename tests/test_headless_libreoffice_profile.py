"""mathbank.headless_libreoffice_profile 单元测试。

重点验证:
1. 项目独立 LibreOffice profile 的 registrymodifications.xcu 格式合法、幂等创建;
2. ``build_soffice_command`` 始终将 ``-env:UserInstallation`` 插入到命令行首位;
3. 全局 xcu 写入函数在配置目录不存在时安全降级为 no-op,不抛异常。

端到端效果(成都七中 docx 转 PDF 后中文空白消失)由 ``scripts/verify_docx_font_fallback.py``
手工验证,不放在 pytest 里(避免引入 LibreOffice 启动耗时与文件系统副作用)。
"""

import sys
from pathlib import Path

import pytest

from mathbank.headless_libreoffice_profile import (
    _FONT_PAIR_DEFINITIONS,
    _PROFILE_MARKER,
    _serialize_registrymodifications_xml,
    _xml_escape,
    build_soffice_command,
    ensure_profile_xcu,
    find_soffice,
    get_profile_user_dir,
    get_registry_xcu_path,
    get_user_installation_url,
    install_global_libreoffice_fallback,
)


class TestSerializeRegistrymodifications:
    def test_xcu_well_formed_xml(self):
        xml = _serialize_registrymodifications_xml(_FONT_PAIR_DEFINITIONS)
        assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        assert "<oor:items" in xml
        assert "</oor:items>" in xml

    def test_xcu_contains_marker_and_pairs(self):
        xml = _serialize_registrymodifications_xml(_FONT_PAIR_DEFINITIONS)
        assert _PROFILE_MARKER in xml
        assert '<prop oor:name="Replacement" oor:op="fuse"><value>true</value></prop>' in xml
        assert '<prop oor:name="AlwaysReplace" oor:op="fuse"><value>true</value></prop>' in xml
        for idx, (replace_font, substitute_font) in enumerate(_FONT_PAIR_DEFINITIONS, 1):
            assert f'oor:path="/org.openoffice.Office.Common/Font/Substitution/FontPairs_{idx}"' in xml
            assert f'<value>{_xml_escape(replace_font)}</value>' in xml
            assert f'<value>{_xml_escape(substitute_font)}</value>' in xml

    def test_xcu_pair_count_matches_definitions(self):
        xml = _serialize_registrymodifications_xml(_FONT_PAIR_DEFINITIONS)
        pair_paths = [
            line for line in xml.splitlines()
            if '/Font/Substitution/FontPairs_' in line
        ]
        assert len(pair_paths) == len(_FONT_PAIR_DEFINITIONS)


class TestProfileLifecycle:
    def test_profile_user_dir_created(self):
        d = get_profile_user_dir()
        assert d.exists()
        assert d.name == "user"
        assert "libreoffice-profile" in str(d)

    def test_ensure_profile_xcu_idempotent(self, tmp_path):
        # 临时换一个 profile 目录,避免污染真实项目路径
        import mathbank.headless_libreoffice_profile as profile_mod
        original_dir = profile_mod._SYSTEM_GENERATED_DIRNAME
        try:
            profile_mod._SYSTEM_GENERATED_DIRNAME = str(tmp_path / "system")
            # 重置缓存路径函数没有状态;get_profile_user_dir 直接重新构造
            first = ensure_profile_xcu(force=True)
            second = ensure_profile_xcu(force=False)
            assert first == second
            text = first.read_text(encoding="utf-8")
            assert text.count(_PROFILE_MARKER) == 1
        finally:
            profile_mod._SYSTEM_GENERATED_DIRNAME = original_dir

    def test_user_installation_url_is_file_scheme(self):
        url = get_user_installation_url()
        assert url.startswith("file://")
        assert "/libreoffice-profile/user" in url


class TestBuildSofficeCommand:
    def test_env_user_installation_is_first_arg(self):
        cmd = build_soffice_command("/usr/bin/soffice", "--headless", "--convert-to", "pdf")
        assert cmd[0] == "/usr/bin/soffice"
        assert cmd[1].startswith("-env:UserInstallation=file://")
        assert cmd[2] == "--headless"

    def test_xcu_path_exists_after_build(self):
        cmd = build_soffice_command("/usr/bin/soffice")
        xcu_path = get_registry_xcu_path()
        assert xcu_path.exists()
        assert _PROFILE_MARKER in xcu_path.read_text(encoding="utf-8")


class TestFindSoffice:
    def test_find_soffice_returns_none_or_path(self):
        soffice = find_soffice()
        assert soffice is None or isinstance(soffice, str)
        if soffice:
            assert Path(soffice).exists()

    def test_find_soffice_prefers_macos_app(self):
        if sys.platform != "darwin":
            pytest.skip("仅 macOS 平台测试")
        soffice = find_soffice()
        if soffice:
            assert "/Applications/LibreOffice.app/Contents/MacOS/soffice" in soffice


class TestGlobalFallback:
    def test_install_global_no_crash_when_profile_missing(self, tmp_path):
        # 通过 monkeypatch 让全局候选目录指向一个不存在的位置
        import mathbank.headless_libreoffice_profile as profile_mod
        original_candidates = profile_mod._GLOBAL_PROFILE_CANDIDATES
        try:
            profile_mod._GLOBAL_PROFILE_CANDIDATES = (tmp_path / "no-such-libreoffice" / "user" / "registrymodifications.xcu",)
            result = install_global_libreoffice_fallback()
            assert result is None
        finally:
            profile_mod._GLOBAL_PROFILE_CANDIDATES = original_candidates

    def test_install_global_skips_when_marker_present(self, tmp_path):
        import mathbank.headless_libreoffice_profile as profile_mod
        xcu = tmp_path / "user" / "registrymodifications.xcu"
        xcu.parent.mkdir(parents=True)
        xcu.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">\n'
            f'<!-- {_PROFILE_MARKER} -->\n'
            '</oor:items>\n',
            encoding="utf-8",
        )
        original_candidates = profile_mod._GLOBAL_PROFILE_CANDIDATES
        try:
            profile_mod._GLOBAL_PROFILE_CANDIDATES = (xcu,)
            result = install_global_libreoffice_fallback()
            assert result == xcu
            # 不应重复追加
            assert xcu.read_text(encoding="utf-8").count(_PROFILE_MARKER) == 1
        finally:
            profile_mod._GLOBAL_PROFILE_CANDIDATES = original_candidates
