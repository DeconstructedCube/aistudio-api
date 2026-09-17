#!/usr/bin/env bash
# Cross-platform browser setup for aistudio-api.
# Installs or stages Chromium / CloakBrowser according to the current platform.
#
# Platforms supported:
#   - Android (Termux): Sets up proot-distro container (aistudio-api) and stages CloakBrowser ARM64.
#   - Linux (x86_64 / aarch64): Downloads prebuilt stealth CloakBrowser into .cloakbrowser/
#     or verifies system Chromium / Chrome.
#   - macOS (Apple Silicon / Intel) / Windows: Verifies installed system Chrome/Chromium/Edge.
#
# Flags:
#   --project-root PATH      where to place .cloakbrowser (default: cwd)
#   --proot-name NAME        container name for Termux (default: aistudio-api)
#   --proot-distro IMAGE     OCI image for Termux (default: ubuntu:24.04)
#   --chromium-version VER   CloakBrowser version (default: 146.0.7680.177.3)
#   --skip-proot-install     don't create/update proot container
#   --skip-browser-download  don't re-fetch CloakBrowser tarball

set -euo pipefail

PROJECT_ROOT="$(pwd)"
PROOT_NAME="aistudio-api"
PROOT_IMAGE="ubuntu:24.04"
CHROMIUM_VERSION="146.0.7680.177.3"
SKIP_PROOT=0
SKIP_BROWSER=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project-root) PROJECT_ROOT="$2"; shift 2 ;;
        --proot-name) PROOT_NAME="$2"; shift 2 ;;
        --proot-distro) PROOT_IMAGE="$2"; shift 2 ;;
        --chromium-version) CHROMIUM_VERSION="$2"; shift 2 ;;
        --skip-proot-install) SKIP_PROOT=1; shift ;;
        --skip-browser-download) SKIP_BROWSER=1; shift ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

PROJECT_ROOT="$(cd "${PROJECT_ROOT}" && pwd)"
CLOAK_ROOT="${PROJECT_ROOT}/.cloakbrowser"
CHROME_DIR_HOST="${CLOAK_ROOT}/chromium-${CHROMIUM_VERSION}"

log() { printf '[setup-browser] %s\n' "$*" >&2; }

is_termux() {
    [[ -n "${PREFIX:-}" && "${PREFIX}" == *"/com.termux"* ]] || command -v pkg >/dev/null 2>&1
}

# ==============================================================================
# Platform Branch 1: Android (Termux with proot-distro container)
# ==============================================================================
setup_termux() {
    log "Configuring Termux CloakBrowser environment (proot-distro)..."

    if ! command -v proot-distro >/dev/null 2>&1; then
        if command -v pkg >/dev/null 2>&1; then
            log "Installing proot-distro on Termux host..."
            pkg install -y proot-distro
        else
            echo "proot-distro is required. Install it via 'pkg install proot-distro'." >&2
            exit 1
        fi
    fi

    # 1. proot container
    if { proot-distro list 2>&1 1>/dev/null || true; } | grep -qE "[[:space:]]\\*?[[:space:]]*${PROOT_NAME}([[:space:]]|$)"; then
        log "Container '${PROOT_NAME}' already exists; skipping container creation."
    elif [[ ${SKIP_PROOT} -eq 1 ]]; then
        log "--skip-proot-install set but container missing; aborting."
        exit 1
    else
        log "Installing proot container '${PROOT_NAME}' from ${PROOT_IMAGE}..."
        proot-distro install "${PROOT_IMAGE}" --name "${PROOT_NAME}"
    fi

    # 2. runtime deps inside container
    log "Ensuring runtime libs inside container '${PROOT_NAME}'..."
    proot-distro login "${PROOT_NAME}" -- bash -c '
        set -euo pipefail
        export DEBIAN_FRONTEND=noninteractive
        export TZ=UTC
        dpkg --configure -a >/dev/null 2>&1 || true
        apt-get install -y --no-install-recommends \
            ca-certificates curl \
            libnss3 libnspr4 libgbm1 libasound2t64 \
            libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
            libxfixes3 libxext6 libxrender1 libx11-6 libxcb1 \
            libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 \
            libgtk-3-0 libnotify4 fonts-liberation libpangocairo-1.0-0
        apt-get clean
        rm -rf /var/lib/apt/lists/*
    '

    # 3. CloakBrowser download
    mkdir -p "${CLOAK_ROOT}"
    ARCHIVE_NAME="cloakbrowser-linux-arm64.tar.gz"
    DOWNLOAD_URL="https://github.com/CloakHQ/cloakbrowser/releases/download/chromium-v${CHROMIUM_VERSION}/${ARCHIVE_NAME}"

    if [[ -x "${CHROME_DIR_HOST}/chrome" ]]; then
        log "CloakBrowser ${CHROMIUM_VERSION} already cached at ${CHROME_DIR_HOST}"
    elif [[ ${SKIP_BROWSER} -eq 1 ]]; then
        log "--skip-browser-download set but no chrome binary; aborting."
        exit 1
    else
        log "Downloading CloakBrowser ARM64 (~200MB)..."
        TMP="$(mktemp -d)"
        trap 'rm -rf "${TMP}"' EXIT
        if command -v curl >/dev/null 2>&1; then
            curl -fSL --retry 3 -o "${TMP}/${ARCHIVE_NAME}" "${DOWNLOAD_URL}"
        else
            proot-distro login "${PROOT_NAME}" -- curl -fSL --retry 3 -o "/tmp/${ARCHIVE_NAME}" "${DOWNLOAD_URL}"
            proot-distro copy "${PROOT_NAME}:/tmp/${ARCHIVE_NAME}" "${TMP}/${ARCHIVE_NAME}"
        fi
        tar -xzf "${TMP}/${ARCHIVE_NAME}" -C "${CLOAK_ROOT}"
        INNER="$(find "${CLOAK_ROOT}" -mindepth 2 -maxdepth 2 -type d -name 'chromium-*' | head -1 || true)"
        if [[ -n "${INNER}" && "${INNER}" != "${CHROME_DIR_HOST}" ]]; then
            mv "${INNER}" "${CHROME_DIR_HOST}"
        fi
        log "CloakBrowser extracted to ${CHROME_DIR_HOST}"
    fi

    # 4. Stage chrome tree into the container
    PROOT_CHROME_DIR="/opt/cloakbrowser"
    if proot-distro login "${PROOT_NAME}" -- test -x "${PROOT_CHROME_DIR}/chrome"; then
        log "CloakBrowser already staged inside container at ${PROOT_CHROME_DIR}."
    else
        log "Staging chrome tree into container at ${PROOT_CHROME_DIR}..."
        proot-distro login "${PROOT_NAME}" -- bash -c "rm -rf '/opt/$(basename "${CHROME_DIR_HOST}")'"
        proot-distro copy -r "${CHROME_DIR_HOST}" "${PROOT_NAME}:/opt/$(basename "${CHROME_DIR_HOST}")"
        proot-distro login "${PROOT_NAME}" -- bash -c "
            rm -rf '${PROOT_CHROME_DIR}'
            mv '/opt/$(basename "${CHROME_DIR_HOST}")' '${PROOT_CHROME_DIR}'
        "
        log "CloakBrowser staging complete."
    fi

    log "Smoke test: bash scripts/cloakbrowser_termux/run-chrome.sh --version"
}

# ==============================================================================
# Platform Branch 2: Standard Linux (x86_64 / aarch64 native)
# ==============================================================================
setup_linux() {
    local arch="$(uname -m)"
    log "Configuring Linux native browser (${arch})..."

    # Check if a system Chromium or Chrome is already installed and runnable
    for bin in google-chrome-stable google-chrome chromium-browser chromium brave-browser; do
        if command -v "${bin}" >/dev/null 2>&1; then
            log "Found existing system browser: $(command -v "${bin}")"
            log "System browser is ready for use."
            return 0
        fi
    done

    # If no system browser, provide standalone CloakBrowser
    mkdir -p "${CLOAK_ROOT}"
    local archive=""
    case "${arch}" in
        x86_64|amd64) archive="cloakbrowser-linux-x64.tar.gz" ;;
        aarch64|arm64) archive="cloakbrowser-linux-arm64.tar.gz" ;;
        *)
            log "Architecture ${arch} does not have a precompiled CloakBrowser bundle."
            log "Please install chromium via your system package manager (e.g. apt-get install chromium)."
            return 0
            ;;
    esac

    local download_url="https://github.com/CloakHQ/cloakbrowser/releases/download/chromium-v${CHROMIUM_VERSION}/${archive}"
    if [[ -x "${CHROME_DIR_HOST}/chrome" ]]; then
        log "CloakBrowser ${CHROMIUM_VERSION} already installed at ${CHROME_DIR_HOST}"
    else
        log "Downloading CloakBrowser (${arch}) from ${download_url}..."
        TMP="$(mktemp -d)"
        trap 'rm -rf "${TMP}"' EXIT
        curl -fSL --retry 3 -o "${TMP}/${archive}" "${download_url}"
        tar -xzf "${TMP}/${archive}" -C "${CLOAK_ROOT}"
        INNER="$(find "${CLOAK_ROOT}" -mindepth 2 -maxdepth 2 -type d -name 'chromium-*' | head -1 || true)"
        if [[ -n "${INNER}" && "${INNER}" != "${CHROME_DIR_HOST}" ]]; then
            mv "${INNER}" "${CHROME_DIR_HOST}"
        fi
        log "CloakBrowser ready at ${CHROME_DIR_HOST}/chrome"
    fi
}

# ==============================================================================
# Platform Branch 3: macOS / Windows
# ==============================================================================
setup_desktop() {
    local os_type="$(uname -s)"
    log "Configuring browser for ${os_type}..."

    # Check common system browsers
    for bin in "Google Chrome" "Chromium" "Microsoft Edge" "Brave Browser"; do
        if [[ "${os_type}" == "Darwin" ]]; then
            if [[ -d "/Applications/${bin}.app" || -d "${HOME}/Applications/${bin}.app" ]]; then
                log "Found installed browser: ${bin}.app"
                return 0
            fi
        fi
    done

    log "Notice: Ensure Google Chrome, Chromium, or Microsoft Edge is installed on your host system."
}

# ---------- Main Dispatch ----------
if is_termux; then
    setup_termux
elif [[ "$(uname -s)" == "Linux" ]]; then
    setup_linux
else
    setup_desktop
fi

log "Browser setup check complete."
