#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-docker}"

if [[ "$MODE" == "--local" ]]; then
    pid_file="$ROOT/logs/server.pid"
    if [[ ! -f "$pid_file" ]]; then
        echo "没有由 start.sh --local 管理的运行中服务"
        exit 0
    fi
    pid="$(<"$pid_file")"
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        for _ in {1..20}; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.1
        done
        kill -0 "$pid" 2>/dev/null && kill -KILL "$pid"
    fi
    rm -f "$pid_file"
    echo "本地兼容模式已停止"
    exit 0
fi
if [[ "$MODE" != "docker" ]]; then
    echo "用法：./stop.sh [--local]" >&2
    exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "缺少必要命令：python3" >&2
    exit 1
fi

"$(command -v python3)" "$ROOT/tools/rag_infra.py" down
echo "AIFPatent Docker 服务已停止；PostgreSQL、Redis、MinIO 和应用数据卷均已保留。"
