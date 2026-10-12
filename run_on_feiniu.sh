#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "[*] 正在飞牛本机通过 Xvfb 虚拟真彩桌面 (1920x1080x24) 启动有头版 Patchright 注册机..."
exec xvfb-run -a --server-args="-screen 0 1920x1080x24 -ac +extension GLX +render -noreset" python3 main.py "$@"
