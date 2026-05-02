# PyInstaller spec for the Thalyn brain sidecar.
#
# Produces a one-folder bundle at `dist/thalyn-brain/`:
#
#     dist/thalyn-brain/
#         thalyn-brain          # entry executable
#         _internal/            # bundled interpreter, deps, data
#
# `scripts/build-brain-sidecar.sh` invokes this spec and stages the
# output under `target/brain-sidecar/thalyn-brain/`. Tauri's
# `beforeBundleCommand` then copies it into
# `<App>.app/Contents/Resources/thalyn-brain/`. ADR-0018 picks
# PyInstaller as the packaging path; the spike happens here.
#
# One-folder mode is preferred over single-file: cold start is
# noticeably faster (no temp-dir extraction on every launch), and
# debugging the bundle is straightforward when a hidden import gets
# missed.

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
)


SPEC_DIR = Path(SPECPATH).resolve()  # noqa: F821 — provided by PyInstaller.


hiddenimports: list[str] = []
datas: list[tuple[str, str]] = []
binaries: list[tuple[str, str]] = []

# Third-party packages that lean on dynamic imports / runtime
# discovery. `collect_all` walks each one for hidden imports, data
# files (templates, JSON schemas, version markers), and any bundled
# native binaries — far more reliable than enumerating by hand.
for pkg in (
    "claude_agent_sdk",
    "langgraph",
    "langgraph.checkpoint.sqlite",
    "opentelemetry",
    "opentelemetry.exporter.otlp",
    "sentry_sdk",
    "yoyo",
    "websockets",
    "croniter",
    "httpx",
):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Walk every `thalyn_brain.*` submodule defensively. The entry
# point's import graph already reaches the registered RPC modules,
# but new modules added later don't need a spec edit to be picked up.
hiddenimports += collect_submodules("thalyn_brain")

# Yoyo migrations are loaded by filesystem path
# (`Path(__file__).parent.parent / "migrations"`) and exec'd
# directly — both `.sql` and `.py` files need to exist on disk
# inside the bundle, not as compiled bytecode.
migrations_src = SPEC_DIR / "thalyn_brain" / "migrations"
for migration_file in migrations_src.iterdir():
    if migration_file.suffix in {".sql", ".py"} and migration_file.is_file():
        datas.append((str(migration_file), "thalyn_brain/migrations"))

# Mem0 / Mcp / mcp-builtin-catalog config files: pull anything tagged
# as data inside our own package (defensive — most of our state
# lives in SQLite, but config snippets and golden fixtures can
# sneak in over time).
datas += collect_data_files("thalyn_brain", excludes=["__pycache__"])


a = Analysis(  # noqa: F821
    [str(SPEC_DIR / "thalyn_brain" / "__main__.py")],
    pathex=[str(SPEC_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavyweight test-only deps the entry-point graph never
        # touches at runtime; excluded so bundle size stays close
        # to the documented ~100 MB target.
        "pytest",
        "pytest_asyncio",
        "pytest_socket",
        "ruff",
        "mypy",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="thalyn-brain",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="thalyn-brain",
)
