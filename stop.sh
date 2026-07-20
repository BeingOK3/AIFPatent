#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT/logs/server.pid"

if [[ ! -f "$PID_FILE" ]]; then
    echo "没有由 start.sh 管理的运行中服务"
    exit 0
fi

pid="$(<"$PID_FILE")"
if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    for _ in {1..20}; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.1
    done
    if kill -0 "$pid" 2>/dev/null; then
        kill -KILL "$pid"
    fi
    echo "服务已停止（PID: $pid）"
else
    echo "服务未在运行"
fi
rm -f "$PID_FILE"
