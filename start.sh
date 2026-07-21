#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-docker}"

start_local() {
    local port="${PORT:-8001}"
    local venv="$ROOT/backend/.venv"
    local pid_file="$ROOT/logs/server.pid"
    if [[ ! -x "$venv/bin/python" ]]; then
        echo "本地环境未就绪，请先运行：./install.sh" >&2
        exit 1
    fi
    mkdir -p "$ROOT/logs" "$ROOT/workspace/uploads" "$ROOT/data/aifpatent"
    local ready_url="http://127.0.0.1:$port/openapi.json"
    if ! curl --noproxy "*" --fail --silent --max-time 2 "$ready_url" >/dev/null; then
        nohup "$venv/bin/python" -m uvicorn main:app --port "$port" --app-dir "$ROOT/backend" \
            >"$ROOT/logs/server.log" 2>"$ROOT/logs/server.err" < /dev/null &
        echo $! > "$pid_file"
        for _ in {1..20}; do
            curl --noproxy "*" --fail --silent --max-time 1 "$ready_url" >/dev/null && break
            sleep 0.5
        done
        curl --noproxy "*" --fail --silent --max-time 2 "$ready_url" >/dev/null
    fi
    echo "AIFPatent 本地兼容模式已启动：http://localhost:$port"
}

if [[ "$MODE" == "--local" ]]; then
    start_local
    exit 0
fi
if [[ "$MODE" != "docker" ]]; then
    echo "用法：./start.sh [--local]" >&2
    exit 2
fi

for command in docker python3 curl; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "缺少必要命令：$command" >&2
        exit 1
    fi
done
if ! docker compose version >/dev/null 2>&1; then
    echo "需要 Docker Compose plugin（docker compose）" >&2
    exit 1
fi

MIN_FREE_KB=$((5 * 1024 * 1024))
available_kb="$(df -Pk "$ROOT" | awk 'NR == 2 {print $4}')"
if [[ ! "$available_kb" =~ ^[0-9]+$ ]] || (( available_kb < MIN_FREE_KB )); then
    echo "磁盘可用空间不足 5GiB，拒绝启动以保护服务器。" >&2
    exit 1
fi

PYTHON="$(command -v python3)"
"$PYTHON" "$ROOT/tools/rag_infra.py" init

started=false
cleanup_on_error() {
    if [[ "$started" == true ]]; then
        "$PYTHON" "$ROOT/tools/rag_infra.py" down >/dev/null 2>&1 || true
    fi
}
trap cleanup_on_error ERR

"$PYTHON" "$ROOT/tools/rag_infra.py" up
started=true
"$PYTHON" "$ROOT/tools/rag_infra.py" migrate

env_file="$ROOT/deploy/rag/rag.env"
port="$(awk -F= '$1 == "AIFPATENT_APP_PORT" {print $2}' "$env_file")"
port="${port:-8001}"
ready_url="http://127.0.0.1:$port/openapi.json"
for _ in {1..30}; do
    curl --noproxy "*" --fail --silent --max-time 2 "$ready_url" >/dev/null && break
    sleep 1
done
curl --noproxy "*" --fail --silent --max-time 3 "$ready_url" >/dev/null
trap - ERR

echo "AIFPatent 首次报告与证据追问 RAG 已启动：http://localhost:$port"
echo "模型 Base URL、Model 和 API Key 请在网页中按 Run 输入；刷新后不会保留。"
echo "停止服务并保留数据：./stop.sh"
