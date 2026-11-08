#!/usr/bin/env bash
set -euo pipefail

# 一键跨平台静态编译 WAA Signer 服务
# 支持环境: Android (Termux arm64), Linux (x86_64 / aarch64), macOS (arm64 / amd64)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SM_VERSION="v152.0.4-aistudio2api.1"
ARCH="$(uname -m)"
HOST_OS="$(uname -s | tr '[:upper:]' '[:lower:]')"

case "$ARCH" in
  x86_64|amd64)
    GO_ARCH="amd64"
    ;;
  aarch64|arm64)
    GO_ARCH="arm64"
    ;;
  *)
    echo "不支持的架构: $ARCH" >&2
    exit 1
    ;;
esac

TARGET_OS="${GOOS:-$HOST_OS}"
case "$TARGET_OS" in
  linux|android)
    TARGET_GOOS="linux"
    OS_PREFIX="linux"
    ;;
  darwin)
    TARGET_GOOS="darwin"
    OS_PREFIX="darwin"
    ;;
  windows|mingw*|msys*|cygwin*)
    TARGET_GOOS="windows"
    OS_PREFIX="windows"
    ;;
  *)
    echo "不支持的操作系统: $TARGET_OS" >&2
    exit 1
    ;;
esac

SM_ARCH="${OS_PREFIX}-${GO_ARCH}"
DEPS_DIR="${SCRIPT_DIR}/.deps/spidermonkey/${SM_ARCH}"
mkdir -p "$DEPS_DIR"

if [ ! -f "$DEPS_DIR/lib/libjs_static.a" ]; then
  TARBALL="spidermonkey-${SM_VERSION}-${SM_ARCH}.tar.gz"
  DOWNLOAD_URL="https://github.com/Mag1cFall/firefox/releases/download/${SM_VERSION}/${TARBALL}"
  echo ">>> 正在下载 SpiderMonkey 静态依赖库: ${DOWNLOAD_URL}"
  PROXY="${AISTUDIO_PROXY:-${HTTPS_PROXY:-${https_proxy:-${HTTP_PROXY:-${http_proxy:-}}}}}"
  CURL_ARGS=(-fL --retry 2 --connect-timeout 15)
  if [ -n "$PROXY" ]; then
    CURL_ARGS+=(-x "$PROXY")
  fi
  curl "${CURL_ARGS[@]}" "$DOWNLOAD_URL" | tar -xz -C "$DEPS_DIR"
  echo ">>> SpiderMonkey 解压完成: ${DEPS_DIR}"
fi

# 检查编译器
if command -v zig >/dev/null 2>&1; then
  case "$TARGET_GOOS" in
    linux)   ZIG_TARGET="${GO_ARCH}-linux-musl" ;;
    darwin)  ZIG_TARGET="${GO_ARCH}-macos-none" ;;
    windows) ZIG_TARGET="${GO_ARCH}-windows-gnu" ;;
  esac
  CC_CMD="zig cc -target ${ZIG_TARGET}"
  CXX_CMD="zig c++ -target ${ZIG_TARGET}"
  EXTLDFLAGS="-static -Wl,--strip-all"
elif command -v clang >/dev/null 2>&1; then
  CC_CMD="clang"
  CXX_CMD="clang++"
  EXTLDFLAGS="-Wl,--strip-all"
elif command -v gcc >/dev/null 2>&1; then
  CC_CMD="gcc"
  CXX_CMD="g++"
  EXTLDFLAGS="-Wl,--strip-all"
else
  echo "错误: 未找到 zig, clang 或 gcc 编译器。请先安装 (例如: pkg install zig 或 apt install clang)" >&2
  exit 1
fi

mkdir -p bin
BIN_NAME="waa-signer"
if [ "$TARGET_GOOS" = "windows" ]; then
  BIN_NAME="waa-signer.exe"
fi

echo ">>> 开始编译 waa-signer (CGO_ENABLED=1 GOOS=${TARGET_GOOS} GOARCH=${GO_ARCH})..."
CGO_ENABLED=1 GOOS="${TARGET_GOOS}" GOARCH="${GO_ARCH}" \
  CC="${CC_CMD}" \
  CXX="${CXX_CMD}" \
  CGO_CXXFLAGS="-I${DEPS_DIR}/include" \
  CGO_LDFLAGS="-L${DEPS_DIR}/lib" \
  go build -trimpath -tags netgo,osusergo \
  -ldflags="-s -w -linkmode external -extldflags '${EXTLDFLAGS}'" \
  -o "bin/${BIN_NAME}" ./cmd/waa-signer

echo ">>> 编译成功: ${SCRIPT_DIR}/bin/${BIN_NAME}"
ls -lh "bin/${BIN_NAME}"
