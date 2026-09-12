"""Release guards for the macOS portable runtime (2.2.1+).

macOS packages used to rely on a host Python and a locally built venv.  They now
ship a pinned portable CPython, pruned to a reviewed minimum, so the release
build has to prove that the runtime really is self-contained and that nothing in
it trips the shared release allowlist.
"""

import io
import os
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest

from scripts import build_release


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_NAME = "启动题库系统.command"
MACHO_MAGIC = 0xFEEDFACF
# Deletions inside the project are rerouted to the macOS Trash by the sandboxed
# dev environment, and the Trash refuses .DS_Store; /tmp stays native.
SCRATCH_PARENT = "/tmp" if os.path.isdir("/tmp") and os.access("/tmp", os.W_OK) else None


def _write_tar(path, entries):
    """Build a tar.gz from (name, payload, mode, kind) tuples."""
    with tarfile.open(path, "w:gz") as archive:
        for name, payload, mode, kind in entries:
            info = tarfile.TarInfo(name)
            info.mode = mode
            if kind == "sym":
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                archive.addfile(info)
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                data = payload.encode("utf-8")
                info.size = len(data)
                info.type = tarfile.REGTYPE
                archive.addfile(info, io.BytesIO(data))


def _macho_header(cpu_type):
    return struct.pack("<I", MACHO_MAGIC) + struct.pack("<i", cpu_type) + b"\x00" * 8


def _make_macos_runtime_tree(root, architecture="intel", cpu_type=None):
    """Stage the runtime files the smoke check insists on.

    The default target is Intel so the smoke check never tries to execute the
    stub binary, which is only possible on a matching host.
    """
    build = build_release.MACOS_RUNTIME_BUILDS[architecture]
    runtime = Path(root) / "python"
    binary = runtime / "bin" / "python3.12"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(_macho_header(build["cpu_type"] if cpu_type is None else cpu_type))
    runtime.joinpath("RUNTIME-ARCH.txt").write_text(
        build["host_machine"] + "\n", encoding="utf-8"
    )
    site_packages = Path(root).joinpath(
        *build_release.MACOS_RUNTIME_SITE_PACKAGES.split("/")
    )
    for name in build_release.MACOS_RUNTIME_REQUIRED_SITE_PACKAGES:
        (site_packages / name).mkdir(parents=True, exist_ok=True)
    return build


def _make_minimal_macos_app_tree(root):
    for relative_path in (
        "main.py",
        "requirements.txt",
        "覆盖升级说明.txt",
        "mathbank/__init__.py",
        "scripts/release_overlay.py",
        "static/index.html",
        "static/uploads/.gitkeep",
    ):
        path = Path(root, relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("safe\n", encoding="utf-8")
    launcher = Path(root, LAUNCHER_NAME)
    launcher.write_text("safe\n", encoding="utf-8")
    launcher.chmod(0o755)


@pytest.fixture
def scratch_root():
    """A disposable root outside the project, where deletions stay local.

    The sandboxed dev environment reroutes in-project deletions into the macOS
    Trash and the Trash rejects `.DS_Store`, so pruning there would not exercise
    the same code path as a real build.  Under /tmp deletions are native.
    """
    root = Path(tempfile.mkdtemp(prefix="mathbank-prune-", dir=SCRATCH_PARENT))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_macos_runtime_pins_are_fixed_and_https():
    assert build_release.MACOS_RUNTIME_BASE_URL.startswith(
        "https://github.com/astral-sh/python-build-standalone/"
    )
    assert build_release.MACOS_RUNTIME_BASE_URL.endswith(
        build_release.MACOS_RUNTIME_RELEASE
    )
    assert set(build_release.MACOS_RUNTIME_BUILDS) == {"apple-silicon", "intel"}

    stems = set()
    for architecture, build in build_release.MACOS_RUNTIME_BUILDS.items():
        assert len(build["sha256"]) == 64
        int(build["sha256"], 16)
        assert build_release.MACOS_RUNTIME_RELEASE in build["asset"]
        assert build["asset"].startswith("cpython-")
        assert build["asset"].endswith("-apple-darwin-install_only.tar.gz")
        assert build["wheel_platforms"]
        assert all(
            platform_name.startswith("macosx_")
            for platform_name in build["wheel_platforms"]
        )
        stems.add(build["output_stem"])
    assert len(stems) == 2

    apple = build_release.MACOS_RUNTIME_BUILDS["apple-silicon"]
    assert "aarch64" in apple["asset"]
    assert apple["host_machine"] == "arm64"
    assert apple["cpu_type"] == 0x0100000C
    assert "arm64" in " ".join(apple["wheel_platforms"])

    intel = build_release.MACOS_RUNTIME_BUILDS["intel"]
    assert "x86_64" in intel["asset"]
    assert intel["host_machine"] == "x86_64"
    assert intel["cpu_type"] == 0x01000007
    assert "x86_64" in " ".join(intel["wheel_platforms"])


def test_release_output_names_cover_both_macos_packages():
    for build in build_release.MACOS_RUNTIME_BUILDS.values():
        assert f"{build['output_stem']}.zip" in build_release.RELEASE_OUTPUT_NAMES
        assert (
            f"{build['output_stem']}.zip.sha256" in build_release.RELEASE_OUTPUT_NAMES
        )
    # 2.2.0 的旧包名保留在清单里，避免 dist/ 里的陈旧产物被当成新构建结果。
    assert "MathBank-macOS.zip" in build_release.RELEASE_OUTPUT_NAMES


def test_macos_staging_roots_are_per_architecture_and_cleaned(tmp_path, monkeypatch):
    build_dirs = {
        architecture: tmp_path / f"staging-{architecture}"
        for architecture in build_release.MACOS_RUNTIME_BUILDS
    }
    for directory in build_dirs.values():
        directory.mkdir()
        (directory / "old").write_text("remove", encoding="utf-8")
    unrelated = tmp_path / "teacher-notes.txt"
    unrelated.write_text("keep", encoding="utf-8")
    windows_dir = tmp_path / "mathbank-windows"
    wheels_dir = tmp_path / "wheels"

    monkeypatch.setattr(build_release, "DIST_DIR", str(tmp_path))
    monkeypatch.setattr(build_release, "BUILD_DIR", str(windows_dir))
    monkeypatch.setattr(build_release, "WHEELS_DIR", str(wheels_dir))
    monkeypatch.setattr(
        build_release,
        "MACOS_BUILD_DIRS",
        {name: str(path) for name, path in build_dirs.items()},
    )
    monkeypatch.setattr(build_release, "PYTHON_DIR", str(windows_dir / "python"))
    monkeypatch.setattr(
        build_release, "SITE_PACKAGES", str(windows_dir / "python" / "site-packages")
    )

    build_release.clean_directories()

    assert unrelated.read_text(encoding="utf-8") == "keep"
    for directory in build_dirs.values():
        assert not (directory / "old").exists()


def test_validate_tar_gz_rejects_traversal_and_absolute_paths(tmp_path):
    for index, unsafe in enumerate(("../escape.txt", "/absolute.txt")):
        archive = tmp_path / f"unsafe-{index}.tar.gz"
        _write_tar(archive, [(unsafe, "payload", 0o644, "file")])
        with pytest.raises(RuntimeError, match="unsafe tar member path"):
            build_release.validate_tar_gz(archive, "unsafe runtime")


def test_validate_tar_gz_rejects_escaping_link_targets(tmp_path):
    archive = tmp_path / "linked.tar.gz"
    _write_tar(archive, [("python/bin/python3", "../../outside", 0o777, "sym")])
    with pytest.raises(RuntimeError, match="unsafe tar link target"):
        build_release.validate_tar_gz(archive, "linked runtime")


def test_validate_tar_gz_accepts_the_pinned_layout(tmp_path):
    archive = tmp_path / "ok.tar.gz"
    _write_tar(
        archive,
        [
            ("python/", "", 0o755, "dir"),
            ("python/bin/python3.12", "binary", 0o755, "file"),
            ("python/bin/python3", "python3.12", 0o777, "sym"),
        ],
    )
    build_release.validate_tar_gz(archive, "pinned runtime")


def test_extract_macos_runtime_skips_links_and_foreign_roots(tmp_path):
    archive = tmp_path / "runtime.tar.gz"
    _write_tar(
        archive,
        [
            ("python/", "", 0o755, "dir"),
            ("python/bin/python3.12", "binary", 0o755, "file"),
            ("python/bin/python3", "python3.12", 0o777, "sym"),
            ("python/lib/python3.12/venv/__init__.py", "venv module", 0o644, "file"),
            ("outside/notes.txt", "ignored", 0o644, "file"),
        ],
    )
    destination = tmp_path / "staging"

    extracted = build_release.extract_macos_runtime(archive, destination)

    assert extracted == 2
    binary = destination / "python" / "bin" / "python3.12"
    assert binary.read_bytes() == b"binary"
    # 符号链接必须被丢弃：ZIP 会把每个链接存成解释器的完整副本。
    assert not (destination / "python" / "bin" / "python3").exists()
    assert not (destination / "outside").exists()
    assert binary.stat().st_mode & stat.S_IXUSR


def test_extract_macos_runtime_rejects_an_empty_payload(tmp_path):
    archive = tmp_path / "empty.tar.gz"
    _write_tar(archive, [("docs/", "", 0o755, "dir")])
    with pytest.raises(RuntimeError, match="no python/ payload"):
        build_release.extract_macos_runtime(archive, tmp_path / "staging")


def test_prune_macos_runtime_removes_reviewed_paths_and_bytecode(scratch_root):
    runtime = scratch_root / "python"
    for relative in build_release.MACOS_RUNTIME_PRUNE_PATHS:
        path = runtime.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()
    interpreter = runtime / "bin" / "python3.12"
    interpreter.write_bytes(b"interpreter")
    keeper = runtime / "lib" / "python3.12" / "os.py"
    keeper.parent.mkdir(parents=True, exist_ok=True)
    keeper.write_text("keep\n", encoding="utf-8")
    cached = keeper.parent / "encodings" / "__pycache__" / "aliases.cpython-312.pyc"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"pyc")
    stray = keeper.parent / ".DS_Store"
    stray.write_bytes(b"meta")

    removed = build_release.prune_macos_runtime(runtime)

    assert set(build_release.MACOS_RUNTIME_PRUNE_PATHS) <= set(removed)
    for relative in build_release.MACOS_RUNTIME_PRUNE_PATHS:
        assert not runtime.joinpath(*relative.split("/")).exists()
    assert interpreter.read_bytes() == b"interpreter"
    assert "bin/python3.12" not in removed
    assert keeper.read_text(encoding="utf-8") == "keep\n"
    assert not cached.exists()
    assert not stray.exists()
    assert "lib/python3.12/encodings/__pycache__" in removed
    assert "lib/python3.12/.DS_Store" in removed


def test_prune_macos_runtime_leaves_a_tree_the_allowlist_accepts(scratch_root):
    root = scratch_root
    _make_macos_runtime_tree(root)
    _make_minimal_macos_app_tree(root)
    runtime = root / "python"
    for relative in (
        "bin/python3",
        "lib/python3.12/venv/__init__.py",
        "lib/python3.12/encodings/__pycache__/aliases.cpython-312.pyc",
        "lib/python3.12/site-packages/pip/__init__.py",
        "lib/python3.12/lib-dynload/.empty",
    ):
        path = runtime.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("payload\n", encoding="utf-8")

    # 未裁剪的运行时会被白名单拦下：venv / __pycache__ / .pyc / 隐藏文件。
    with pytest.raises(RuntimeError, match="forbidden artifacts"):
        build_release.assert_release_tree_clean(root, "macos")

    build_release.prune_macos_runtime(runtime)

    build_release.assert_release_tree_clean(root, "macos")


def test_pruned_runtime_still_satisfies_the_smoke_check(scratch_root):
    """裁剪清空纯净运行时自带的 site-packages，依赖由 wheel 预装阶段重建。"""
    root = scratch_root
    _make_macos_runtime_tree(root)
    _make_minimal_macos_app_tree(root)
    runtime = root / "python"
    stale = runtime / "lib" / "python3.12" / "venv" / "__init__.py"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("payload\n", encoding="utf-8")

    removed = build_release.prune_macos_runtime(runtime)

    assert "lib/python3.12/site-packages" in removed
    assert not (runtime / "lib" / "python3.12" / "site-packages").exists()
    build_release.assert_release_tree_clean(root, "macos")

    # 预装阶段把 wheel 解压回 site-packages，冒烟检查此时才应通过。
    site_packages = runtime / "lib" / "python3.12" / "site-packages"
    for name in build_release.MACOS_RUNTIME_REQUIRED_SITE_PACKAGES:
        (site_packages / name).mkdir(parents=True, exist_ok=True)

    build_release.assert_release_tree_clean(root, "macos")
    build_release.validate_macos_runtime(root, "intel")


def test_release_tree_allows_the_delocate_dylibs_directory(tmp_path):
    _make_minimal_macos_app_tree(tmp_path)
    dylibs = (
        tmp_path
        / "python"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "PIL"
        / ".dylibs"
    )
    dylibs.mkdir(parents=True)
    (dylibs / "libjpeg.62.4.0.dylib").write_bytes(b"native")

    build_release.assert_release_tree_clean(tmp_path, "macos")

    # 仍然禁止其它未列入白名单的隐藏路径。
    (tmp_path / "python" / "lib" / "python3.12" / "site-packages" / ".hidden").write_text(
        "no\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="forbidden artifacts"):
        build_release.assert_release_tree_clean(tmp_path, "macos")


def test_validate_macos_runtime_accepts_a_complete_layout(tmp_path):
    _make_macos_runtime_tree(tmp_path, "intel")
    build_release.validate_macos_runtime(tmp_path, "intel")


def test_validate_macos_runtime_lists_missing_payload(tmp_path):
    with pytest.raises(RuntimeError, match="smoke check is missing"):
        build_release.validate_macos_runtime(tmp_path, "intel")


def test_validate_macos_runtime_rejects_architecture_mismatch(tmp_path):
    _make_macos_runtime_tree(
        tmp_path,
        "apple-silicon",
        cpu_type=build_release.MACOS_RUNTIME_BUILDS["intel"]["cpu_type"],
    )
    with pytest.raises(RuntimeError, match="architecture does not match"):
        build_release.validate_macos_runtime(tmp_path, "apple-silicon")


def test_validate_macos_runtime_rejects_non_macho_binaries(tmp_path):
    build = _make_macos_runtime_tree(tmp_path, "intel")
    runtime_binary = tmp_path / "python" / "bin" / "python3.12"
    runtime_binary.write_bytes(b"#!/bin/sh\nexit 0\n")
    assert build["host_machine"] == "x86_64"
    with pytest.raises(RuntimeError, match="architecture does not match"):
        build_release.validate_macos_runtime(tmp_path, "intel")


def test_validate_macos_runtime_rejects_architecture_marker_drift(tmp_path):
    _make_macos_runtime_tree(tmp_path, "intel")
    (tmp_path / "python" / "RUNTIME-ARCH.txt").write_text("arm64\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="architecture marker does not match"):
        build_release.validate_macos_runtime(tmp_path, "intel")


def test_validate_macos_runtime_rejects_symbolic_links(tmp_path):
    _make_macos_runtime_tree(tmp_path, "intel")
    target = tmp_path / "python" / "bin" / "python3.12"
    (tmp_path / "python" / "bin" / "python3").symlink_to(target.name)
    with pytest.raises(RuntimeError, match="must not contain symbolic links"):
        build_release.validate_macos_runtime(tmp_path, "intel")


def test_macos_launcher_prefers_bundled_runtime_and_keeps_host_fallback():
    launcher = (PROJECT_ROOT / LAUNCHER_NAME).read_text(encoding="utf-8")

    assert 'PORTABLE_PYTHON="$SCRIPT_DIR/python/bin/python3.12"' in launcher
    assert "RUNTIME-ARCH.txt" in launcher
    assert "sysctl -n hw.optional.arm64" in launcher
    assert "recommended_macos_package" in launcher
    assert "xiaochen-math-treasure-macOS-AppleSilicon.zip" in launcher
    assert "xiaochen-math-treasure-macOS-Intel.zip" in launcher
    assert "chmod +x" in launcher
    # 下载隔离属性（com.apple.quarantine）会连带阻止包内自带 Python 被
    # 执行，启动器必须做一次性自愈。
    assert "com.apple.quarantine" in launcher
    assert "xattr -dr" in launcher
    # 身份核验必须同时接受新中文品牌与旧 MathBank 字样：只匹配旧字样会让
    # 旧实例永远停不掉（曾导致"端口 8000 已被占用"而拒绝启动）。
    assert "MathBank|小陈的数学宝藏" in launcher
    # 内置运行时分支必须排在 venv 回退分支之前，否则会先创建无用的 venv。
    portable_branch = launcher.index('if [ -f "$PORTABLE_PYTHON" ]; then')
    venv_branch = launcher.index('if [ ! -d "$SCRIPT_DIR/venv" ]; then')
    assert portable_branch < venv_branch
    assert portable_branch < launcher.index("find_supported_python) ||")
    assert venv_branch < launcher.rindex('PYTHON_BIN="$SCRIPT_DIR/venv/bin/python"')
    # 停止旧实例发生在选择解释器之前，身份校验必须同时接受内置运行时与旧 venv，
    # 否则升级到内置运行时后第二次双击会被“无法验证身份”挡住。
    assert "expected_python_executables" in launcher
    assert launcher.index("expected_python_executables() {") < launcher.index(
        "is_owned_server() {"
    )
    candidates = launcher[
        launcher.index("project_python_candidates() {") : launcher.index(
            "expected_python_executables() {"
        )
    ]
    assert '"$SCRIPT_DIR/python/bin/python3.12"' in candidates
    assert '"$SCRIPT_DIR/venv/bin/python"' in candidates
    # 内置运行时已预装依赖：只做完整性校验，不得再联网安装。
    assert 'if [ "$PORTABLE_RUNTIME" -eq 1 ]; then' in launcher
    assert "内置运行时的依赖不完整" in launcher
    assert "尚未安装，无法校验运行依赖" not in launcher


def test_macos_launcher_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(PROJECT_ROOT / LAUNCHER_NAME)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def test_macos_launcher_check_order_matches_the_documented_flow():
    launcher = (PROJECT_ROOT / LAUNCHER_NAME).read_text(encoding="utf-8")

    assert launcher.index("LEGACY_VENV_BACKUP=\"\"") < launcher.index(
        "-B -m scripts.release_overlay --platform macos"
    ) < launcher.index("正在检查运行环境依赖是否完整")
