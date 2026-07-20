#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/backend/.venv"

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "缺少必要命令：$1" >&2
        exit 1
    }
}

require_command python3
mkdir -p "$ROOT/logs" "$ROOT/data/aifpatent" "$ROOT/data/langgraph" "$ROOT/workspace/uploads"

if [[ ! -d "$VENV" ]]; then
    echo "[1/2] 创建 Python 虚拟环境..."
    python3 -m venv "$VENV"
fi
echo "[1/2] 安装 Python 依赖..."
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r "$ROOT/backend/requirements.txt"
echo "  ✓ 依赖已安装"

echo "[2/2] 运行目录已就绪"
echo "API Base URL、Token 和 Model 由每次网页 Run 临时输入，不写入本地文件。"

echo
echo "安装完成！"
echo "启动：./start.sh"
echo "开发：./dev.sh"
echo "停止：./stop.sh"
