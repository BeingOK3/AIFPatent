#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8001}"
VENV="$ROOT/backend/.venv"
PID_FILE="$ROOT/logs/server.pid"

if [[ ! -x "$VENV/bin/python" ]]; then
    echo "环境未就绪，请先运行：./install.sh" >&2
    exit 1
fi

mkdir -p "$ROOT/logs" "$ROOT/workspace/uploads" "$ROOT/data/opencode"
READY_URL="http://127.0.0.1:$PORT/openapi.json"
if curl --noproxy "*" --fail --silent --max-time 2 "$READY_URL" >/dev/null; then
    echo "服务已在运行：http://localhost:$PORT"
else
    echo "启动服务..."
    nohup "$VENV/bin/python" -m uvicorn main:app --port "$PORT" --app-dir "$ROOT/backend" \
        >"$ROOT/logs/server.log" 2>"$ROOT/logs/server.err" < /dev/null &
    echo $! > "$PID_FILE"
    ready=false
    for _ in {1..20}; do
        if curl --noproxy "*" --fail --silent --max-time 1 "$READY_URL" >/dev/null; then
            ready=true
            break
        fi
        sleep 0.5
    done
    if [[ "$ready" != true ]]; then
        echo "服务启动失败，请查看 logs/server.err" >&2
        exit 1
    fi
fi

echo "AI4P 专利工作台已启动：http://localhost:$PORT"
echo "日志：logs/server.log、logs/ai4p.log"
echo "停止：./stop.sh"
if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://localhost:$PORT" >/dev/null 2>&1 &
fi
