#!/data/data/com.termux/files/usr/bin/bash
# Termux Glibc launcher for CloakBrowser (anti-bot Chromium runtime).
#
# Why this script exists:
#   CloakBrowser is a glibc-linked ELF binary. Termux ships a bionic libc,
#   so when the kernel loads chrome it uses the bionic linker, which does
#   not honor glibc's RUNPATH/RPATH layout and aborts with errors like:
#       error while loading shared libraries: libplc4.so: ...
#   This wrapper bypasses bionic by invoking the project-glibc dynamic
#   linker directly (glibc/lib/ld-linux-*.so.1), which then resolves the
#   project's .cloakbrowser/libs/ first, then the system
#   /data/data/com.termux/files/usr/glibc/lib/ — exactly matching what
#   the binary was compiled to expect.
#
# CRITICAL: This script does NOT export LD_LIBRARY_PATH (set at any point
# inside bash itself). Doing so would corrupt subsequent bionic subprocesses
# (uname, sort, ...). Only chrome's loader sees glibc, via --library-path.
#
# Filesystem layout (auto-created by scripts/bootstrap_cloakbrowser_termux.py):
#   <project_root>/
#     .cloakbrowser/
#       chromium-<version>/chrome, icudtl.dat, *.pak, ...
#       libs/libnss3.so, libnspr4.so, libplc4.so, libgbm1.so, ...
#     scripts/cloakbrowser_termux/run-chrome.sh  (this file)
#
# Usage (from the repo root):
#   ./scripts/cloakbrowser_termux/run-chrome.sh --version
#   ./scripts/cloakbrowser_termux/run-chrome.sh --headless --no-sandbox \
#       --remote-debugging-port=9555 about:blank
#
# Or via the API server:
#   AISTUDIO_BROWSER_EXECUTABLE=$PWD/scripts/cloakbrowser_termux/run-chrome.sh \
#       python3 main.py server

set -euo pipefail

# Allow caller override (handy when running from a separate worktree).
: "${CLOAKBROWSER_PROJECT_DIR:=}"
if [[ -z "${CLOAKBROWSER_PROJECT_DIR}" ]]; then
  PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
else
  PROJECT_ROOT="${CLOAKBROWSER_PROJECT_DIR}"
fi

CLOAK_ROOT="${PROJECT_ROOT}/.cloakbrowser"
LIBDIR="${CLOAK_ROOT}/libs"
GLIBC_LIB="/data/data/com.termux/files/usr/glibc/lib"

# 1) Locate the cached Chromium binary (handles arbitrary future versions).
CHROME_BIN=""
case "$(uname -s)" in
  Linux|Android)
    if [[ -d "${CLOAK_ROOT}" ]]; then
      for d in $(ls -1d "${CLOAK_ROOT}"/chromium-* 2>/dev/null | sort -V); do
        if [[ -x "${d}/chrome" ]]; then
          CHROME_BIN="${d}/chrome"
          break
        fi
      done
    fi
    ;;
  Darwin)
    for d in $(ls -1d "${CLOAK_ROOT}"/chromium-* 2>/dev/null | sort -V); do
      if [[ -x "${d}/Chromium.app/Contents/MacOS/Chromium" ]]; then
        CHROME_BIN="${d}/Chromium.app/Contents/MacOS/Chromium"
        break
      fi
    done
    ;;
  *)
    for d in $(ls -1d "${CLOAK_ROOT}"/chromium-* 2>/dev/null | sort -V); do
      if [[ -f "${d}/chrome.exe" ]]; then
        CHROME_BIN="${d}/chrome.exe"
        break
      fi
    done
    ;;
esac

if [[ -z "${CHROME_BIN}" || ! -f "${CHROME_BIN}" ]]; then
  cat >&2 <<EOF
[run-chrome] No CloakBrowser binary found in ${CLOAK_ROOT}.

Re-run the bootstrap to (re)fetch it:
    python3 scripts/bootstrap_cloakbrowser_termux.py \\
        --project-root "${PROJECT_ROOT}"

Or set CLOAKBROWSER_CACHE_DIR=/path/to/cache before launching the API.
EOF
  exit 127
fi

# 2) Compose glibc --library-path. Project libs first, then system glibc.
LIBP=""
[[ -d "${LIBDIR}" ]] && LIBP="${LIBDIR}"
[[ -d "${GLIBC_LIB}" ]] && LIBP="${LIBP:+${LIBP}:}${GLIBC_LIB}"

# 3) Pick the dynamic linker. Used only for this exec; we never pollute
#    the wrapping shell's environment with LD_LIBRARY_PATH (that would
#    break bionic `uname` / `sort` subprocesses used above).
case "$(uname -s)" in
  Linux|Android)
    LD_BIN=""
    if [[ -x "${GLIBC_LIB}/ld-linux-aarch64.so.1" ]]; then
      LD_BIN="${GLIBC_LIB}/ld-linux-aarch64.so.1"
    elif [[ -x "${GLIBC_LIB}/ld-linux-x86_64.so.2" ]]; then
      LD_BIN="${GLIBC_LIB}/ld-linux-x86_64.so.2"
    elif [[ -x "/lib64/ld-linux-x86-64.so.2" ]]; then
      LD_BIN="/lib64/ld-linux-x86-64.so.2"
    elif [[ -x "/lib/ld-linux.so.2" ]]; then
      LD_BIN="/lib/ld-linux.so.2"
    fi
    if [[ -n "${LD_BIN}" && -n "${LIBP}" ]]; then
      exec "${LD_BIN}" --library-path "${LIBP}" "${CHROME_BIN}" "$@"
    fi
    # Fallback: trust the chrome binary's own RUNPATH (already glibc ld).
    exec "${CHROME_BIN}" "$@"
    ;;
  Darwin)
    exec "${CHROME_BIN}" "$@"
    ;;
  *)
    # Cygwin / MSYS / WSL: trust the binary.
    exec "${CHROME_BIN}" "$@"
    ;;
esac
