#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/backend/.venv"
ENGINE_DIR="$ROOT/bin/opencode"
ENGINE="$ENGINE_DIR/opencode"

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "缺少必要命令：$1" >&2
        exit 1
    }
}

require_command python3
require_command curl
require_command tar

disable_unavailable_local_proxies() {
    local variable proxy endpoint host port
    for variable in HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy; do
        proxy="${!variable:-}"
        [[ -n "$proxy" ]] || continue

        endpoint="${proxy#*://}"
        endpoint="${endpoint##*@}"
        endpoint="${endpoint%%/*}"
        if [[ "$endpoint" =~ ^(127\.0\.0\.1|localhost):([0-9]+)$ ]]; then
            host="${BASH_REMATCH[1]}"
            port="${BASH_REMATCH[2]}"
            if ! (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null; then
                echo "检测到不可用的本地代理 $proxy，已忽略 $variable 并改用直连。" >&2
                unset "$variable"
            fi
        fi
    done
}

disable_unavailable_local_proxies

case "$(uname -m)" in
    x86_64|amd64) arch="x64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) echo "不支持的 Linux 架构：$(uname -m)" >&2; exit 1 ;;
esac

target="linux-$arch"
if [[ "$arch" == "x64" ]] && ! grep -qwi avx2 /proc/cpuinfo 2>/dev/null; then
    target+="-baseline"
fi
if [[ -f /etc/alpine-release ]] || (command -v ldd >/dev/null 2>&1 && ldd --version 2>&1 | grep -qi musl); then
    target+="-musl"
fi

mkdir -p "$ENGINE_DIR" "$ROOT/logs" "$ROOT/data/opencode" "$ROOT/workspace/uploads"

if [[ "${INSTALL_OPENCODE:-0}" == "1" && ! -x "$ENGINE" ]]; then
    archive="opencode-$target.tar.gz"
    url="${OPENCODE_DOWNLOAD_URL:-https://github.com/anomalyco/opencode/releases/latest/download/$archive}"
    temp_dir="$(mktemp -d)"
    trap 'rm -rf "$temp_dir"' EXIT

    echo "[1/4] 下载 OpenCode Linux 引擎 ($target)..."
    curl --fail --location --retry 3 --output "$temp_dir/$archive" "$url"
    tar -xzf "$temp_dir/$archive" -C "$temp_dir"
    install -m 755 "$temp_dir/opencode" "$ENGINE"
    echo "  ✓ OpenCode 已安装"
elif [[ -x "$ENGINE" ]]; then
    echo "[1/4] OpenCode 已存在，跳过"
else
    echo "[1/4] IDEA Workflow 不依赖 OpenCode 引擎，跳过（如需旧功能可设 INSTALL_OPENCODE=1）"
fi

if [[ ! -d "$VENV" ]]; then
    echo "[2/4] 创建 Python 虚拟环境..."
    python3 -m venv "$VENV"
fi
echo "[2/4] 安装 Python 依赖..."
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r "$ROOT/backend/requirements.txt"
echo "  ✓ 依赖已安装"

echo "[3/4] 运行目录已就绪"
if [[ -f "$ROOT/data/opencode/auth.json" ]]; then
    echo "[4/4] 已检测到 API 配置"
else
    echo "[4/4] 尚未配置 API Key；请设置 DEEPSEEK_API_KEY 或 data/opencode/auth.json"
fi

echo
echo "安装完成！"
echo "启动：./start.sh"
echo "开发：./dev.sh"
echo "停止：./stop.sh"
