#!/usr/bin/env python3
"""One-click launcher for Quanti Web Console (cross-platform).

What it does:
  1. Create/reuse a local virtual environment at <repo>/.venv
  2. Install backend dependencies (requirements.txt + requirements-web.txt)
  3. Build the Vue frontend if frontend/dist is missing
  4. Start the FastAPI server on http://127.0.0.1:8000

Usage:
    python start_web.py                 # first run: full setup + start
    python start_web.py --install       # force reinstall Python deps
    python start_web.py --build         # force rebuild Vue frontend
    python start_web.py --port 9000
    python start_web.py --skip-deps     # skip Python dependency install
    python start_web.py --skip-build    # skip frontend build even if dist missing
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
    return False


def npm_command() -> list[str]:
    # On Windows npm is npm.cmd; subprocess without shell cannot find plain "npm".
    return ["npm.cmd"] if os.name == "nt" else ["npm"]


def ensure_frontend(force_build: bool = False) -> None:
    if not INDEX_HTML.exists() or force_build:
        print("[3/4] Building Vue frontend ...")
        if not (FRONTEND_DIR / "node_modules").exists():
            run(npm_command() + ["install"], cwd=FRONTEND_DIR)
        run(npm_command() + ["run", "build"], cwd=FRONTEND_DIR)
    else:
        print("[3/4] Frontend already built (use --build to force rebuild)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Start Quanti Web Console")
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="bind port (default 8000)")
    parser.add_argument("--install", action="store_true", help="force reinstall Python deps")
    parser.add_argument("--build", action="store_true", help="force rebuild Vue frontend")
    parser.add_argument("--skip-deps", action="store_true", help="skip Python dependency install")
    parser.add_argument("--skip-build", action="store_true", help="skip frontend build")
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
