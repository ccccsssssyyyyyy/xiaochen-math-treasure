"""Build verified Windows and macOS release archives for MathBank.

The release builder deliberately fails closed: downloaded runtimes must match
their pinned SHA-256 values, cached downloads are rechecked on every build, and
only an explicit application allowlist is copied into staging directories.
"""

import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from mathbank import __version__
from mathbank.paths import BUILD_CACHE_DIR, DIST_DIR as PROJECT_DIST_DIR, PROJECT_ROOT


# Paths
BASE_DIR = str(PROJECT_ROOT)
DIST_DIR = str(PROJECT_DIST_DIR)
BUILD_DIR = os.path.join(DIST_DIR, "mathbank-windows")
PYTHON_DIR = os.path.join(BUILD_DIR, "python")
WHEELS_DIR = os.path.join(DIST_DIR, "wheels")
SITE_PACKAGES = os.path.join(PYTHON_DIR, "site-packages")
CACHE_DIR = str(BUILD_CACHE_DIR)
CACHE_WHEELS_DIR = os.path.join(CACHE_DIR, "wheels")

PYTHON_ZIP_URL = (
    "https://www.python.org/ftp/python/3.10.11/"
    "python-3.10.11-embed-amd64.zip"
)
PYTHON_ZIP_SHA256 = "608619f8619075629c9c69f361352a0da6ed7e62f83a0e19c63e0ea32eb7629d"
NUGET_ZIP_URL = (
    "https://api.nuget.org/v3-flatcontainer/python/3.10.11/"
    "python.3.10.11.nupkg"
)
NUGET_ZIP_SHA256 = "7c6f99b160a36a7e09492dfcff2b0a3a60bb5229ca44cdcc3ecb32871a6144d0"

# macOS ships a full portable CPython so Apple users no longer need a system
# Python.  The runtime is pinned by release tag and per-architecture SHA-256;
# see scripts/README-macos-runtime.md for the pruning contract.
MACOS_RUNTIME_PYTHON_VERSION = "3.12"
MACOS_RUNTIME_RELEASE = "20260901"
MACOS_RUNTIME_BASE_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{MACOS_RUNTIME_RELEASE}"
)
MACOS_RUNTIME_ROOT = "python"
MACOS_RUNTIME_ARCH_FILE = "python/RUNTIME-ARCH.txt"
_MACOS_CPU_TYPE_ARM64 = 0x0100000C
_MACOS_CPU_TYPE_X86_64 = 0x01000007
# 发布包文件名前缀，与仓库名保持一致。
# 为什么不用中文品牌名做文件名：GitHub 下载直链会对非 ASCII 做百分号转义，
# 复制/转发的链接会变成一串 %E5%...，既难看也容易在转发中截断；不同系统
# 解压时的编码处理也不一致。因此文件名走 ASCII，品牌名只出现在界面与文档里。
# 历史版本用 "MathBank-" 前缀，旧名保留在 RELEASE_OUTPUT_NAMES 中以便清理。
ASSET_PREFIX = "xiaochen-math-treasure"
MACOS_RUNTIME_BUILDS = {
    "apple-silicon": {
        "asset": (
            f"cpython-{MACOS_RUNTIME_PYTHON_VERSION}.14+{MACOS_RUNTIME_RELEASE}"
            "-aarch64-apple-darwin-install_only.tar.gz"
        ),
        "sha256": "3ee3ee547cedfeb7c2b16b2b7156039f7b470bb8f857e226fd3d2eb11db83c76",
        "host_machine": "arm64",
        "cpu_type": _MACOS_CPU_TYPE_ARM64,
        "wheel_platforms": ("macosx_11_0_arm64", "macosx_11_0_universal2"),
        "output_stem": f"{ASSET_PREFIX}-macOS-AppleSilicon",
    },
    "intel": {
        "asset": (
            f"cpython-{MACOS_RUNTIME_PYTHON_VERSION}.14+{MACOS_RUNTIME_RELEASE}"
            "-x86_64-apple-darwin-install_only.tar.gz"
        ),
        "sha256": "2e31b23f3f1319f707d0e620b48847a0046577541d357276821f9f1b5492e0ba",
        "host_machine": "x86_64",
        "cpu_type": _MACOS_CPU_TYPE_X86_64,
        "wheel_platforms": (
            "macosx_10_15_x86_64",
            "macosx_10_15_universal2",
            "macosx_11_0_x86_64",
            "macosx_11_0_universal2",
        ),
        "output_stem": f"{ASSET_PREFIX}-macOS-Intel",
    },
}
MACOS_BUILD_DIRS = {
    architecture: os.path.join(DIST_DIR, f"mathbank-macos-{architecture}")
    for architecture in MACOS_RUNTIME_BUILDS
}
# The portable runtime binary lives at python/bin/python3.12 and resolves
# site-packages relative to sys.prefix, i.e. python/lib/python3.12/.
_MACOS_RUNTIME_BIN = f"bin/python3.{MACOS_RUNTIME_PYTHON_VERSION[2:]}"
MACOS_RUNTIME_SITE_PACKAGES = os.path.join(
    MACOS_RUNTIME_ROOT,
    "lib",
    f"python{MACOS_RUNTIME_PYTHON_VERSION}",
    "site-packages",
)
# Imported by the runtime smoke check and by the macOS launcher.
MACOS_RUNTIME_REQUIRED_SITE_PACKAGES = (
    "PIL",
    "docx",
    "fastapi",
    "greenlet",
    "lxml",
    "pdf_inspector",
    "pymupdf",
    "sqlalchemy",
    "uvicorn",
)

ROOT_FILE_ALLOWLIST = (
    "main.py",
    ".env.example",
    "覆盖升级说明.txt",
    "requirements.txt",
    "README.md",
    "一键安装指令.md",
    "LICENSE",
)
STATIC_ALLOWLIST = (
    "index.html",
    "apple-touch-icon.png",
    "favicon.ico",
    "favicon.png",
    "favicon.svg",
    "css",
    "js",
    "lib",
)
# `.dylibs` is the delocate layout wheel-builders use to vendor macOS native
# libraries (Pillow ships libjpeg/libpng there); without it Pillow cannot load.
HIDDEN_FILE_ALLOWLIST = {".dylibs", ".env.example", ".gitkeep", ".rels"}
FORBIDDEN_RELEASE_PARTS = {
    ".DS_Store",
    ".git",
    ".github",
    ".pytest_cache",
    ".system_generated",
    "__pycache__",
    "scratch",
    "test_uploads",
    "tests",
    "venv",
}
FORBIDDEN_RELEASE_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".log",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RELEASE_OUTPUT_NAMES = (
    f"{ASSET_PREFIX}-Windows-x64.zip",
    f"{ASSET_PREFIX}-Windows-x64.zip.sha256",
    f"{ASSET_PREFIX}-macOS-AppleSilicon.zip",
    f"{ASSET_PREFIX}-macOS-AppleSilicon.zip.sha256",
    f"{ASSET_PREFIX}-macOS-Intel.zip",
    f"{ASSET_PREFIX}-macOS-Intel.zip.sha256",
    # Legacy names (<= 2.2.3) kept so a stale package in dist/ is always purged.
    "MathBank-Windows-x64.zip",
    "MathBank-Windows-x64.zip.sha256",
    "MathBank-macOS-AppleSilicon.zip",
    "MathBank-macOS-AppleSilicon.zip.sha256",
    "MathBank-macOS-Intel.zip",
    "MathBank-macOS-Intel.zip.sha256",
    "MathBank-macOS.zip",
    "MathBank-macOS.zip.sha256",
)
# These directories are shipped by pinned wheels but are not needed at runtime.
# Keep the list explicit so a newly introduced package payload still fails the
# release allowlist review instead of being silently discarded.
DEPENDENCY_TEST_PATHS = (
    Path("certifi", "tests"),
    Path("colorama", "tests"),
    Path("fastapi", ".agents"),
    Path("greenlet", "tests"),
)
# Reviewed, non-runtime payloads of the macOS dependency set.  Both are C header
# trees for compiling against the libraries, not runtime data.
MACOS_DEPENDENCY_PRUNE_PATHS = (
    Path("lxml", "includes"),
    Path("pymupdf", "mupdf-devel"),
)
# Everything below is present in the pinned macOS runtime but unused by the
# application.  Removing it takes the extracted runtime from 67 MB to 44 MB and
# is what allows the release to ship without a single symbolic link.
MACOS_RUNTIME_PRUNE_PATHS = (
    # Developer-facing launchers; only bin/python3.12 is executed.  The python3
    # and python entries are symbolic links that a ZIP would store as three
    # extra 18 MB copies of the interpreter.
    "bin/2to3",
    "bin/2to3-3.12",
    "bin/idle3",
    "bin/idle3.12",
    "bin/pip",
    "bin/pip3",
    "bin/pip3.12",
    "bin/pydoc3",
    "bin/pydoc3.12",
    "bin/python",
    "bin/python3",
    "bin/python3-config",
    "bin/python3.12-config",
    # Headers, pkg-config files and man pages.
    "include",
    "lib/pkgconfig",
    "share",
    # Tcl/Tk and its helper binding: only tkinter would load them.
    "lib/itcl4.3.8",
    "lib/libtcl9.0.dylib",
    "lib/libtcl9tk9.0.dylib",
    "lib/tcl9",
    "lib/tcl9.0",
    "lib/thread3.0.6",
    "lib/tk9.0",
    # libpython is only needed for embedding; bin/python3.12 is linked
    # statically and no extension module references it (verified with otool).
    "lib/libpython3.12.dylib",
    # Build configuration left behind by install_only.
    "lib/python3.12/config-3.12-darwin",
    "lib/python3.12/lib-dynload/.empty",
    # Bootstrappers and developer tooling.  The release installs dependencies at
    # build time, so pip/ensurepip/venv must never appear in the package.
    "lib/python3.12/ensurepip",
    "lib/python3.12/idlelib",
    "lib/python3.12/lib2to3",
    "lib/python3.12/tkinter",
    "lib/python3.12/turtledemo",
    "lib/python3.12/venv",
    # install_only seeds pip here; the release replaces it with the pinned
    # application dependencies.
    "lib/python3.12/site-packages",
)
OVERLAY_PROTECTED_PATHS = (
    ".env",
    ".system_generated",
    "data_backup",
    "math_question_bank.db",
    "math_question_bank.db-wal",
    "math_question_bank.db-shm",
    "static/uploads",
    "venv",
)
OVERLAY_MANAGED_ROOTS = (
    "main.py",
    ".env.example",
    "覆盖升级说明.txt",
    "requirements.txt",
    "README.md",
    "一键安装指令.md",
    "LICENSE",
    "mathbank",
    "scripts",
    "static",
    "templates",
)
WINDOWS_LAUNCHER_NAME = "启动题库系统.bat"


def sha256_file(path):
    """Return the lowercase SHA-256 digest for *path*."""
    digest = hashlib.sha256()
    with open(path, "rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path, expected_sha256, label="download"):
    """Fail closed unless *path* matches a syntactically valid pinned digest."""
    expected = str(expected_sha256).strip().lower()
    if not SHA256_PATTERN.fullmatch(expected):
        raise RuntimeError(f"{label} has no valid pinned SHA-256")
    actual = sha256_file(path)
    if not hmac.compare_digest(actual, expected):
        raise RuntimeError(
            f"{label} SHA-256 mismatch: expected {expected}, got {actual}"
        )
    return actual


def validate_zip(path, label="archive"):
    """Check ZIP structure and every member CRC before extraction."""
    try:
        with zipfile.ZipFile(path, "r") as zip_ref:
            corrupt_member = zip_ref.testzip()
    except (OSError, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"{label} is not a valid ZIP archive") from exc
    if corrupt_member is not None:
        raise RuntimeError(f"{label} contains a corrupt member: {corrupt_member}")


def validate_tar_gz(path, label="archive"):
    """Check tar.gz readability and reject unsafe member paths or link targets."""
    try:
        with tarfile.open(path, "r:gz") as tar_ref:
            members = tar_ref.getmembers()
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise RuntimeError(f"{label} is not a valid tar.gz archive") from exc
    if not members:
        raise RuntimeError(f"{label} is an empty tar.gz archive")
    for member in members:
        member_path = PurePosixPath(member.name)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise RuntimeError(f"unsafe tar member path: {member.name}")
        if member.issym() or member.islnk():
            link_path = PurePosixPath(member.linkname)
            if link_path.is_absolute() or ".." in link_path.parts:
                raise RuntimeError(f"unsafe tar link target: {member.name}")


def _validate_https_url(url, label):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise RuntimeError(f"{label} download URL must use HTTPS")


def download_verified(url, cache_path, expected_sha256, label, validator=validate_zip):
    """Return a verified cache file, downloading atomically when necessary."""
    _validate_https_url(url, label)
    expected = str(expected_sha256).strip().lower()
    if not SHA256_PATTERN.fullmatch(expected):
        raise RuntimeError(f"{label} has no valid pinned SHA-256")

    cache_path = os.fspath(cache_path)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    if os.path.isfile(cache_path):
        try:
            verify_sha256(cache_path, expected, label)
            validator(cache_path, label)
            print(f"💾 Using verified cached {label}...")
            return cache_path
        except RuntimeError as exc:
            # Keep the old cache untouched until a replacement is fully verified.
            print(f"⚠️ Ignoring invalid cached {label}: {exc}")

    print(f"📥 Downloading {label} from {url}...")
    file_descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{Path(cache_path).name}.",
        suffix=".download",
        dir=os.path.dirname(cache_path),
    )
    os.close(file_descriptor)
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": f"MathBank-release-builder/{__version__}"},
        )
        with urllib.request.urlopen(request, timeout=60) as response, open(
            temporary_path, "wb"
        ) as target:
            final_url = response.geturl() if hasattr(response, "geturl") else url
            _validate_https_url(final_url, label)
            shutil.copyfileobj(response, target, length=1024 * 1024)

        verify_sha256(temporary_path, expected, label)
        validator(temporary_path, label)
        os.replace(temporary_path, cache_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return cache_path


def _safe_zip_members(zip_ref):
    """Reject absolute paths and parent traversal before ZIP extraction."""
    for member in zip_ref.infolist():
        member_path = Path(member.filename.replace("\\", "/"))
        if member_path.is_absolute() or ".." in member_path.parts:
            raise RuntimeError(f"unsafe ZIP member path: {member.filename}")
        yield member


def validate_release_source():
    """Fail before packaging when release-critical imports or hints are broken."""
    print("🔎 Validating release source imports and type annotations...")
    validation_code = (
        "from typing import get_type_hints; "
        "from mathbank.ai_json import parse_ai_json; "
        "get_type_hints(parse_ai_json)"
    )
    subprocess.check_call([sys.executable, "-c", validation_code], cwd=BASE_DIR)

    compile_code = (
        "from pathlib import Path; "
        "[compile(p.read_bytes(), str(p), 'exec') "
        "for root in ('mathbank', 'scripts') "
        "for p in Path(root).rglob('*.py')]"
    )
    subprocess.check_call([sys.executable, "-c", compile_code], cwd=BASE_DIR)

    python310 = shutil.which("python3.10")
    if python310:
        subprocess.check_call(
            [python310, "-c", "import mathbank.ai_json"],
            cwd=BASE_DIR,
        )


def validate_target_requirements():
    """Reject host-dependent requirements in the cross-platform release input."""
    for requirements_name in ("requirements.txt", "requirements-windows.txt"):
        requirements_path = Path(BASE_DIR, requirements_name)
        if not requirements_path.is_file():
            raise RuntimeError(f"missing target requirements: {requirements_name}")
        for line_number, raw_line in enumerate(
            requirements_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = raw_line.partition("#")[0].strip().lower()
            if line and ";" in line and any(
                marker in line
                for marker in ("sys_platform", "platform_system", "platform_machine")
            ):
                raise RuntimeError(
                    f"{requirements_name} contains a host-dependent platform marker "
                    f"at line {line_number}; list target dependencies explicitly"
                )


def _remove_release_outputs():
    """Remove only known final archives, never unrelated dist contents."""

    for name in RELEASE_OUTPUT_NAMES:
        Path(DIST_DIR, name).unlink(missing_ok=True)


def clean_directories():
    print("🧹 Cleaning old directories...")
    os.makedirs(DIST_DIR, exist_ok=True)
    for path in (BUILD_DIR, WHEELS_DIR, *MACOS_BUILD_DIRS.values()):
        shutil.rmtree(path, ignore_errors=True)
    _remove_release_outputs()
    os.makedirs(PYTHON_DIR, exist_ok=True)
    os.makedirs(WHEELS_DIR, exist_ok=True)
    os.makedirs(SITE_PACKAGES, exist_ok=True)


def download_python():
    cache_path = os.path.join(CACHE_DIR, "python_embed.zip")
    download_verified(
        PYTHON_ZIP_URL,
        cache_path,
        PYTHON_ZIP_SHA256,
        "Python 3.10.11 embedded runtime",
    )
    print("📦 Extracting Python...")
    with zipfile.ZipFile(cache_path, "r") as zip_ref:
        zip_ref.extractall(PYTHON_DIR, members=_safe_zip_members(zip_ref))


def download_and_extract_sqlite():
    cache_path = os.path.join(CACHE_DIR, "python_nuget.zip")
    download_verified(
        NUGET_ZIP_URL,
        cache_path,
        NUGET_ZIP_SHA256,
        "official CPython NuGet package",
    )

    print("📦 Extracting sqlite3 binaries and VC runtime DLLs...")
    required_members = {
        "tools/DLLs/_sqlite3.pyd": os.path.join(PYTHON_DIR, "_sqlite3.pyd"),
        "tools/DLLs/sqlite3.dll": os.path.join(PYTHON_DIR, "sqlite3.dll"),
        "tools/vcruntime140.dll": os.path.join(PYTHON_DIR, "vcruntime140.dll"),
        "tools/vcruntime140_1.dll": os.path.join(PYTHON_DIR, "vcruntime140_1.dll"),
    }
    extracted_members = set()
    with zipfile.ZipFile(cache_path, "r") as zip_ref:
        _ = list(_safe_zip_members(zip_ref))
        for member in zip_ref.namelist():
            if member in required_members:
                target_path = required_members[member]
                with zip_ref.open(member) as source, open(target_path, "wb") as target:
                    shutil.copyfileobj(source, target)
                extracted_members.add(member)
            elif member.startswith("tools/Lib/sqlite3/") and not member.endswith("/"):
                relative_path = Path(member).relative_to("tools/Lib")
                target_path = Path(SITE_PACKAGES, relative_path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zip_ref.open(member) as source, open(target_path, "wb") as target:
                    shutil.copyfileobj(source, target)

    missing = sorted(set(required_members) - extracted_members)
    if missing:
        raise RuntimeError(f"official CPython NuGet package is missing: {missing}")
    print("✅ sqlite3 binaries and VC runtime DLLs injected successfully!")


def configure_python_path():
    print("⚙️ Configuring python310._pth...")
    pth_file = os.path.join(PYTHON_DIR, "python310._pth")
    if not os.path.isfile(pth_file):
        raise RuntimeError("embedded Python archive is missing python310._pth")
    with open(pth_file, "r", encoding="utf-8") as file_handle:
        lines = file_handle.read().splitlines()

    new_lines = []
    application_root_added = False
    site_packages_added = False
    for line in lines:
        if line.strip() == "#import site":
            new_lines.append("import site")
        else:
            new_lines.append(line)
        if line.strip() == ".":
            # Entries in python310._pth are relative to python/python.exe, not
            # to the launcher's working directory.  The application modules
            # (main.py, mathbank/, scripts/) live one directory above it.
            new_lines.append("..")
            application_root_added = True
            new_lines.append("site-packages")
            site_packages_added = True
    if not application_root_added or not site_packages_added:
        raise RuntimeError("embedded Python path file has no application path entry")
    with open(pth_file, "w", encoding="utf-8", newline="\n") as file_handle:
        file_handle.write("\n".join(new_lines) + "\n")


def download_and_extract_wheels():
    print("📥 Checking and downloading Windows wheels for requirements...")
    validate_target_requirements()
    requirements_file = os.path.join(BASE_DIR, "requirements-windows.txt")
    os.makedirs(CACHE_WHEELS_DIR, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "--platform",
        "win_amd64",
        "--python-version",
        "3.10",
        "--implementation",
        "cp",
        "--abi",
        "cp310",
        "--dest",
        WHEELS_DIR,
        "--find-links",
        CACHE_WHEELS_DIR,
        "--requirement",
        requirements_file,
    ]
    print(f"Running command: {subprocess.list2cmdline(cmd)}")
    subprocess.check_call(cmd, cwd=BASE_DIR)

    print("💾 Updating local wheels cache...")
    wheel_paths = sorted(Path(WHEELS_DIR).glob("*.whl"))
    if not wheel_paths:
        raise RuntimeError("pip did not resolve any Windows wheels")
    for wheel_path in wheel_paths:
        validate_zip(wheel_path, wheel_path.name)
        destination = Path(CACHE_WHEELS_DIR, wheel_path.name)
        if not destination.exists() or sha256_file(destination) != sha256_file(wheel_path):
            temporary = destination.with_name(f".{destination.name}.tmp")
            shutil.copy2(wheel_path, temporary)
            os.replace(temporary, destination)

    print("📦 Extracting wheels into site-packages...")
    for wheel_path in wheel_paths:
        with zipfile.ZipFile(wheel_path, "r") as zip_ref:
            zip_ref.extractall(SITE_PACKAGES, members=_safe_zip_members(zip_ref))
    _prune_dependency_test_artifacts(SITE_PACKAGES)


def _prune_dependency_test_artifacts(site_packages):
    """Remove reviewed, non-runtime test payloads shipped by pinned wheels."""

    root = Path(site_packages)
    for relative_path in DEPENDENCY_TEST_PATHS:
        shutil.rmtree(root / relative_path, ignore_errors=True)


def _remove_release_path(target):
    """Delete a file, link or directory; return True when something was removed."""
    if target.is_symlink():
        target.unlink()
        return True
    if target.is_dir():
        shutil.rmtree(target)
        return True
    if target.exists():
        target.unlink()
        return True
    return False


def prune_macos_dependency_payloads(site_packages):
    """Drop reviewed C header trees that macOS wheels ship for downstream builds."""
    root = Path(site_packages)
    removed = []
    for relative_path in MACOS_DEPENDENCY_PRUNE_PATHS:
        if _remove_release_path(root / relative_path):
            removed.append(relative_path.as_posix())
    return removed


def sweep_release_bytecode(root):
    """Delete compiled caches, links and macOS metadata the allowlist forbids."""
    root = Path(root)
    removed = []
    for directory, subdirectories, filenames in os.walk(root, topdown=True):
        directory = Path(directory)
        for name in list(subdirectories):
            child = directory / name
            if child.is_symlink() or name == "__pycache__":
                subdirectories.remove(name)
                if _remove_release_path(child):
                    removed.append(child.relative_to(root).as_posix())
        for name in filenames:
            if name.endswith((".pyc", ".pyo")) or name == ".DS_Store":
                child = directory / name
                if _remove_release_path(child):
                    removed.append(child.relative_to(root).as_posix())
    return removed


def prune_macos_runtime(runtime_root):
    """Reduce the pinned runtime to the reviewed minimum needed to serve the app."""
    root = Path(runtime_root)
    removed = []
    for relative_name in MACOS_RUNTIME_PRUNE_PATHS:
        if _remove_release_path(root.joinpath(*PurePosixPath(relative_name).parts)):
            removed.append(relative_name)
    removed.extend(sweep_release_bytecode(root))
    return removed


def extract_macos_runtime(archive_path, destination):
    """Extract the pinned runtime into its staging directory without any links."""
    destination = Path(destination)
    extracted = 0
    with tarfile.open(archive_path, "r:gz") as tar_ref:
        for member in tar_ref.getmembers():
            parts = PurePosixPath(member.name).parts
            if not parts or parts[0] != MACOS_RUNTIME_ROOT or len(parts) == 1:
                continue
            # Links are dropped on purpose: python/bin/python3.12 is the only
            # entry point, and a ZIP would store each link as a full 18 MB copy
            # of the interpreter.
            if member.issym() or member.islnk() or member.isdev():
                continue
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar_ref.extractfile(member)
            if source is None:
                raise RuntimeError(f"runtime member is unreadable: {member.name}")
            with source, open(target, "wb") as handle:
                shutil.copyfileobj(source, handle, length=1024 * 1024)
            os.chmod(target, member.mode & 0o755)
            extracted += 1
    if extracted == 0:
        raise RuntimeError("macOS runtime archive contains no python/ payload")
    return extracted


def write_macos_runtime_arch_file(root, architecture):
    """Record the target architecture so the launcher can reject a mismatched Mac."""
    build = MACOS_RUNTIME_BUILDS[architecture]
    path = Path(root, MACOS_RUNTIME_ARCH_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, build["host_machine"] + "\n")
    return path


def download_macos_wheels(architecture, wheels_dir):
    """Resolve every pinned macOS dependency wheel for one architecture."""
    validate_target_requirements()
    build = MACOS_RUNTIME_BUILDS[architecture]
    os.makedirs(wheels_dir, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "--python-version",
        MACOS_RUNTIME_PYTHON_VERSION,
        "--implementation",
        "cp",
        "--abi",
        f"cp{MACOS_RUNTIME_PYTHON_VERSION.replace('.', '')}",
    ]
    for wheel_platform in build["wheel_platforms"]:
        cmd.extend(("--platform", wheel_platform))
    cmd.extend(
        (
            "--dest",
            wheels_dir,
            "--find-links",
            CACHE_WHEELS_DIR,
            "--requirement",
            os.path.join(BASE_DIR, "requirements-windows.txt"),
        )
    )
    print(f"Running command: {subprocess.list2cmdline(cmd)}")
    subprocess.check_call(cmd, cwd=BASE_DIR)

    wheel_paths = sorted(Path(wheels_dir).glob("*.whl"))
    if not wheel_paths:
        raise RuntimeError(f"pip did not resolve any macOS wheels for {architecture}")
    for wheel_path in wheel_paths:
        validate_zip(wheel_path, wheel_path.name)
    return wheel_paths


def extract_macos_dependencies(wheel_paths, site_packages):
    """Install the pinned wheels into the runtime's own site-packages."""
    destination = Path(site_packages)
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    for wheel_path in wheel_paths:
        with zipfile.ZipFile(wheel_path, "r") as zip_ref:
            zip_ref.extractall(destination, members=_safe_zip_members(zip_ref))
    _prune_dependency_test_artifacts(destination)
    prune_macos_dependency_payloads(destination)
    sweep_release_bytecode(destination)


def _macho_cpu_type(path):
    """Return the Mach-O CPU type of *path*, or None when it is not Mach-O."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(8)
    except OSError:
        return None
    if len(header) < 8 or struct.unpack("<I", header[:4])[0] != 0xFEEDFACF:
        return None
    return struct.unpack("<i", header[4:8])[0]


def validate_macos_runtime(root, architecture):
    """Verify the staged macOS runtime, executing it on a matching host."""
    root = Path(root)
    build = MACOS_RUNTIME_BUILDS[architecture]
    runtime = root / MACOS_RUNTIME_ROOT
    executable = runtime.joinpath(*PurePosixPath(_MACOS_RUNTIME_BIN).parts)
    arch_file = root / MACOS_RUNTIME_ARCH_FILE
    site_packages = root.joinpath(*PurePosixPath(MACOS_RUNTIME_SITE_PACKAGES).parts)

    required = [executable, arch_file]
    required.extend(
        site_packages / name for name in MACOS_RUNTIME_REQUIRED_SITE_PACKAGES
    )
    missing = [path.relative_to(root).as_posix() for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"macOS runtime smoke check is missing: {missing}")

    if arch_file.read_text(encoding="utf-8").strip() != build["host_machine"]:
        raise RuntimeError("macOS runtime architecture marker does not match its target")

    links = sorted(
        path.relative_to(root).as_posix()
        for path in runtime.rglob("*")
        if path.is_symlink()
    )
    if links:
        raise RuntimeError(f"macOS runtime must not contain symbolic links: {links[:5]}")

    if _macho_cpu_type(executable) != build["cpu_type"]:
        raise RuntimeError(
            "macOS runtime binary architecture does not match "
            f"{build['host_machine']}: {executable.relative_to(root).as_posix()}"
        )

    if platform.system() == "Darwin" and platform.machine() == build["host_machine"]:
        environment = os.environ.copy()
        # A build must never leave bytecode behind: the staging tree is checked
        # against FORBIDDEN_RELEASE_SUFFIXES right after this call.
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        subprocess.check_call(
            [
                str(executable),
                "-c",
                (
                    "import sqlite3, ssl; "
                    "import fastapi, uvicorn, sqlalchemy, greenlet, colorama; "
                    "import pdf_inspector, pymupdf, PIL, lxml, docx; "
                    "import mathbank.ai_json"
                ),
            ],
            cwd=root,
            env=environment,
        )


def _release_ignore(_directory, names):
    ignored = set()
    for name in names:
        if (
            name.startswith(".")
            or name in FORBIDDEN_RELEASE_PARTS
            or name.endswith((".pyc", ".pyo"))
        ):
            ignored.add(name)
    return ignored


def _release_path_violation(relative_path):
    """Return why a package member crosses the persistent-data boundary."""

    path = PurePosixPath(Path(relative_path).as_posix())
    name = path.as_posix()
    if name == "static/uploads/.gitkeep":
        return None
    if name == ".env" or name.startswith(".env/"):
        return "local API configuration"
    if name == ".system_generated" or name.startswith(".system_generated/"):
        return "local runtime state"
    if name == "data_backup" or name.startswith("data_backup/"):
        return "local backups and metadata"
    if name == "venv" or name.startswith("venv/"):
        return "local virtual environment"
    if name == "static/uploads" or name.startswith("static/uploads/"):
        return "user uploads"
    if path.name.lower().endswith(tuple(FORBIDDEN_RELEASE_SUFFIXES)):
        return "persistent database or runtime artifact"
    return None


def _overlay_managed_roots(platform_name):
    roots = list(OVERLAY_MANAGED_ROOTS)
    if platform_name == "windows-x64":
        roots.extend(("启动题库系统.bat", "python"))
    elif platform_name == "macos":
        roots.extend(("启动题库系统.command", MACOS_RUNTIME_ROOT))
    else:
        raise RuntimeError(f"unknown release platform: {platform_name}")
    return roots


def _copy_allowlisted_entry(source, destination):
    if source.is_dir():
        shutil.copytree(source, destination, ignore=_release_ignore)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    else:
        raise RuntimeError(f"required release source is missing: {source}")


def copy_app_files(destination, launcher_name):
    """Copy only approved application assets into a release staging tree."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    print(f"📂 Copying allowlisted application files to {destination}...")

    for relative_name in ROOT_FILE_ALLOWLIST:
        _copy_allowlisted_entry(
            Path(BASE_DIR, relative_name), destination / relative_name
        )
    _copy_allowlisted_entry(Path(BASE_DIR, launcher_name), destination / launcher_name)
    _copy_allowlisted_entry(Path(BASE_DIR, "mathbank"), destination / "mathbank")
    _copy_allowlisted_entry(Path(BASE_DIR, "scripts"), destination / "scripts")

    static_destination = destination / "static"
    static_destination.mkdir(exist_ok=True)
    for relative_name in STATIC_ALLOWLIST:
        _copy_allowlisted_entry(
            Path(BASE_DIR, "static", relative_name),
            static_destination / relative_name,
        )
    uploads_directory = static_destination / "uploads"
    uploads_directory.mkdir(parents=True, exist_ok=True)
    (uploads_directory / ".gitkeep").touch()

    templates_source = Path(BASE_DIR, "templates")
    if templates_source.is_dir():
        _copy_allowlisted_entry(templates_source, destination / "templates")


def create_launcher():
    """Write the reviewed Windows launcher with cmd.exe-safe CRLF endings."""
    launcher_source = Path(BASE_DIR, WINDOWS_LAUNCHER_NAME)
    launcher_destination = Path(BUILD_DIR, launcher_source.name)
    if not launcher_source.is_file():
        raise RuntimeError(f"missing Windows launcher: {launcher_source}")
    try:
        launcher_text = launcher_source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("Windows launcher must be UTF-8 text") from exc
    if launcher_text.startswith("\ufeff"):
        raise RuntimeError("Windows launcher must not contain a UTF-8 BOM")
    launcher_destination.parent.mkdir(parents=True, exist_ok=True)
    normalized_text = launcher_text.replace("\r\n", "\n").replace("\r", "\n")
    launcher_destination.write_bytes(
        normalized_text.replace("\n", "\r\n").encode("utf-8")
    )
    validate_windows_launcher(launcher_destination)


def _validate_windows_launcher_bytes(payload, label):
    """Reject batch launchers that cmd.exe cannot parse consistently."""
    if not payload:
        raise RuntimeError(f"{label} is empty")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise RuntimeError(f"{label} must not contain a UTF-8 BOM")
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"{label} must be UTF-8 text") from exc
    remaining = payload.replace(b"\r\n", b"")
    if b"\r" in remaining or b"\n" in remaining or not payload.endswith(b"\r\n"):
        raise RuntimeError(f"{label} must use CRLF line endings only")


def validate_windows_launcher(path):
    """Validate the raw bytes of a staged Windows batch launcher."""
    launcher_path = Path(path)
    if not launcher_path.is_file():
        raise RuntimeError(f"Windows launcher is missing: {launcher_path}")
    _validate_windows_launcher_bytes(
        launcher_path.read_bytes(), f"Windows launcher {launcher_path.name}"
    )


def _release_files(root):
    return sorted(path for path in Path(root).rglob("*") if path.is_file())


def assert_release_tree_clean(root, platform_name):
    """Enforce release allowlist invariants before an archive can be created."""
    root = Path(root)
    required = {
        "main.py",
        "requirements.txt",
        "覆盖升级说明.txt",
        "mathbank/__init__.py",
        "scripts/release_overlay.py",
        "static/index.html",
        "static/uploads/.gitkeep",
    }
    if platform_name == "windows-x64":
        required.update(
            {
                "python/python.exe",
                "python/_sqlite3.pyd",
                "启动题库系统.bat",
            }
        )
    elif platform_name == "macos":
        required.add("启动题库系统.command")
    else:
        raise RuntimeError(f"unknown release platform: {platform_name}")

    relative_files = {path.relative_to(root).as_posix() for path in _release_files(root)}
    missing = sorted(required - relative_files)
    if missing:
        raise RuntimeError(f"release staging tree is missing required files: {missing}")

    violations = []
    for file_path in _release_files(root):
        relative_path = file_path.relative_to(root)
        persistence_violation = _release_path_violation(relative_path)
        if persistence_violation:
            violations.append(
                f"{relative_path.as_posix()} ({persistence_violation})"
            )
            continue
        for part in relative_path.parts:
            if part in FORBIDDEN_RELEASE_PARTS:
                violations.append(relative_path.as_posix())
                break
            if part.startswith(".") and part not in HIDDEN_FILE_ALLOWLIST:
                violations.append(relative_path.as_posix())
                break
        else:
            lower_name = file_path.name.lower()
            if any(lower_name.endswith(suffix) for suffix in FORBIDDEN_RELEASE_SUFFIXES):
                violations.append(relative_path.as_posix())
    if violations:
        raise RuntimeError(
            "release staging tree contains forbidden artifacts: "
            + ", ".join(sorted(set(violations))[:20])
        )


def write_release_manifest(root, platform_name):
    """Write a deterministic per-file SHA-256 manifest inside the release."""
    root = Path(root)
    manifest_path = root / "RELEASE-MANIFEST.json"
    entries = []
    for file_path in _release_files(root):
        if file_path == manifest_path:
            continue
        entries.append(
            {
                "path": file_path.relative_to(root).as_posix(),
                "sha256": sha256_file(file_path),
                "size": file_path.stat().st_size,
            }
        )
    manifest = {
        "app_version": __version__,
        "files": entries,
        "overlay": {
            "managed_roots": _overlay_managed_roots(platform_name),
            "protected_paths": list(OVERLAY_PROTECTED_PATHS),
            "schema_version": 1,
        },
        "platform": platform_name,
        "schema_version": 1,
    }
    _atomic_write_text(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest_path


def _atomic_write_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def validate_staged_python_sources(root):
    """Compile staged Python sources without importing data-bearing modules."""
    smoke_code = (
        "from pathlib import Path; "
        "files=list(Path('.').joinpath('mathbank').rglob('*.py'))"
        "+list(Path('.').joinpath('scripts').rglob('*.py'))+[Path('main.py')]; "
        "[compile(p.read_bytes(), str(p), 'exec') for p in files]"
    )
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.check_call(
        [sys.executable, "-c", smoke_code], cwd=root, env=environment
    )


def validate_windows_runtime(root):
    """Run the embedded runtime on Windows, otherwise verify its critical layout."""
    root = Path(root)
    python_executable = root / "python" / "python.exe"
    required = (
        python_executable,
        root / "python" / "python310._pth",
        root / "python" / "_sqlite3.pyd",
        root / "python" / "sqlite3.dll",
        root / "python" / "site-packages" / "fastapi",
        root / "python" / "site-packages" / "uvicorn",
        root / "python" / "site-packages" / "sqlalchemy",
        root / "python" / "site-packages" / "greenlet",
        root / "python" / "site-packages" / "colorama",
    )
    missing = [str(path.relative_to(root)) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"Windows runtime smoke check is missing: {missing}")
    path_lines = {
        line.strip()
        for line in (root / "python" / "python310._pth").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    required_path_lines = {".", "..", "site-packages", "import site"}
    missing_path_lines = sorted(required_path_lines - path_lines)
    if missing_path_lines:
        raise RuntimeError(
            "Windows embedded runtime cannot import the application root; "
            f"python310._pth is missing: {missing_path_lines}"
        )
    if os.name == "nt":
        subprocess.check_call(
            [
                str(python_executable),
                "-c",
                (
                    "import sqlite3, fastapi, uvicorn, sqlalchemy, greenlet, colorama; "
                    "import pdf_inspector; "
                    "import mathbank.ai_json"
                ),
            ],
            cwd=root,
        )


def verify_zip_archive(zip_path, required_members):
    """Verify a finished archive against its embedded per-file manifest."""
    validate_zip(zip_path, Path(zip_path).name)
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        file_infos = {}
        collision_keys = set()
        for info in zip_ref.infolist():
            raw_name = info.filename
            name = raw_name[:-1] if raw_name.endswith("/") else raw_name
            if not name:
                continue
            member_path = PurePosixPath(name)
            if (
                "\\" in name
                or member_path.is_absolute()
                or member_path.as_posix() != name
                or any(part in {"", ".", ".."} or ":" in part for part in member_path.parts)
            ):
                raise RuntimeError(
                    f"finished release archive contains an unsafe path: {raw_name}"
                )
            if info.is_dir():
                continue
            collision_key = name.casefold()
            if collision_key in collision_keys:
                raise RuntimeError(
                    f"finished release archive contains a duplicate path: {name}"
                )
            collision_keys.add(collision_key)
            file_infos[name] = info

        names = set(file_infos)
        missing = sorted(set(required_members) - names)
        if missing:
            raise RuntimeError(f"finished release archive is missing: {missing}")

        violations = []
        for name in names:
            parts = PurePosixPath(name).parts
            persistence_violation = _release_path_violation(name)
            if persistence_violation:
                violations.append(f"{name} ({persistence_violation})")
                continue
            if any(part in FORBIDDEN_RELEASE_PARTS for part in parts):
                violations.append(name)
            if any(
                part.startswith(".") and part not in HIDDEN_FILE_ALLOWLIST
                for part in parts
            ):
                violations.append(name)
            if any(name.lower().endswith(suffix) for suffix in FORBIDDEN_RELEASE_SUFFIXES):
                violations.append(name)
        if violations:
            raise RuntimeError(
                "finished release archive contains forbidden artifacts: "
                + ", ".join(sorted(set(violations))[:20])
            )

        manifest_info = file_infos.get("RELEASE-MANIFEST.json")
        if manifest_info is None:
            raise RuntimeError("finished release archive has no RELEASE-MANIFEST.json")
        if manifest_info.file_size > 8 * 1024 * 1024:
            raise RuntimeError("finished release manifest is unexpectedly large")
        try:
            with zip_ref.open(manifest_info, "r") as manifest_stream:
                manifest_bytes = manifest_stream.read(8 * 1024 * 1024 + 1)
            if len(manifest_bytes) > 8 * 1024 * 1024:
                raise RuntimeError("finished release manifest is unexpectedly large")
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("finished release manifest is invalid JSON") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise RuntimeError("finished release manifest schema is unsupported")
        if manifest.get("app_version") != __version__:
            raise RuntimeError("finished release manifest version does not match the app")
        if manifest.get("platform") not in {"macos", "windows-x64"}:
            raise RuntimeError("finished release manifest platform is invalid")
        overlay = manifest.get("overlay")
        expected_managed_roots = _overlay_managed_roots(manifest.get("platform"))
        if overlay != {
            "managed_roots": expected_managed_roots,
            "protected_paths": list(OVERLAY_PROTECTED_PATHS),
            "schema_version": 1,
        }:
            raise RuntimeError("finished release overlay contract is invalid")

        if manifest.get("platform") == "macos":
            launcher_info = file_infos.get("启动题库系统.command")
            if launcher_info is None:
                raise RuntimeError("finished macOS release has no launcher")
            unix_mode = launcher_info.external_attr >> 16
            if launcher_info.create_system == 3 and not unix_mode & 0o111:
                raise RuntimeError("finished macOS launcher is not executable")
        else:
            launcher_info = file_infos.get(WINDOWS_LAUNCHER_NAME)
            if launcher_info is None:
                raise RuntimeError("finished Windows release has no launcher")
            if launcher_info.file_size > 1024 * 1024:
                raise RuntimeError("finished Windows launcher is unexpectedly large")
            with zip_ref.open(launcher_info, "r") as launcher_stream:
                launcher_payload = launcher_stream.read(1024 * 1024 + 1)
            if len(launcher_payload) > 1024 * 1024:
                raise RuntimeError("finished Windows launcher is unexpectedly large")
            _validate_windows_launcher_bytes(
                launcher_payload, "finished Windows launcher"
            )

        entries = manifest.get("files")
        if not isinstance(entries, list) or len(entries) > 100_000:
            raise RuntimeError("finished release manifest file list is invalid")
        expected_files = {}
        expected_collision_keys = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError("finished release manifest contains an invalid entry")
            name = entry.get("path")
            expected_size = entry.get("size")
            expected_hash = entry.get("sha256")
            if not isinstance(name, str) or name == "RELEASE-MANIFEST.json":
                raise RuntimeError("finished release manifest contains an invalid path")
            member_path = PurePosixPath(name)
            if (
                "\\" in name
                or member_path.is_absolute()
                or member_path.as_posix() != name
                or any(part in {"", ".", ".."} or ":" in part for part in member_path.parts)
                or name.casefold() in expected_collision_keys
            ):
                raise RuntimeError(
                    f"finished release manifest contains a duplicate or unsafe path: {name}"
                )
            if type(expected_size) is not int or expected_size < 0:
                raise RuntimeError(
                    f"finished release manifest has an invalid size for: {name}"
                )
            if not isinstance(expected_hash, str) or not SHA256_PATTERN.fullmatch(
                expected_hash
            ):
                raise RuntimeError(
                    f"finished release manifest has an invalid SHA-256 for: {name}"
                )
            expected_files[name] = (expected_size, expected_hash)
            expected_collision_keys.add(name.casefold())

        expected_names = set(expected_files) | {"RELEASE-MANIFEST.json"}
        if names != expected_names:
            missing_from_zip = sorted(expected_names - names)
            extra_in_zip = sorted(names - expected_names)
            raise RuntimeError(
                "finished release archive does not match its manifest: "
                f"missing={missing_from_zip[:10]}, extra={extra_in_zip[:10]}"
            )

        for name, (expected_size, expected_hash) in expected_files.items():
            info = file_infos[name]
            if info.file_size != expected_size:
                raise RuntimeError(
                    f"finished release member size does not match manifest: {name}"
                )
            actual_size = 0
            digest = hashlib.sha256()
            with zip_ref.open(info, "r") as member_stream:
                for chunk in iter(lambda: member_stream.read(1024 * 1024), b""):
                    actual_size += len(chunk)
                    if actual_size > expected_size:
                        raise RuntimeError(
                            f"finished release member exceeds manifest size: {name}"
                        )
                    digest.update(chunk)
            if actual_size != expected_size or not hmac.compare_digest(
                digest.hexdigest(), expected_hash
            ):
                raise RuntimeError(
                    f"finished release member SHA-256 does not match manifest: {name}"
                )


def _write_archive_checksum(zip_path):
    zip_path = Path(zip_path)
    checksum_path = zip_path.with_name(zip_path.name + ".sha256")
    _atomic_write_text(
        checksum_path, f"{sha256_file(zip_path)}  {zip_path.name}\n"
    )
    return checksum_path


def _build_archive(staging_root, archive_stem, platform_name, launcher_name):
    archive_path = Path(f"{archive_stem}.zip")
    checksum_path = archive_path.with_name(archive_path.name + ".sha256")
    archive_path.unlink(missing_ok=True)
    checksum_path.unlink(missing_ok=True)
    try:
        assert_release_tree_clean(staging_root, platform_name)
        if platform_name == "windows-x64":
            validate_windows_launcher(Path(staging_root, WINDOWS_LAUNCHER_NAME))
        validate_staged_python_sources(staging_root)
        write_release_manifest(staging_root, platform_name)
        archive_path = Path(shutil.make_archive(archive_stem, "zip", staging_root))
        verify_zip_archive(
            archive_path,
            {
                "RELEASE-MANIFEST.json",
                "main.py",
                launcher_name,
                "scripts/release_overlay.py",
                "覆盖升级说明.txt",
            },
        )
        checksum_path = _write_archive_checksum(archive_path)
    except Exception:
        archive_path.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        raise
    print(f"🎉 Release archive created: {archive_path}")
    print(f"🔐 Archive checksum created: {checksum_path}")
    return os.fspath(archive_path)


def zip_release():
    print("🤐 Zipping Windows release package...")
    validate_windows_runtime(BUILD_DIR)
    return _build_archive(
        BUILD_DIR,
        os.path.join(DIST_DIR, f"{ASSET_PREFIX}-Windows-x64"),
        "windows-x64",
        WINDOWS_LAUNCHER_NAME,
    )


def build_macos_release(architecture):
    """Stage, verify and archive one macOS portable release."""
    build = MACOS_RUNTIME_BUILDS[architecture]
    staging = Path(MACOS_BUILD_DIRS[architecture])
    shutil.rmtree(staging, ignore_errors=True)
    print(f"🤐 Building macOS {architecture} package ({build['host_machine']})...")

    copy_app_files(staging, "启动题库系统.command")
    os.chmod(staging / "启动题库系统.command", 0o755)

    runtime_archive = download_verified(
        f"{MACOS_RUNTIME_BASE_URL}/{build['asset']}",
        os.path.join(CACHE_DIR, build["asset"]),
        build["sha256"],
        f"macOS {build['host_machine']} portable Python "
        f"{MACOS_RUNTIME_PYTHON_VERSION}",
        validator=validate_tar_gz,
    )
    print("📦 Extracting portable Python runtime...")
    extract_macos_runtime(runtime_archive, staging)
    prune_macos_runtime(staging / MACOS_RUNTIME_ROOT)
    write_macos_runtime_arch_file(staging, architecture)

    wheels_dir = os.path.join(DIST_DIR, "wheels", f"macos-{architecture}")
    wheel_paths = download_macos_wheels(architecture, wheels_dir)
    print(f"📦 Installing {len(wheel_paths)} wheels into the runtime...")
    extract_macos_dependencies(
        wheel_paths, staging.joinpath(*PurePosixPath(MACOS_RUNTIME_SITE_PACKAGES).parts)
    )
    validate_macos_runtime(staging, architecture)

    try:
        return _build_archive(
            staging,
            os.path.join(DIST_DIR, build["output_stem"]),
            "macos",
            "启动题库系统.command",
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(wheels_dir, ignore_errors=True)


def zip_macos_release():
    print("🤐 Zipping macOS release packages...")
    return [
        build_macos_release(architecture) for architecture in MACOS_RUNTIME_BUILDS
    ]


def cleanup_temp():
    print("🧹 Cleaning up temporary files...")
    shutil.rmtree(WHEELS_DIR, ignore_errors=True)
    shutil.rmtree(BUILD_DIR, ignore_errors=True)


def main():
    try:
        # Clear stale finals before even the earliest preflight.  A failed
        # invocation must never leave an older package looking newly valid.
        _remove_release_outputs()
        validate_release_source()
        validate_target_requirements()
        clean_directories()
        download_python()
        download_and_extract_sqlite()
        configure_python_path()
        download_and_extract_wheels()
        copy_app_files(BUILD_DIR, "启动题库系统.bat")
        create_launcher()
        zip_release()
        zip_macos_release()
        cleanup_temp()
        print("🚀 Windows & macOS Release Packages Built Successfully!")
    except Exception as exc:
        # The two platform archives are one release set.  If either side fails,
        # remove both known outputs while preserving every unrelated dist file.
        _remove_release_outputs()
        print(f"❌ Error during packaging: {exc}")
        raise


if __name__ == "__main__":
    main()
