#!/usr/bin/env bash
# Ultra-defensive Python + uv environment setup for aistudio-api.
# Covers Android (Termux), Linux, macOS, and Git Bash/WSL.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

EXPLICIT_PROXY=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --proxy)
            if [ "$#" -lt 2 ]; then
                echo "错误: --proxy 需要参数" >&2
                exit 1
            fi
            EXPLICIT_PROXY="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

log()  { printf '[setup-env] %s\n' "$*" >&2; }
warn() { printf '[setup-env][警告] %s\n' "$*" >&2; }
die()  { printf '[setup-env][错误] %s\n' "$*" >&2; exit 1; }

is_termux() {
    [[ -n "${PREFIX:-}" && "${PREFIX}" == *"/com.termux"* ]] && return 0
    [[ -d "/data/data/com.termux/files/usr" ]] && command -v pkg >/dev/null 2>&1 && return 0
    return 1
}

prepend_path_if_dir() {
    local d
    for d in "$@"; do
        if [[ -d "$d" ]]; then
            case ":$PATH:" in
                *":$d:"*) ;;
                *) export PATH="$d:$PATH" ;;
            esac
        fi
    done
}

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
    if [[ -n "$EXPLICIT_PROXY" ]]; then
        printf '%s' "$EXPLICIT_PROXY"
        return
    fi
    local v
    for v in "${AISTUDIO_PROXY:-}" "${HTTPS_PROXY:-}" "${https_proxy:-}" "${HTTP_PROXY:-}" "${http_proxy:-}"; do
        if [[ -n "$v" ]]; then
            printf '%s' "$v"
            return
        fi
    done
    local p
    for p in 7890 7897 10808 10809 2080; do
        if tcp_port_open "127.0.0.1" "$p"; then
            printf 'http://127.0.0.1:%s' "$p"
            return
        fi
    done
}

PROXY="$(detect_proxy)"
if [[ -n "$PROXY" ]]; then
    log "使用网络代理: $PROXY"
    export HTTPS_PROXY="${HTTPS_PROXY:-$PROXY}"
    export HTTP_PROXY="${HTTP_PROXY:-$PROXY}"
fi

check_python_ge_311() {
    local py_bin="$1"
    "$py_bin" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >/dev/null 2>&1
}

check_venv_healthy() {
    local venv_py="${PROJECT_ROOT}/.venv/bin/python"
    [[ -x "$venv_py" ]] || return 1
    check_python_ge_311 "$venv_py" || return 1
    # Ensure core binary wheel deps can actually load (catches Termux ABI drift after pkg upgrade)
    if "$venv_py" -c "import pydantic_core" >/dev/null 2>&1; then
        return 0
    fi
    # If .venv exists without pydantic_core yet, still allow uv sync unless python itself is broken
    "$venv_py" -c "import ssl, sqlite3, json" >/dev/null 2>&1
}

if is_termux; then
    log "检测到 Android Termux 环境。"

    # 1. Prevent Play Store obsolete Termux traps & broken $TMPDIR
    if [[ -f "${PREFIX}/etc/apt/sources.list" ]] && grep -q "dl.bintray.com" "${PREFIX}/etc/apt/sources.list" 2>/dev/null; then
        die "检测到已停止维护的 Google Play 旧版 Termux（Bintray 源已失效）。请卸载后从 F-Droid 或 GitHub Releases 重新安装 Termux。"
    fi
    export TMPDIR="${TMPDIR:-${PREFIX}/tmp}"
    mkdir -p "${TMPDIR}"

    # 2. Recover any interrupted dpkg state before installing packages
    if command -v dpkg >/dev/null 2>&1 && [ -n "$(dpkg --audit 2>/dev/null || true)" ]; then
        warn "检测到上次未完成的包安装，正在自动修复 dpkg 状态..."
        dpkg --configure -a || true
    fi

    # 3. Put Termux $PREFIX/bin first so broken ~/.local/bin/uv (glibc) never shadows native Termux uv
    prepend_path_if_dir "${PREFIX}/bin"

    NEEDED_PKGS=()
    for p in ca-certificates curl tar gzip xz-utils python uv golang zig; do
        if ! dpkg -s "${p}" >/dev/null 2>&1; then
            NEEDED_PKGS+=("${p}")
        fi
    done

    if [[ ${#NEEDED_PKGS[@]} -gt 0 ]]; then
        log "正在安装/补齐 Termux 基础与编译依赖: ${NEEDED_PKGS[*]}"
        pkg update -y
        pkg install -y "${NEEDED_PKGS[@]}"
    fi

    # 4. Verify native Python >= 3.11 and native Termux uv
    SYS_PY="$(command -v python3 || true)"
    if [[ -z "$SYS_PY" ]] || ! check_python_ge_311 "$SYS_PY"; then
        log "升级 Termux Python 至 3.11+..."
        pkg update -y && pkg upgrade -y python
        SYS_PY="$(command -v python3 || true)"
    fi
    [[ -n "$SYS_PY" ]] && check_python_ge_311 "$SYS_PY" || die "Termux Python 版本低于 3.11，请运行 pkg upgrade python 后重试。"

    UV_BIN="${PREFIX}/bin/uv"
    if [[ ! -x "$UV_BIN" ]] || ! "$UV_BIN" --version >/dev/null 2>&1; then
        pkg install -y uv || pkg upgrade -y uv
    fi
    [[ -x "$UV_BIN" ]] && "$UV_BIN" --version >/dev/null 2>&1 || die "Termux uv 不可用，请执行 pkg reinstall uv 后重试。"

    # 5. Write project-local uv.toml for TUR prebuilt Android wheels
    UV_CONFIG="${PROJECT_ROOT}/uv.toml"
    cat > "${UV_CONFIG}" <<'EOF'
# Auto-generated by scripts/setup-env.sh for Termux
# Directs uv to resolve prebuilt Android wheels from TUR without compilation.
[[index]]
name = "tur"
url = "https://termux-user-repository.github.io/pypi/"
extra = true
EOF
    log "已写入 Termux TUR 预编译源配置: ${UV_CONFIG}"

    # 6. Recreate .venv cleanly if missing or ABI-corrupted
    if [[ -d "${PROJECT_ROOT}/.venv" ]] && ! check_venv_healthy; then
        warn "检测到现有 .venv 已损坏或与当前 Termux Python ABI 不兼容，正在重建..."
        rm -rf "${PROJECT_ROOT}/.venv"
    fi
    if [[ ! -d "${PROJECT_ROOT}/.venv" ]]; then
        "$UV_BIN" venv --python "$SYS_PY" "${PROJECT_ROOT}/.venv"
    fi

    # 7. Sync strictly with Termux native Python (never download glibc Python)
    log "正在同步 Python 依赖 (Termux 原生环境 + TUR 预编译源)..."
    if ! "$UV_BIN" sync --no-managed-python; then
        warn "默认同步失败，尝试使用清华 PyPI 镜像重试..."
        UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple" "$UV_BIN" sync --no-managed-python
    fi

    # 8. Optional: link precompiled ruff into .venv if available
    if command -v ruff >/dev/null 2>&1 && [[ -d "${PROJECT_ROOT}/.venv/bin" && ! -e "${PROJECT_ROOT}/.venv/bin/ruff" ]]; then
        ln -sf "$(command -v ruff)" "${PROJECT_ROOT}/.venv/bin/ruff"
    fi
else
    log "检测到标准桌面/服务器环境 ($(uname -s) $(uname -m))。"
    prepend_path_if_dir "${HOME}/.local/bin" "${HOME}/.cargo/bin" "/opt/homebrew/bin" "/usr/local/bin"

    # Remove stale Termux uv.toml if copied from an Android device
    if [[ -f "${PROJECT_ROOT}/uv.toml" ]] && grep -q "termux-user-repository" "${PROJECT_ROOT}/uv.toml" 2>/dev/null; then
        warn "检测到桌面环境残留了 Termux 专用 uv.toml，已自动移除以恢复官方 PyPI 解析。"
        rm -f "${PROJECT_ROOT}/uv.toml"
    fi

    if ! command -v curl >/dev/null 2>&1; then
        die "缺少 curl 命令，请先安装 curl 后重试。"
    fi

    if ! command -v uv >/dev/null 2>&1 || ! uv --version >/dev/null 2>&1; then
        log "未检测到 uv，正在安装 uv..."
        if ! curl -LsSf --connect-timeout 15 --max-time 120 https://astral.sh/uv/install.sh | sh; then
            warn "官方源安装 uv 失败，尝试通过 GitHub 镜像安装..."
            curl -LsSf --connect-timeout 15 --max-time 120 https://ghproxy.net/https://github.com/astral-sh/uv/releases/latest/download/uv-installer.sh | sh
        fi
        prepend_path_if_dir "${HOME}/.local/bin" "${HOME}/.cargo/bin"
    fi
    command -v uv >/dev/null 2>&1 || die "uv 安装失败，请手动安装: https://docs.astral.sh/uv/"
    log "uv 就绪: $(uv --version)"

    SYS_PY="$(command -v python3 || command -v python || true)"
    if [[ -z "$SYS_PY" ]] || ! check_python_ge_311 "$SYS_PY"; then
        log "系统 Python 缺失或版本低于 3.11，正在通过 uv 自动安装 Python 3.11..."
        uv python install 3.11
    fi

    if [[ -d "${PROJECT_ROOT}/.venv" ]] && ! check_venv_healthy; then
        warn "检测到现有 .venv 损坏或 Python 版本过低，正在重建..."
        rm -rf "${PROJECT_ROOT}/.venv"
    fi

    log "正在同步 Python 依赖..."
    if ! uv sync; then
        warn "官方 PyPI 同步失败，尝试使用清华 PyPI 镜像重试..."
        UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple" uv sync
    fi
fi

# 9. Verify critical runtime packages import cleanly
log "正在校验 Python 核心运行时依赖..."
uv run --no-sync python -c "import fastapi, httpx, pydantic, yaml, uvicorn" \
    || die "Python 运行时依赖导入自检失败，请删除 .venv 后重试。"

log "Python 环境就绪!"
