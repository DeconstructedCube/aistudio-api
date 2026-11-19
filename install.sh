#!/usr/bin/env bash
# Unified one-command installer and builder for aistudio-api.
# Covers Android (Termux), Linux (x86_64/arm64), macOS (arm64/Intel), and Git Bash/WSL.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
cd "$PROJECT_ROOT"

START_AFTER=0
FORCE_DEPS=0
RUN_TESTS=0
PORT="8080"
PROXY_ARG=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --start)
      START_AFTER=1
      shift
      ;;
    --force-deps|--rebuild)
      FORCE_DEPS=1
      shift
      ;;
    --test)
      RUN_TESTS=1
      shift
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --proxy)
      PROXY_ARG=(--proxy "$2")
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
用法: bash install.sh [选项]
  --start          安装编译完成后立即启动服务
  --rebuild        强制重新下载依赖并编译本地签名器
  --test           运行 Go + Python 测试验证
  --port <端口>    配合 --start 使用，指定监听端口 (默认 8080)
  --proxy <URL>    指定下载代理 (如 http://127.0.0.1:7890)
EOF
      exit 0
      ;;
    *)
      echo "未知选项: $1 (使用 --help 查看帮助)" >&2
      exit 1
      ;;
  esac
done

echo ""
echo "=================================================================="
echo "  aistudio-api 全平台一键安装与本地签名引擎构建"
echo "=================================================================="
echo ""

echo "[1/3] 初始化 Python 与依赖环境..."
bash "${PROJECT_ROOT}/scripts/setup-env.sh" "${PROXY_ARG[@]}"

echo ""
echo "[2/3] 构建本地 WAA IPC 签名引擎 (waa-signer)..."
BUILD_ARGS=("${PROXY_ARG[@]}")
if [ "$FORCE_DEPS" -eq 1 ]; then
  BUILD_ARGS+=(--force-deps)
fi
bash "${PROJECT_ROOT}/waa-signer/build.sh" "${BUILD_ARGS[@]}"

echo ""
echo "[3/3] 检查默认配置..."
if [ ! -f "${PROJECT_ROOT}/config.yaml" ] && [ -f "${PROJECT_ROOT}/defaults/config.yaml" ]; then
  cp "${PROJECT_ROOT}/defaults/config.yaml" "${PROJECT_ROOT}/config.yaml"
  echo "已初始化默认配置: config.yaml"
fi

if [ "$RUN_TESTS" -eq 1 ]; then
  echo ""
  echo "[验证] 运行 Go 与 Python 测试套件..."
  bash "${PROJECT_ROOT}/waa-signer/build.sh" --test "${PROXY_ARG[@]}"
  uv run --no-sync pytest
fi

echo ""
echo "=================================================================="
echo "  安装与编译全部完成!"
echo "  日常启动命令: bash start.sh"
echo "  访问管理面板: http://localhost:${PORT}"
if [ -n "${PREFIX:-}" ] && [[ "${PREFIX}" == *"/com.termux"* ]]; then
  echo "  [Termux 提示] 下拉通知栏点击 Acquire Wakelock 并关闭电池优化可防止锁屏杀后台。"
fi
echo "=================================================================="
echo ""

if [ "$START_AFTER" -eq 1 ]; then
  exec bash "${PROJECT_ROOT}/start.sh" --port "$PORT"
fi
