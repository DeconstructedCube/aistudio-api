#!/usr/bin/env bash
set -euo pipefail

# Defensive cross-platform static builder for waa-signer (IPC mode).
# Supports: Android (Termux aarch64), Linux (x86_64/arm64), macOS (arm64/amd64),
# and cross-compiling Windows x64 from Unix/MSYS2.
#
# Flags:
#   --test          Run Go unit tests instead of building the binary
#   --proxy <url>   Explicit HTTP/HTTPS/SOCKS5 proxy URL
#   --force-deps    Re-download and re-extract SpiderMonkey static libraries

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
if [ ! -f "${SCRIPT_DIR}/go.mod" ] && [ -f "${PWD}/waa-signer/go.mod" ]; then
  SCRIPT_DIR="${PWD}/waa-signer"
fi
cd "$SCRIPT_DIR"

SM_VERSION="v152.0.4-aistudio2api.1"
ZIG_FALLBACK_VERSION="0.17.1"
MIN_GO_MINOR_TOOLCHAIN=21

RUN_TESTS=0
FORCE_DEPS=0
EXPLICIT_PROXY=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --test)
      RUN_TESTS=1
      shift
      ;;
    --force-deps)
      FORCE_DEPS=1
      shift
      ;;
    --proxy)
      if [ "$#" -lt 2 ]; then
        echo "错误: --proxy 需要参数" >&2
        exit 1
      fi
      EXPLICIT_PROXY="$2"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
用法: bash waa-signer/build.sh [--test] [--force-deps] [--proxy <url>]
  --test        使用自动配置好的 CGO 环境运行 Go 单元测试
  --force-deps  强制重新下载并校验 SpiderMonkey 静态库
  --proxy       指定 HTTP/HTTPS/SOCKS5 下载代理
EOF
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      exit 1
      ;;
  esac
done

log()  { printf '[waa-signer] %s\n' "$*" >&2; }
warn() { printf '[waa-signer][警告] %s\n' "$*" >&2; }
die()  { printf '[waa-signer][错误] %s\n' "$*" >&2; exit 1; }

is_termux() {
  [ -n "${PREFIX:-}" ] && [[ "$PREFIX" == *"/com.termux"* ]] && return 0
  [ -d "/data/data/com.termux/files/usr" ] && command -v pkg >/dev/null 2>&1 && return 0
  return 1
}

ensure_path_dirs() {
  local d
  for d in "$@"; do
    if [ -d "$d" ]; then
      case ":$PATH:" in
        *":$d:"*) ;;
        *) export PATH="$d:$PATH" ;;
      esac
    fi
  done
}

ensure_path_dirs \
  "${HOME}/.local/bin" \
  "${HOME}/.local/share/aistudio-tools/go/bin" \
  "${HOME}/.local/share/aistudio-tools/zig" \
  "${SCRIPT_DIR}/.deps/tools/go/bin" \
  "${SCRIPT_DIR}/.deps/tools/zig" \
  "/usr/local/go/bin" \
  "/opt/homebrew/bin" \
  "/usr/local/bin"

tcp_port_open() {
  local host="$1"
  local port="$2"
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "import socket,sys; s=socket.socket(); s.settimeout(0.25); rc=s.connect_ex(('${host}', ${port})); s.close(); sys.exit(0 if rc==0 else 1)" 2>/dev/null
    return $?
  fi
  if command -v nc >/dev/null 2>&1; then
    nc -z -w 1 "$host" "$port" >/dev/null 2>&1
    return $?
  fi
  return 1
}

detect_proxy() {
  if [ -n "$EXPLICIT_PROXY" ]; then
    printf '%s' "$EXPLICIT_PROXY"
    return
  fi
  local v
  for v in "${AISTUDIO_PROXY:-}" "${HTTPS_PROXY:-}" "${https_proxy:-}" "${HTTP_PROXY:-}" "${http_proxy:-}"; do
    if [ -n "$v" ]; then
      printf '%s' "$v"
      return
    fi
  done
  if [ "$(uname -s)" = "Darwin" ] && command -v scutil >/dev/null 2>&1; then
    local sc_out enabled host port
    sc_out="$(scutil --proxy 2>/dev/null || true)"
    enabled="$(printf '%s\n' "$sc_out" | awk '/HTTPSEnable/{print $3; exit}')"
    if [ "$enabled" = "1" ]; then
      host="$(printf '%s\n' "$sc_out" | awk '/HTTPSProxy/{print $3; exit}')"
      port="$(printf '%s\n' "$sc_out" | awk '/HTTPSPort/{print $3; exit}')"
      if [ -n "$host" ] && [ -n "$port" ]; then
        printf 'http://%s:%s' "$host" "$port"
        return
      fi
    fi
    enabled="$(printf '%s\n' "$sc_out" | awk '/HTTPEnable/{print $3; exit}')"
    if [ "$enabled" = "1" ]; then
      host="$(printf '%s\n' "$sc_out" | awk '/HTTPProxy/{print $3; exit}')"
      port="$(printf '%s\n' "$sc_out" | awk '/HTTPPort/{print $3; exit}')"
      if [ -n "$host" ] && [ -n "$port" ]; then
        printf 'http://%s:%s' "$host" "$port"
        return
      fi
    fi
  fi
  local p
  for p in 7890 7897 10808 10809 2080; do
    if tcp_port_open "127.0.0.1" "$p"; then
      printf 'http://127.0.0.1:%s' "$p"
      return
    fi
  done
}

PROXY="$(detect_proxy)"
if [ -n "$PROXY" ]; then
  log "使用代理: $PROXY"
  export HTTPS_PROXY="${HTTPS_PROXY:-$PROXY}"
  export HTTP_PROXY="${HTTP_PROXY:-$PROXY}"
fi

curl_fetch() {
  local url="$1"
  local out="$2"
  local args=(
    -fL --retry 2 --connect-timeout 10 --max-time 240
    -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    -o "$out"
  )
  if [ -n "$PROXY" ]; then
    args=(-x "$PROXY" "${args[@]}")
  fi
  curl "${args[@]}" "$url"
}

verify_tar_gz() {
  local f="$1"
  local min_bytes="${2:-1000}"
  [ -f "$f" ] || return 1
  local sz
  sz="$(wc -c < "$f" | tr -d '[:space:]')"
  [ "${sz:-0}" -ge "$min_bytes" ] || return 1
  gzip -t "$f" >/dev/null 2>&1 || return 1
  tar -tzf "$f" >/dev/null 2>&1 || return 1
  return 0
}

verify_tar_xz() {
  local f="$1"
  local min_bytes="${2:-1000}"
  [ -f "$f" ] || return 1
  local sz
  sz="$(wc -c < "$f" | tr -d '[:space:]')"
  [ "${sz:-0}" -ge "$min_bytes" ] || return 1
  tar -tJf "$f" >/dev/null 2>&1 || return 1
  return 0
}

# --- Host & Target Detection ---
HOST_ARCH="$(uname -m)"
HOST_OS="$(uname -s | tr '[:upper:]' '[:lower:]')"

if is_termux; then
  case "$SCRIPT_DIR" in
    /sdcard/*|/storage/*|/mnt/sdcard/*)
      warn "检测到项目位于 Android 共享存储 (${SCRIPT_DIR})，该分区通常为 noexec，建议移至 Termux 主目录 (~) 下运行。"
      ;;
  esac
  USER_BITS="$(getconf LONG_BIT 2>/dev/null || true)"
  DPKG_ARCH="$(dpkg --print-architecture 2>/dev/null || true)"
  if [ "$USER_BITS" = "32" ] || [ "$DPKG_ARCH" = "arm" ] || [ "$DPKG_ARCH" = "i686" ]; then
    die "检测到 32 位 Termux 用户态 (dpkg=${DPKG_ARCH:-unknown}, LONG_BIT=${USER_BITS:-unknown})。SpiderMonkey 152 仅支持 64 位 arm64-v8a，请从 F-Droid 或 GitHub Releases 安装 64 位 arm64-v8a 版 Termux。"
  fi
fi

case "$HOST_ARCH" in
  x86_64|amd64)
    HOST_GO_ARCH="amd64"
    HOST_ZIG_ARCH="x86_64"
    ;;
  aarch64|arm64)
    HOST_GO_ARCH="arm64"
    HOST_ZIG_ARCH="aarch64"
    ;;
  *)
    die "不支持的 CPU 架构: $HOST_ARCH (仅支持 x86_64 与 arm64/aarch64)"
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
    die "不支持的操作系统: $TARGET_OS"
    ;;
esac

GO_ARCH="${GOARCH:-$HOST_GO_ARCH}"
case "$GO_ARCH" in
  amd64) ZIG_ARCH="x86_64" ;;
  arm64) ZIG_ARCH="aarch64" ;;
  *) die "不支持的目标架构: $GO_ARCH" ;;
esac
if [ "$TARGET_GOOS" = "windows" ] && [ "$GO_ARCH" != "amd64" ]; then
  warn "Windows 目标强制使用 amd64 (SpiderMonkey 与 CGO 约束)"
  GO_ARCH="amd64"
  ZIG_ARCH="x86_64"
fi

# --- Go Toolchain Defense ---
sanitize_go_env() {
  if [ -n "${GOROOT:-}" ] && [ ! -d "${GOROOT}" ]; then
    warn "检测到无效 GOROOT=${GOROOT}，已自动清除"
    unset GOROOT
  fi
  if command -v go >/dev/null 2>&1; then
    local real_goroot
    real_goroot="$(go env GOROOT 2>/dev/null || true)"
    if [ -n "${GOROOT:-}" ] && [ -n "$real_goroot" ] && [ "${GOROOT}" != "$real_goroot" ]; then
      warn "检测到 GOROOT 冲突 (${GOROOT} != ${real_goroot})，已自动清除"
      unset GOROOT
    fi
  fi
}

go_minor_version() {
  local ver_str
  ver_str="$(go version 2>/dev/null | sed -n 's/.*go\([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1.\2/p')"
  printf '%s' "$ver_str"
}

go_version_meets_min() {
  local ver="$1"
  local req_major="$2"
  local req_minor="$3"
  [ -n "$ver" ] || return 1
  local major="${ver%%.*}"
  local minor="${ver#*.}"
  [ "$major" -gt "$req_major" ] && return 0
  [ "$major" -eq "$req_major" ] && [ "$minor" -ge "$req_minor" ] && return 0
  return 1
}

install_portable_go() {
  local go_os=""
  case "$HOST_OS" in
    linux) go_os="linux" ;;
    darwin) go_os="darwin" ;;
    *) return 1 ;;
  esac

  local go_ver=""
  local v_url
  for v_url in "https://go.dev/VERSION?m=text" "https://golang.google.cn/VERSION?m=text"; do
    local tmp_v
    tmp_v="$(curl -fsSL --connect-timeout 8 --max-time 15 ${PROXY:+-x "$PROXY"} "$v_url" 2>/dev/null | head -n 1 | tr -d '\r\n' || true)"
    if [[ "$tmp_v" == go1.* ]]; then
      go_ver="$tmp_v"
      break
    fi
  done
  if [ -z "$go_ver" ]; then
    go_ver="go1.24.4"
  fi

  local tools_dir="${SCRIPT_DIR}/.deps/tools"
  local go_root="${tools_dir}/go"
  local tar_name="${go_ver}.${go_os}-${HOST_GO_ARCH}.tar.gz"
  local tmp_tar="${tools_dir}/${tar_name}"
  mkdir -p "$tools_dir"

  local dl_urls=(
    "https://go.dev/dl/${tar_name}"
    "https://golang.google.cn/dl/${tar_name}"
  )
  local ok=0
  local u
  for u in "${dl_urls[@]}"; do
    log "正在下载独立 Go 工具链 (${go_ver}): $u"
    rm -f "$tmp_tar"
    if curl_fetch "$u" "$tmp_tar" && verify_tar_gz "$tmp_tar" 20000000; then
      ok=1
      break
    fi
    rm -f "$tmp_tar"
  done
  [ "$ok" -eq 1 ] || return 1

  rm -rf "$go_root"
  tar -xzf "$tmp_tar" -C "$tools_dir"
  rm -f "$tmp_tar"
  export GOROOT="$go_root"
  export PATH="${go_root}/bin:${PATH}"
  log "已就绪独立 Go 工具链: $(go version)"
}

ensure_go() {
  sanitize_go_env
  local cur_ver=""
  if command -v go >/dev/null 2>&1; then
    cur_ver="$(go_minor_version)"
  fi

  if [ -z "$cur_ver" ] || ! go_version_meets_min "$cur_ver" 1 "$MIN_GO_MINOR_TOOLCHAIN"; then
    if [ -n "$cur_ver" ]; then
      warn "检测到 Go 版本过低 (go${cur_ver} < go1.${MIN_GO_MINOR_TOOLCHAIN})，需要升级。"
    else
      log "未检测到 Go，正在自动配置 Go 工具链..."
    fi
    if is_termux; then
      pkg update -y
      pkg install -y golang || pkg upgrade -y golang
      sanitize_go_env
      cur_ver="$(go_minor_version)"
    elif install_portable_go; then
      sanitize_go_env
      cur_ver="$(go_minor_version)"
    elif [ "$HOST_OS" = "darwin" ] && command -v brew >/dev/null 2>&1; then
      brew install go || brew upgrade go
      sanitize_go_env
      cur_ver="$(go_minor_version)"
    fi
  fi

  [ -n "$cur_ver" ] || die "无法安装 Go 1.21+，请手动安装后重试。"
  go_version_meets_min "$cur_ver" 1 "$MIN_GO_MINOR_TOOLCHAIN" || die "当前 Go 版本 go${cur_ver} 过低，请升级至 Go 1.27+。"

  local req_ver
  req_ver="$(sed -n 's/^go[[:space:]]\([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' "${SCRIPT_DIR}/go.mod" | head -n 1)"
  if [ -n "$req_ver" ]; then
    local req_major="${req_ver%%.*}"
    local req_minor="${req_ver#*.}"
    if ! go_version_meets_min "$cur_ver" "$req_major" "$req_minor"; then
      log "当前 Go 为 go${cur_ver} (go.mod 需要 go${req_ver})，已启用 GOTOOLCHAIN=auto 自动按需拉取工具链"
      export GOTOOLCHAIN=auto
    fi
  fi

  # Configure GOPROXY mirror fallback if proxy.golang.org is unreachable
  if [ -z "${GOPROXY:-}" ] || [ "${GOPROXY}" = "https://proxy.golang.org,direct" ]; then
    if ! curl -fsSL --connect-timeout 3 --max-time 5 ${PROXY:+-x "$PROXY"} "https://proxy.golang.org" >/dev/null 2>&1; then
      log "proxy.golang.org 不可直连，自动启用国内 Go 模块镜像 (goproxy.cn)"
      export GOPROXY="https://goproxy.cn,https://mirrors.aliyun.com/goproxy/,direct"
    fi
  fi
}

# --- C/C++ Compiler Defense (Zig / Clang) ---
install_portable_zig() {
  [ "$HOST_OS" = "linux" ] || return 1
  command -v xz >/dev/null 2>&1 || return 1
  local tools_dir="${SCRIPT_DIR}/.deps/tools"
  local zig_dir="${tools_dir}/zig"
  local tar_name="zig-${HOST_ZIG_ARCH}-linux-${ZIG_FALLBACK_VERSION}.tar.xz"
  local tmp_tar="${tools_dir}/${tar_name}"
  mkdir -p "$tools_dir"

  local dl_urls=(
    "https://ziglang.org/download/${ZIG_FALLBACK_VERSION}/${tar_name}"
    "https://ghproxy.net/https://ziglang.org/download/${ZIG_FALLBACK_VERSION}/${tar_name}"
  )
  local ok=0
  local u
  for u in "${dl_urls[@]}"; do
    log "正在下载免配置静态编译器 Zig ${ZIG_FALLBACK_VERSION}: $u"
    rm -f "$tmp_tar"
    if curl_fetch "$u" "$tmp_tar" && verify_tar_xz "$tmp_tar" 15000000; then
      ok=1
      break
    fi
    rm -f "$tmp_tar"
  done
  [ "$ok" -eq 1 ] || return 1

  rm -rf "$zig_dir"
  mkdir -p "$zig_dir"
  tar -xJf "$tmp_tar" --strip-components=1 -C "$zig_dir"
  rm -f "$tmp_tar"
  [ -x "${zig_dir}/zig" ] || return 1
  export PATH="${zig_dir}:${PATH}"
  log "已就绪独立 Zig 编译器: $(zig version)"
}

setup_compiler() {
  if [ "$TARGET_GOOS" = "darwin" ]; then
    if [ "$HOST_OS" = "darwin" ]; then
      if ! xcode-select -p >/dev/null 2>&1; then
        warn "macOS 未安装 Xcode Command Line Tools，正在触发安装弹窗..."
        xcode-select --install >/dev/null 2>&1 || true
        die "请在弹出的系统窗口中完成 Command Line Tools 安装后重新运行本脚本。"
      fi
      if command -v clang >/dev/null 2>&1; then
        CC_CMD="clang"
        CXX_CMD="clang++"
        EXTLDFLAGS="-Wl,-S,-x"
        return
      fi
    fi
    if command -v zig >/dev/null 2>&1; then
      local sdk_flag=""
      if [ "$HOST_OS" = "darwin" ] && command -v xcrun >/dev/null 2>&1; then
        local sdk_path
        sdk_path="$(xcrun --show-sdk-path 2>/dev/null || true)"
        if [ -n "$sdk_path" ]; then
          sdk_flag=" -isysroot ${sdk_path} -F${sdk_path}/System/Library/Frameworks"
        fi
      fi
      CC_CMD="zig cc -target ${ZIG_ARCH}-macos-none${sdk_flag}"
      CXX_CMD="zig c++ -target ${ZIG_ARCH}-macos-none${sdk_flag}"
      EXTLDFLAGS="-Wl,-S,-x"
      return
    fi
    die "macOS 需要 clang (Xcode CLT) 或 zig 编译器。"
  fi

  if ! command -v zig >/dev/null 2>&1; then
    if is_termux; then
      log "Termux 环境自动安装 zig..."
      pkg install -y zig
    else
      install_portable_zig || true
    fi
  fi

  if command -v zig >/dev/null 2>&1; then
    case "$TARGET_GOOS" in
      linux)
        ZIG_TARGET="${ZIG_ARCH}-linux-musl"
        EXTLDFLAGS="-static -Wl,--strip-all"
        ;;
      windows)
        ZIG_TARGET="${ZIG_ARCH}-windows-gnu"
        EXTLDFLAGS="-static -Wl,--strip-all"
        ;;
    esac
    CC_CMD="zig cc -target ${ZIG_TARGET}"
    CXX_CMD="zig c++ -target ${ZIG_TARGET}"
    return
  fi

  if is_termux; then
    die "Termux 环境必须使用 zig (aarch64-linux-musl) 进行静态链接，不可回退 Bionic clang。请执行 pkg update && pkg install -y zig 后重试。"
  fi

  if command -v clang >/dev/null 2>&1; then
    warn "未检测到 zig，回退使用 clang (若缺少 libc++/libstdc++ 静态库可能会链接失败)"
    CC_CMD="clang"
    CXX_CMD="clang++"
    EXTLDFLAGS="-Wl,--strip-all"
    return
  fi

  if command -v gcc >/dev/null 2>&1 && [ "$TARGET_GOOS" = "linux" ]; then
    warn "未检测到 zig，回退使用 gcc (若缺少 libstdc++.a 可能会链接失败)"
    CC_CMD="gcc"
    CXX_CMD="g++"
    EXTLDFLAGS="-Wl,--strip-all"
    return
  fi

  die "未找到 zig 或 clang 编译器，无法继续编译。"
}

# --- SpiderMonkey Static Library Download & Integrity Verification ---
ensure_spidermonkey_deps() {
  SM_ARCH="${OS_PREFIX}-${GO_ARCH}"
  DEPS_DIR="${SCRIPT_DIR}/.deps/spidermonkey/${SM_ARCH}"
  if [ "$FORCE_DEPS" -eq 1 ]; then
    rm -rf "$DEPS_DIR"
  fi
  if [ -f "${DEPS_DIR}/lib/libjs_static.a" ] && [ -f "${DEPS_DIR}/include/jsapi.h" ]; then
    log "SpiderMonkey 静态库已就绪: ${DEPS_DIR}"
    return
  fi

  rm -rf "$DEPS_DIR"
  mkdir -p "$DEPS_DIR" "${SCRIPT_DIR}/.deps"

  local tarball="spidermonkey-${SM_VERSION}-${SM_ARCH}.tar.gz"
  local official_url="https://github.com/Mag1cFall/firefox/releases/download/${SM_VERSION}/${tarball}"
  local temp_tar="${SCRIPT_DIR}/.deps/${tarball}"

  local has_local=0
  local candidate
  for candidate in "$temp_tar" "${SCRIPT_DIR}/${tarball}" "${HOME}/Downloads/${tarball}"; do
    if verify_tar_gz "$candidate" 10000000; then
      log "检测到有效本地 SpiderMonkey 依赖包: ${candidate}"
      if [ "$candidate" != "$temp_tar" ]; then
        cp -f "$candidate" "$temp_tar"
      fi
      has_local=1
      break
    elif [ -f "$candidate" ] && [ "$candidate" = "$temp_tar" ]; then
      warn "清理损坏或未下完的缓存压缩包: $candidate"
      rm -f "$candidate"
    fi
  done

  if [ "$has_local" -ne 1 ]; then
    local download_urls=(
      "$official_url"
      "https://ghproxy.net/${official_url}"
      "https://gh-proxy.com/${official_url}"
      "https://mirror.ghproxy.com/${official_url}"
    )
    local dl_ok=0
    local url
    for url in "${download_urls[@]}"; do
      log "正在下载 SpiderMonkey 静态依赖库: ${url}"
      rm -f "$temp_tar"
      if curl_fetch "$url" "$temp_tar" && verify_tar_gz "$temp_tar" 10000000; then
        dl_ok=1
        break
      fi
      rm -f "$temp_tar"
    done
    if [ "$dl_ok" -ne 1 ]; then
      echo "" >&2
      echo "==================================================================" >&2
      echo "错误: 下载 SpiderMonkey 依赖包失败。请尝试:" >&2
      echo "  1) 设置代理重试: bash install.sh --proxy http://127.0.0.1:7890" >&2
      echo "  2) 或用浏览器手动下载下方文件并放入 ${SCRIPT_DIR}/.deps/ 目录:" >&2
      echo "     ${official_url}" >&2
      echo "==================================================================" >&2
      exit 1
    fi
  fi

  local extract_tmp="${DEPS_DIR}.extracting"
  rm -rf "$extract_tmp"
  mkdir -p "$extract_tmp"
  tar -xzf "$temp_tar" -C "$extract_tmp"
  rm -f "$temp_tar"

  if [ ! -f "${extract_tmp}/lib/libjs_static.a" ] || [ ! -f "${extract_tmp}/include/jsapi.h" ]; then
    rm -rf "$extract_tmp"
    die "解压 SpiderMonkey 后缺少 libjs_static.a 或 jsapi.h"
  fi
  rm -rf "$DEPS_DIR"
  mv "$extract_tmp" "$DEPS_DIR"

  if [ "$TARGET_GOOS" = "windows" ] && command -v zig >/dev/null 2>&1 && [ -f "${DEPS_DIR}/lib/libjsrust.a" ]; then
    zig ar d "${DEPS_DIR}/lib/libjsrust.a" "compiler_builtins-37a08fb9d355cb46.compiler_builtins.7e37c3da72dd266-cgu.150.rcgu.o" 2>/dev/null || true
  fi
  log "SpiderMonkey 解压完成: ${DEPS_DIR}"
}

ensure_go
setup_compiler
ensure_spidermonkey_deps

export CGO_ENABLED=1
export GOOS="${TARGET_GOOS}"
export GOARCH="${GO_ARCH}"
export CC="${CC_CMD}"
export CXX="${CXX_CMD}"
export CGO_CXXFLAGS="'-I${DEPS_DIR}/include'"
export CGO_LDFLAGS="'-L${DEPS_DIR}/lib'"
if [ "$RUN_TESTS" -eq 1 ]; then
  log "运行 Go 单元测试 (GOOS=${GOOS} GOARCH=${GOARCH})..."
  go test -tags netgo,osusergo ./cmd/waa-signer ./internal/aistudio
  exit 0
fi

mkdir -p bin
BIN_NAME="waa-signer"
if [ "$TARGET_GOOS" = "windows" ]; then
  BIN_NAME="waa-signer.exe"
fi

log "开始编译 waa-signer (GOOS=${TARGET_GOOS} GOARCH=${GO_ARCH}, CC=${CC_CMD})..."
go build -buildvcs=false -trimpath -tags netgo,osusergo \
  -ldflags="-s -w -linkmode external -extldflags '${EXTLDFLAGS}'" \
  -o "bin/${BIN_NAME}.tmp" ./cmd/waa-signer
chmod +x "bin/${BIN_NAME}.tmp"
mv -f "bin/${BIN_NAME}.tmp" "bin/${BIN_NAME}"

if [ "$TARGET_GOOS" = "$HOST_OS" ] || { is_termux && [ "$TARGET_GOOS" = "linux" ]; }; then
  CHECK_OUT="$("./bin/${BIN_NAME}" -check 2>&1 || true)"
  if [[ "$CHECK_OUT" != *'"mode":"ipc"'* ]]; then
    die "编译产物自检失败: ./bin/${BIN_NAME} -check 输出异常: ${CHECK_OUT}"
  fi
fi

log "编译并自检成功: ${SCRIPT_DIR}/bin/${BIN_NAME}"
ls -lh "bin/${BIN_NAME}"
