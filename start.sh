#!/usr/bin/env bash
# Unified one-command launcher for aistudio-api (Linux / macOS / Android Termux).

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
cd "$PROJECT_ROOT"

PORT="${AISTUDIO_PORT:-8080}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --port)
      PORT="$2"
      shift 2
      ;;
    -h|--help)
      echo "用法: bash start.sh [--port 8080]"
      exit 0
      ;;
    *)
      shift
      ;;
  esac
done

# Verify local IPC signer supports stdio IPC mode (catches missing or stale HTTP-mode binaries)
signer_ready() {
  local bin="${PROJECT_ROOT}/waa-signer/bin/waa-signer"
  [ -x "$bin" ] || return 1
  local check_out
  check_out="$("$bin" -check 2>/dev/null || true)"
  [[ "$check_out" == *'"mode":"ipc"'* ]]
}

# Auto-heal: if virtual env or local IPC signer is missing/incompatible, run install.sh first
if [ ! -x "${PROJECT_ROOT}/.venv/bin/python" ]; then
  echo "[start] 检测到尚未完成初始化，正在自动执行安装..."
  bash "${PROJECT_ROOT}/install.sh"
elif ! signer_ready; then
  echo "[start] 检测到本地 WAA IPC 签名器缺失或为旧版/不兼容二进制，正在自动重新构建..."
  bash "${PROJECT_ROOT}/waa-signer/build.sh"
fi

if [ -n "${PREFIX:-}" ] && [[ "${PREFIX}" == *"/com.termux"* ]]; then
  case ":$PATH:" in
    *":${PREFIX}/bin:"*) ;;
    *) export PATH="${PREFIX}/bin:$PATH" ;;
  esac
else
  for d in "${HOME}/.local/bin" "${HOME}/.cargo/bin"; do
    if [ -d "$d" ]; then
      case ":$PATH:" in
        *":$d:"*) ;;
        *) export PATH="$d:$PATH" ;;
      esac
    fi
  done
fi

echo "[start] 正在启动 aistudio-api (端口: ${PORT}, 管理面板: http://localhost:${PORT})..."
exec uv run --no-sync python main.py server --port "${PORT}"
