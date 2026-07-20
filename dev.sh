#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8001}"
VENV="$ROOT/backend/.venv"

if [[ ! -x "$VENV/bin/python" ]]; then
    echo "环境未就绪，请先运行：./install.sh" >&2
    exit 1
fi

mkdir -p "$ROOT/logs" "$ROOT/workspace/uploads" "$ROOT/data/aifpatent"
echo "AIFPatent 专利工作台：http://localhost:$PORT"
echo "日志：当前终端和 logs/aifpatent.log"
echo "退出：Ctrl+C"
if command -v xdg-open >/dev/null 2>&1; then
    (sleep 2; xdg-open "http://localhost:$PORT" >/dev/null 2>&1) &
fi
exec "$VENV/bin/python" -m uvicorn main:app --port "$PORT" --app-dir "$ROOT/backend"
