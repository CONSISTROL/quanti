#!/usr/bin/env python3
"""One-click launcher for Quanti Web Console (cross-platform).

What it does:
  1. Create/reuse a local virtual environment at <repo>/.venv
  2. Install backend dependencies (requirements.txt + requirements-web.txt)
  3. Build the Vue frontend when frontend/dist is missing or older than frontend/src
  4. Start the FastAPI server on http://127.0.0.1:8000

Usage:
    python start_web.py                 # first run: full setup + start
    python start_web.py --install       # force reinstall Python deps
    python start_web.py --build         # force rebuild Vue frontend
    python start_web.py --port 9000
    python start_web.py --skip-deps     # skip Python dependency install
    python start_web.py --skip-build    # never build the frontend (serve dist as-is)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
FRONTEND_DIR = ROOT / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
INDEX_HTML = DIST_DIR / "index.html"
FRONTEND_SRC = FRONTEND_DIR / "src"
# 这些构建配置/入口改动也会影响产物，一并纳入新鲜度判断。
FRONTEND_WATCH_FILES = (
    FRONTEND_DIR / "index.html",
    FRONTEND_DIR / "vite.config.js",
    FRONTEND_DIR / "package.json",
)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"\n>>> {' '.join(str(c) for c in cmd)}")
    subprocess.check_call(cmd, cwd=cwd or ROOT)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def ensure_venv(force_install: bool = False) -> bool:
    """Returns True when Python dependencies were installed."""
    created = not VENV_DIR.exists()
    py = venv_python()
    if created:
        print("[1/4] Creating virtual environment .venv ...")
        run([sys.executable, "-m", "venv", str(VENV_DIR)])
    else:
        print("[1/4] Reusing existing virtual environment .venv")

    if not py.exists():
        raise RuntimeError(f"venv python not found: {py}")

    if created or force_install:
        print("[2/4] Installing Python dependencies ...")
        run([str(py), "-m", "pip", "install", "--upgrade", "pip"])
        run([str(py), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
        run([str(py), "-m", "pip", "install", "-r", str(ROOT / "requirements-web.txt")])
        run([str(py), "-m", "pip", "install", "-r", str(ROOT / "requirements-opt.txt")])
        return True

    # .venv 已存在时不能直接跳过：检查 Web 后端关键依赖是否齐全，
    # 避免用户之前手动建过 .venv 但没装 requirements-web.txt 导致缺少 uvicorn/fastapi。
    check = subprocess.run(
        [str(py), "-c", "import fastapi, uvicorn, pandas, numpy"],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        print("[2/4] Existing .venv is missing web dependencies, installing ...")
        run([str(py), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
        run([str(py), "-m", "pip", "install", "-r", str(ROOT / "requirements-web.txt")])
        run([str(py), "-m", "pip", "install", "-r", str(ROOT / "requirements-opt.txt")])
        return True
    return False


def _files_newer_than(target: Path, built_at: float) -> list[Path]:
    """返回 target（文件或目录）下比 built_at 更新的文件。"""
    if not target.exists():
        return []
    files = target.rglob("*") if target.is_dir() else [target]
    return [f for f in files if f.is_file() and f.stat().st_mtime > built_at]


def frontend_stale_reason() -> str | None:
    """frontend/dist 是否落后于源码；是最新则返回 None。

    run_web.py 只托管预构建的 dist、自己不会编译，所以这里必须能发现
    「改了 frontend/src 但忘了 npm run build」—— 否则页面会一直停留在旧版本。
    """
    if not INDEX_HTML.exists():
        return "frontend/dist/index.html 不存在"
    built_at = INDEX_HTML.stat().st_mtime
    for target in (FRONTEND_SRC, *FRONTEND_WATCH_FILES):
        hits = _files_newer_than(target, built_at)
        if hits:
            names = "、".join(sorted({h.name for h in hits})[:3])
            more = f" 等 {len(hits)} 个文件" if len(hits) > 3 else ""
            return f"{target.name} 比 dist 新（{names}{more}）"
    return None


def npm_command() -> list[str]:
    # On Windows npm is npm.cmd; subprocess without shell cannot find plain "npm".
    return ["npm.cmd"] if os.name == "nt" else ["npm"]


def ensure_frontend(force_build: bool = False) -> None:
    reason = "--build 指定强制重建" if force_build else frontend_stale_reason()
    if reason is None:
        print("[3/4] Frontend already built and up to date (use --build to force rebuild)")
        return
    print(f"[3/4] Rebuilding Vue frontend: {reason}")
    if not (FRONTEND_DIR / "node_modules").exists():
        run(npm_command() + ["install"], cwd=FRONTEND_DIR)
    run(npm_command() + ["run", "build"], cwd=FRONTEND_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start Quanti Web Console")
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="bind port (default 8000)")
    parser.add_argument("--install", action="store_true", help="force reinstall Python deps")
    parser.add_argument("--build", action="store_true", help="force rebuild Vue frontend")
    parser.add_argument("--skip-deps", action="store_true", help="skip Python dependency install")
    parser.add_argument("--skip-build", action="store_true", help="never build the frontend")
    args = parser.parse_args()

    if not FRONTEND_DIR.is_dir():
        raise RuntimeError(f"frontend directory not found: {FRONTEND_DIR}")

    py = venv_python()
    if args.skip_deps:
        print("[1/4] Skipping Python dependency install (--skip-deps)")
    else:
        ensure_venv(force_install=args.install)

    if args.skip_build:
        print("[3/4] Skipping frontend build (--skip-build)")
    else:
        ensure_frontend(force_build=args.build)


    print("[4/4] Starting Quanti Web Console ...")
    print(f"    URL: http://{args.host}:{args.port}")
    print("    Ctrl+C to stop.\n")

    cmd = [
        str(py),
        str(ROOT / "run_web.py"),
        "--host", args.host,
        "--port", str(args.port),
    ]
    run(cmd)


if __name__ == "__main__":
    main()
