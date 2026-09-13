#!/data/data/com.termux/files/usr/bin/bash
# Idempotent Termux prereq installer for aistudio-api.
#
# What it does:
#   1. Ensures pkg: proot-distro, ca-certificates, curl.
#   2. Installs proot-distro container ${AISTUDIO_PROOT_NAME} (default
#      aistudio-api) — Ubuntu 24.04 from termux/proot-distro. Skips if
#      already present so it never collides with the user's own
#      `ubuntu` (or any other) container.
#   3. Inside the container, apt-installs the glibc runtime deps
#      CloakBrowser actually needs (libnss3, libnspr4, libgbm1,
#      libasound2, libxkbcommon0, libxcomposite1, libxdamage1,
#      libxrandr2, libxkbcommon0, libatk-bridge2.0-0, libatk1.0-0,
#      libatspi2.0-0, libgtk-3-0, libnotify4, fonts-liberation).
#   4. Downloads CloakBrowser into
#      <project_root>/.cloakbrowser/chromium-<ver>/. Skips if already
#      cached. Extracts in place; copies the chrome tree into the
#      container at /opt/cloakbrowser/ so the wrapper can --bind it.
#
# Flags:
#   --project-root PATH      where to place .cloakbrowser (default: cwd)
#   --proot-name NAME        container name (default: aistudio-api)
#   --proot-distro IMAGE     OCI image to install (default: ubuntu:24.04)
#   --chromium-version VER   CloakBrowser chromium version
#                            (default: 146.0.7680.177.3)
#   --skip-proot-install     don't create/update proot container
#   --skip-browser-download  don't re-fetch CloakBrowser tarball
#
# Re-running is safe and fast: each step short-circuits when its
# precondition is already satisfied.

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
        -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

PROJECT_ROOT="$(cd "${PROJECT_ROOT}" && pwd)"
CLOAK_ROOT="${PROJECT_ROOT}/.cloakbrowser"
CHROME_DIR_HOST="${CLOAK_ROOT}/chromium-${CHROMIUM_VERSION}"

log() { printf '[install] %s\n' "$*" >&2; }

# ---------- 1. Termux host packages ----------
if command -v pkg >/dev/null 2>&1; then
    NEEDED_PKGS=()
    for p in proot-distro ca-certificates curl; do
        if ! command -v "${p%proot-distro*}" >/dev/null 2>&1 \
           && ! dpkg -s "${p}" >/dev/null 2>&1; then
            NEEDED_PKGS+=("${p}")
        fi
    done
    command -v proot-distro >/dev/null 2>&1 || NEEDED_PKGS+=("proot-distro")
    command -v curl >/dev/null 2>&1 || NEEDED_PKGS+=("curl")
    if [[ ${#NEEDED_PKGS[@]} -gt 0 ]]; then
        log "Installing Termux packages: ${NEEDED_PKGS[*]}"
        pkg update -y
        pkg install -y "${NEEDED_PKGS[@]}"
    else
        log "Termux packages already present"
    fi
else
    log "pkg not found; assuming non-Termux host (skip host deps)"
fi

command -v proot-distro >/dev/null 2>&1 || {
    echo "proot-distro is required. Install it via 'pkg install proot-distro'." >&2
    exit 1
}

# ---------- 2. proot container ----------
if { proot-distro list 2>&1 1>/dev/null || true; } | grep -qE "[[:space:]]\\*?[[:space:]]*${PROOT_NAME}([[:space:]]|$)"; then
    log "proot container '${PROOT_NAME}' already exists; skipping install"
elif [[ ${SKIP_PROOT} -eq 1 ]]; then
    log "--skip-proot-install set but container missing; aborting"
    exit 1
else
    log "Installing proot container '${PROOT_NAME}' from ${PROOT_IMAGE}"
    proot-distro install "${PROOT_IMAGE}" --name "${PROOT_NAME}"
fi

# ---------- 3. apt deps inside container ----------
install_apt_deps() {
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
}
log "Ensuring runtime libs inside container '${PROOT_NAME}'"
install_apt_deps

# ---------- 4. CloakBrowser binary ----------
ARCHIVE_NAME="cloakbrowser-linux-arm64.tar.gz"
DOWNLOAD_URL="https://github.com/CloakHQ/cloakbrowser/releases/download/chromium-v${CHROMIUM_VERSION}/${ARCHIVE_NAME}"

mkdir -p "${CLOAK_ROOT}"

if [[ -x "${CHROME_DIR_HOST}/chrome" ]]; then
    log "CloakBrowser ${CHROMIUM_VERSION} already cached at ${CHROME_DIR_HOST}"
elif [[ ${SKIP_BROWSER} -eq 1 ]]; then
    log "--skip-browser-download set but no chrome binary; aborting"
    exit 1
else
    log "Downloading CloakBrowser ${CHROMIUM_VERSION} (~200MB)..."
    TMP="$(mktemp -d)"
    trap 'rm -rf "${TMP}"' EXIT
    if command -v curl >/dev/null 2>&1; then
        curl -fSL --retry 3 -o "${TMP}/${ARCHIVE_NAME}" "${DOWNLOAD_URL}"
    else
        proot-distro login "${PROOT_NAME}" -- curl -fSL --retry 3 \
        proot-distro copy "${PROOT_NAME}:/tmp/${ARCHIVE_NAME}" \
            "${TMP}/${ARCHIVE_NAME}"
    fi
    tar -xzf "${TMP}/${ARCHIVE_NAME}" -C "${CLOAK_ROOT}"
    # Tarball may nest: chromium-<ver>/... — flatten one level if so.
    INNER="$(find "${CLOAK_ROOT}" -mindepth 2 -maxdepth 2 -type d -name 'chromium-*' | head -1 || true)"
    if [[ -n "${INNER}" && "${INNER}" != "${CHROME_DIR_HOST}" ]]; then
        mv "${INNER}" "${CHROME_DIR_HOST}"
    fi
    if [[ ! -x "${CHROME_DIR_HOST}/chrome" ]]; then
        echo "extracted chrome binary not found at ${CHROME_DIR_HOST}/chrome" >&2
        exit 1
    fi
    log "CloakBrowser extracted to ${CHROME_DIR_HOST}"
fi

# ---------- 5. Stage chrome tree into the container ----------
PROOT_CHROME_DIR="/opt/cloakbrowser"
log "Staging chrome tree into container at ${PROOT_CHROME_DIR}"
# proot-distro copy -r copies the dir itself. We'll rename it inside.
proot-distro login "${PROOT_NAME}" -- bash -c "rm -rf '/opt/$(basename "${CHROME_DIR_HOST}")'"
proot-distro copy -r "${CHROME_DIR_HOST}" "${PROOT_NAME}:/opt/$(basename "${CHROME_DIR_HOST}")"
proot-distro login "${PROOT_NAME}" -- bash -c "
    rm -rf '${PROOT_CHROME_DIR}'
    mv '/opt/$(basename "${CHROME_DIR_HOST}")' '${PROOT_CHROME_DIR}'
"

cat <<EOF

[install] Done.

Quick smoke test:
    bash scripts/cloakbrowser_termux/run-chrome.sh --version

Then start the API:
    uv run python3 main.py server --port 8080
EOF