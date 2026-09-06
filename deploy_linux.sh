#!/usr/bin/env bash
#
# Quanti - Linux 一键部署/运行脚本
#
# 用法：
#   bash deploy_linux.sh                  # 首次部署并后台启动 (0.0.0.0:8000)
#   bash deploy_linux.sh --foreground     # 部署后前台运行
#   bash deploy_linux.sh --systemd        # 部署并注册为 systemd 服务（需 root）
#   bash deploy_linux.sh --port 9000      # 自定义端口
#   bash deploy_linux.sh --host 127.0.0.1 # 仅本机访问
#   bash deploy_linux.sh --install-only   # 只安装依赖/构建前端，不启动
#   bash deploy_linux.sh --stop           # 停止后台进程
#   bash deploy_linux.sh --status         # 查看后台状态
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
MODE="background"
FORCE_SETUP=0
FORCE_BUILD=0

usage() {
    sed -n '2,18p' "$0"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --foreground) MODE="foreground"; shift ;;
        --systemd) MODE="systemd"; shift ;;
        --install-only) MODE="install-only"; shift ;;
        --stop) MODE="stop"; shift ;;
        --status) MODE="status"; shift ;;
        --port) PORT="$2"; shift 2 ;;
        --host) HOST="$2"; shift 2 ;;
        --force-setup) FORCE_SETUP=1; shift ;;
        --force-build) FORCE_BUILD=1; shift ;;
        -h|--help) usage ;;
        *) echo "[ERROR] 未知参数: $1" >&2; usage ;;
    esac
done

VENV_DIR="$ROOT/.venv"
PY="$VENV_DIR/bin/python"
LOG_DIR="$ROOT/logs"
OUT_LOG="$LOG_DIR/start_web.out.log"
ERR_LOG="$LOG_DIR/start_web.err.log"
PID_FILE="$LOG_DIR/start_web.pid"
MARKER="$VENV_DIR/.quanti_installed"

mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------- status
if [[ "$MODE" == "status" ]]; then
    if [[ -f "$PID_FILE" ]]; then
        PID="$(cat "$PID_FILE")"
        if kill -0 "$PID" 2>/dev/null; then
            echo "quanti-web is running: PID=$PID  http://$HOST:$PORT"
        else
            echo "quanti-web PID file exists but process is dead: $PID"
            rm -f "$PID_FILE"
        fi
    else
        echo "quanti-web is not running"
    fi
    exit 0
fi

# ---------------------------------------------------------------- stop
if [[ "$MODE" == "stop" ]]; then
    echo "Stopping quanti-web..."
    if [[ -f "$PID_FILE" ]]; then
        kill "$(cat "$PID_FILE")" 2>/dev/null || true
        rm -f "$PID_FILE"
    fi
    # start_web.py 会拉起 run_web.py，这里同时清理可能的子进程
    pkill -f "run_web.py --host $HOST --port $PORT" 2>/dev/null || true
    pkill -f "start_web.py --host $HOST --port $PORT" 2>/dev/null || true
    sleep 1
    echo "Stopped."
    exit 0
fi

# ---------------------------------------------------------------- 1. Python 环境
NEED_SETUP=0
if [[ ! -x "$PY" || ! -f "$MARKER" || "$FORCE_SETUP" == "1" ]]; then
    NEED_SETUP=1
fi

if [[ "$NEED_SETUP" == "1" ]]; then
    echo "[1/4] 准备 Python 虚拟环境..."
    if [[ ! -d "$VENV_DIR" ]]; then
        python3 -m venv "$VENV_DIR"
    fi
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install -r requirements.txt
    "$PY" -m pip install -r requirements-web.txt
    "$PY" -m pip install -r requirements-opt.txt || echo "[WARN] requirements-opt.txt 安装失败（可选优化依赖），继续"
    touch "$MARKER"
else
    echo "[1/4] Python 虚拟环境已就绪"
fi

# ---------------------------------------------------------------- 2. 前端
if [[ "$FORCE_BUILD" == "1" || ! -f "frontend/dist/index.html" ]]; then
    echo "[2/4] 构建 Vue 前端..."
    if ! command -v npm >/dev/null 2>&1; then
        echo "[ERROR] 未找到 npm，请先安装 Node.js" >&2
        exit 1
    fi
    if [[ ! -d "frontend/node_modules" ]]; then
        (cd frontend && npm install)
    fi
    (cd frontend && npm run build)
else
    echo "[2/4] 前端产物已存在，跳过构建（--force-build 可强制重建）"
fi

if [[ "$MODE" == "install-only" ]]; then
    echo "安装完成，未启动服务。"
    exit 0
fi

# ---------------------------------------------------------------- systemd
if [[ "$MODE" == "systemd" ]]; then
    if [[ $EUID -ne 0 ]]; then
        echo "[ERROR] --systemd 需要 root 权限：sudo bash deploy_linux.sh --systemd" >&2
        exit 1
    fi
    SERVICE_FILE="/etc/systemd/system/quanti-web.service"
    echo "[3/4] 写入 systemd 服务: $SERVICE_FILE"
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Quanti Web Console
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$ROOT
ExecStart=$PY start_web.py --host $HOST --port $PORT --skip-deps --skip-build
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now quanti-web
    echo "[4/4] systemd 服务已启动："
    systemctl --no-pager --full status quanti-web || true
    echo
    echo "日志：journalctl -u quanti-web -f"
    exit 0
fi

# ---------------------------------------------------------------- foreground
if [[ "$MODE" == "foreground" ]]; then
    echo "前台运行: http://$HOST:$PORT"
    exec "$PY" start_web.py --host "$HOST" --port "$PORT" --skip-deps --skip-build
fi

# ---------------------------------------------------------------- background
if [[ -f "$PID_FILE" ]]; then
    OLD_PID="$(cat "$PID_FILE")"
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "quanti-web 已在运行: PID=$OLD_PID  http://$HOST:$PORT"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

echo "[3/4] 后台启动 quanti-web..."
nohup "$PY" start_web.py --host "$HOST" --port "$PORT" --skip-deps --skip-build \
    >"$OUT_LOG" 2>"$ERR_LOG" </dev/null &
echo $! > "$PID_FILE"

sleep 2

if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    PID="$(cat "$PID_FILE")"
    echo "[4/4] 启动成功"
    echo "  URL:  http://$HOST:$PORT"
    echo "  PID:  $PID"
    echo "  日志: $OUT_LOG"
    echo "  错误: $ERR_LOG"
    echo "  停止: bash deploy_linux.sh --stop"
else
    echo "[ERROR] 启动失败，最近日志如下：" >&2
    tail -50 "$ERR_LOG" 2>/dev/null || true
    rm -f "$PID_FILE"
    exit 1
fi
