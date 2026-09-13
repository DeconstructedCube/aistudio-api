"""
Bootstrap helper: download CloakBrowser (stealth Chromium) into the project
folder (.cloakbrowser/) and populate .cloakbrowser/libs/ with the glibc-based
runtime libraries the bundle needs under Termux (no PRoot, no Docker).

Why this script:
  Termux's native Chromium has no SwiftShader / ANGLE, so creating a WebGL
  context returns null and Google BotGuard blocks every request. CloakBrowser
  ships a SwiftShader-equipped build with anti-fingerprint patches, but on
  Termux (bionic linker) it fails with errors like
      "error while loading shared libraries: libplc4.so: ..."
  because the bionic ELF loader does not honour the glibc RUNPATH/RPATH path
  layout. We solve this in three steps:
      1. Download the official arm64 tarball into the repo: <root>/.cloakbrowser/
      2. Extract a curated set of Debian-Bookworm arm64 .so files (libnss3,
         libnspr4, libgbm1, libatspi2.0-0, libxkbcommon0, libxcb1, libx11-6,
         ...) into <root>/.cloakbrowser/libs/ so the binary starts up directly.
      3. The project ships .cloakbrowser/wrapper/run-chrome.sh which forces
         the project-glibc ld.so to load the binary with --library-path so the
         bionic loader never sees it.
  The Python project finds the binary automatically through find_chromium_executable(),
  which now prefers <root>/.cloakbrowser/ over ~/.cloakbrowser/.

Usage (from the repo root):
    python3 scripts/bootstrap_cloakbrowser_termux.py \\
        --project-root "$PWD" \\
        --mirror https://mirror.sjtu.edu.cn/debian \\
        --deb-release bookworm

Default mirror: SJTU Debian mirror (CN); can be overridden for region-specific
fetch speed or vendor mirrors (deb.debian.org, mirrors.tuna.tsinghua.edu.cn,
cloudflaremirrors.com, etc.). On non-Android hosts, the script still extracts
the binary, but `--deb-release` resolution and `libplc4.so` may need separate
host-side `apt install` or `dnf install` — those are out of scope.
"""

from __future__ import annotations

import argparse
import io
import lzma
import os
import platform
import re
import stat
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

# CloakBrowser release manifest. Pinned: free / stable channel.
# Reference: https://github.com/CloakHQ/CloakBrowser/releases
CLOAKBROWSER_VERSION = "146.0.7680.177.3"
CLOAKBROWSER_ARCHIVE = (
    f"cloakbrowser-linux-arm64.tar.gz"
)
CLOAKBROWSER_DOWNLOAD_BASE = (
    "https://github.com/CloakHQ/cloakbrowser/releases/download/"
    f"chromium-v{CLOAKBROWSER_VERSION}"
)

# Curated mapping: ClibBrowser.so -> Debian Bookworm arm64 package.
# This is the *minimum* set needed for `--version` to print Chromium's banner.
LIB_TO_PACKAGE = {
    # Core NSS / NSPR (every modern Chromium needs these on glibc).
    "libnspr4.so": "libnspr4",
    "libplc4.so": "libnspr4",
    "libplds4.so": "libnspr4",
    "libnss3.so": "libnss3",
    "libnssutil3.so": "libnss3",
    "libsmime3.so": "libnss3",
    "libssl3.so": "libnss3",
    # GTK / accessibility stack.
    "libatk-1.0.so.0": "libatk1.0-0",
    "libatk-bridge-2.0.so.0": "libatk-bridge2.0-0",
    "libatspi.so.0": "libatspi2.0-0",
    # GLib / GIO / GObject (Chromium really does need glib + gobject for IPC).
    "libglib-2.0.so.0": "libglib2.0-0",
    "libgobject-2.0.so.0": "libglib2.0-0",
    "libgio-2.0.so.0": "libglib2.0-0",
    "libgmodule-2.0.so.0": "libglib2.0-0",
    # Cario / Pango / font rendering.
    "libcairo.so.2": "libcairo2",
    "libpixman-1.so.0": "libpixman-1-0",
    "libpango-1.0.so.0": "libpango-1.0-0",
    "libpangocairo-1.0.so.0": "libpango1.0-0",
    "libpangoft2-1.0.so.0": "libpango1.0-0",
    "libfribidi.so.0": "libfribidi0",
    "libharfbuzz.so.0": "libharfbuzz0b",
    "libthai.so.0": "libthai0",
    "libdatrie.so.1": "libdatrie1",
    "libgraphite2.so.3": "libgraphite2-3",
    "libfontconfig.so.1": "libfontconfig1",
    "libfreetype.so.6": "libfreetype6",
    "libpng16.so.16": "libpng16-16",
    "libxcb-render.so.0": "libxcb-render0",
    "libxcb-shm.so.0": "libxcb-shm0",
    # X11 / RandR / Composite / Damage / Xi.
    "libX11.so.6": "libx11-6",
    "libXcomposite.so.1": "libxcomposite1",
    "libXdamage.so.1": "libxdamage1",
    "libXext.so.6": "libxext6",
    "libXfixes.so.3": "libxfixes3",
    "libXi.so.6": "libxi6",
    "libXrandr.so.2": "libxrandr2",
    "libXrender.so.1": "libxrender1",
    "libxcb.so.1": "libxcb1",
    "libXau.so.6": "libxau6",
    "libXdmcp.so.6": "libxdmcp6",
    "libxkbcommon.so.0": "libxkbcommon0",
    # Mesa EGL / GBM (chromium's --use-gl=angle uses these).
    "libgbm.so.1": "libgbm1",
    "libEGL.so.1": "libegl1",
    "libGLESv2.so.2": "libglesv2-2",
    # DBus + Avahi (Bonjour) for printers/zeroconf.
    "libdbus-1.so.3": "libdbus-1-3",
    "libavahi-common.so.3": "libavahi-common3",
    "libavahi-client.so.3": "libavahi-client3",
    # udev, ALSA, CUPS — Chromium probes these on startup.
    "libudev.so.1": "libudev1",
    "libasound.so.2": "libasound2",
    "libcups.so.2": "libcups2",
    # GnuTLS (CUPS uses it, and so does libcurl-resolve).
    "libgnutls.so.30": "libgnutls30",
    "libnettle.so.8": "libnettle8",
    "libhogweed.so.6": "libhogweed6",
    "libgmp.so.10": "libgmp10",
    "libidn2.so.0": "libidn2-0",
    "libunistring.so.2": "libunistring2",
    "libtasn1.so.6": "libtasn1-6",
    "libp11-kit.so.0": "libp11-kit0",
    "libffi.so.8": "libffi8",
    # util-linux helpers (mount / blkid).
    "libmount.so.1": "libmount1",
    "libblkid.so.1": "libblkid1",
    # systemd for udev.
    "libsystemd.so.0": "libsystemd0",
    "libselinux.so.1": "libselinux1",
    # Compression helpers.
    "libzstd.so.1": "libzstd1",
    "liblzma.so.5": "liblzma5",
    "libbrotlidec.so.1": "libbrotli1",
    "libbrotlicommon.so.1": "libbrotli1",
    # libbsd / libmd (some glibc builds prefer these instead of bsd/libmd in libc).
    "libbsd.so.0": "libbsd0",
    "libmd.so.0": "libmd0",
    "libcap.so.2": "libcap2",
    "libgcrypt.so.20": "libgcrypt20",
    "libgpg-error.so.0": "libgpg-error0",
}

DEBIAN_BOOKWORM_MIRRORS = (
    "https://mirror.sjtu.edu.cn/debian",
    "https://mirrors.tuna.tsinghua.edu.cn/debian",
    "https://deb.debian.org/debian",
)


def log(msg: str) -> None:
    print(f"[bootstrap] {msg}", file=sys.stderr, flush=True)


def http_get(url: str, *, headers: dict[str, str] | None = None,
             timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def download_cloakbrowser(target_dir: Path) -> Path:
    """Download the CloakBrowser archive and extract it into target_dir."""
    target_dir.mkdir(parents=True, exist_ok=True)
    archive = target_dir / CLOAKBROWSER_ARCHIVE
    extract_dir = target_dir / f"chromium-{CLOAKBROWSER_VERSION}"

    if (extract_dir / "chrome").exists():
        log(f"CloakBrowser already present: {extract_dir}")
        return extract_dir

    url = f"{CLOAKBROWSER_DOWNLOAD_BASE}/{CLOAKBROWSER_ARCHIVE}"
    log(f"Downloading CloakBrowser from {url} (≈200MB)...")
    data = http_get(url, timeout=600.0)
    archive.write_bytes(data)
    log(f"Saved {archive} ({len(data) // 1024 // 1024} MB)")

    log(f"Extracting to {extract_dir}...")
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(extract_dir)

    # Tarballs may wrap files in a single subdir; flatten.
    entries = list(extract_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        sub = entries[0]
        if not sub.name.endswith(".app"):
            for item in sub.iterdir():
                item.rename(extract_dir / item.name)
            sub.rmdir()

    chrome_bin = extract_dir / "chrome"
    if not chrome_bin.exists():
        raise RuntimeError(
            f"chrome binary not found after extraction: {chrome_bin}"
        )
    chrome_bin.chmod(chrome_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log(f"Binary ready: {chrome_bin}")
    return extract_dir


def _resolve_chrome_dir(cloak_dir: Path) -> Path:
    """Find a chrome binary under .cloakbrowser/chromium-*/. Used when --no-cloak-download was passed."""
    candidates = sorted(cloak_dir.glob("chromium-*/chrome"))
    if not candidates:
        raise FileNotFoundError(f"No chrome binary found in {cloak_dir}")
    return candidates[0].parent


def fetch_packages_index(mirror: str, release: str, arch: str) -> dict[str, str]:
    """Download Debian Packages.xz index and return {package: filename}."""
    url = f"{mirror}/dists/{release}/main/binary-{arch}/Packages.xz"
    log(f"Fetching Debian package index: {url}")
    raw = http_get(url, timeout=120.0)
    text = lzma.decompress(raw).decode("utf-8", errors="ignore")

    mapping: dict[str, str] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("Package: "):
            cur = line.split("Package: ", 1)[1].strip()
        elif line.startswith("Filename: ") and cur:
            mapping[cur] = line.split("Filename: ", 1)[1].strip()
        elif line == "":
            cur = None
    log(f"Indexed {len(mapping)} packages from {release}/{arch}")
    return mapping


def extract_deb_into(deb_bytes: bytes, out_dir: Path) -> None:
    """ar-extract a Debian .deb into out_dir, preserving symlinks for .so files.

    A .deb file is an `ar` archive containing:
        debian-binary (ignored)
        control.tar.* (ignored for runtime, only metadata)
        data.tar.*   (we extract all *.so* from here)
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    # .deb files use a 60-byte fixed-width `ar` header.
    pos = 8  # magic + version
    while pos < len(deb_bytes):
        block = deb_bytes[pos:pos + 60]
        if len(block) < 60:
            break
        name = block[:16].decode("ascii", errors="ignore").strip()
        size = int(block[48:58].decode("ascii", errors="ignore").strip())
        pos += 60
        data = deb_bytes[pos:pos + size]
        pos += size
        if pos % 2 == 1:
            pos += 1
        if not name.startswith("data.tar"):
            continue
        bio = io.BytesIO(data)
        with tarfile.open(fileobj=bio, mode="r:*") as tar:
            for member in tar.getmembers():
                if ".so" not in member.name:
                    continue
                target = out_dir / Path(member.name).name
                if member.issym() or member.islnk():
                    target.unlink(missing_ok=True)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.symlink_to(Path(member.linkname).name)
                elif member.isreg():
                    f = tar.extractfile(member)
                    if f is None:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(f.read())
                    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _resolve_path_for(lib_dir: Path, lib: str) -> Path | None:
    """Find a SONAME on disk. Checks the project cache, Termux glibc, and
    /usr/lib for completeness on glibc desktop distros."""
    for d in (lib_dir,
              Path("/data/data/com.termux/files/usr/glibc/lib"),
              Path("/usr/lib/aarch64-linux-gnu"),
              Path("/lib/aarch64-linux-gnu")):
        try:
            candidate = d / lib
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def collect_needed_libraries(chrome_bin: Path, lib_dir: Path) -> list[str]:
    """Direct NEEDED libraries of ``chrome_bin`` only.

    Walk each ELF file's ``.dynamic`` section to find the direct
    ``NEEDED`` entries, dropping the libc-level ones that the glibc loader
    itself handles.
    """
    out = subprocess.check_output(["readelf", "-d", str(chrome_bin)], text=True)
    needed: list[str] = []
    for line in out.splitlines():
        if "(NEEDED)" in line and "Shared library:" in line:
            lib = line.split("[", 1)[1].split("]", 1)[0]
            if lib not in ("libdl.so.2", "libpthread.so.0", "libc.so.6", "libm.so.6",
                           "libgcc_s.so.1", "ld-linux-aarch64.so.1"):
                needed.append(lib)
    return needed


def collect_needed_libraries_transitive(chrome_bin: Path, lib_dir: Path) -> list[str]:
    """All NEEDED libraries transitively — chrome AND any .so files we have
    already placed under lib_dir/. Captures indirect deps like libmount
    (via libgio-2.0) that chrome's own NEEDED does not list."""
    queue: list[Path] = [chrome_bin]
    seen: set[Path] = set()
    yield_list: list[str] = []
    while queue:
        e = queue.pop()
        if e in seen or not e.exists():
            continue
        seen.add(e)
        try:
            out = subprocess.check_output(["readelf", "-d", str(e)], text=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
        for line in out.splitlines():
            if "(NEEDED)" in line:
                lib = line.split("[", 1)[1].split("]", 1)[0]
                if lib in ("libdl.so.2", "libpthread.so.0", "libc.so.6", "libm.so.6",
                           "libgcc_s.so.1", "ld-linux-aarch64.so.1"):
                    continue
                if _resolve_path_for(lib_dir, lib) is None:
                    if lib not in yield_list:
                        yield_list.append(lib)
                else:
                    # Recurse into already-resolved deps to discover theirs.
                    cached = (lib_dir / lib)
                    if cached.exists():
                        queue.append(cached)
    return yield_list
def fetch_libraries(needed: list[str], lib_dir: Path, mirror: str, release: str,
                    arch: str, package_index: dict[str, str]) -> list[str]:
    """Fetch each .deb that supplies one of `needed`, extract into lib_dir.

    Returns the list of libraries that *still* could not be satisfied.
    """
    lib_dir.mkdir(parents=True, exist_ok=True)
    already_present = {p.name for p in lib_dir.iterdir()}
    fetched_packages: set[str] = set()
    unresolved: list[str] = []

    package_resolver = list(LIB_TO_PACKAGE.items())
    cur_pkgs = set()

    for lib in needed:
        if lib in already_present:
            continue
        # 1) Direct map.
        pkg = LIB_TO_PACKAGE.get(lib)
        # 2) Heuristic: libfoo.so.N -> libfooN
        if not pkg:
            stem = lib.replace(".so", "").split(".")[0]
            candidates = [stem, stem + "0", stem + "1", stem + "2", stem + "3", stem + "6", stem + "-1", stem + "-0"]
            for cand in candidates:
                if cand in package_index:
                    pkg = cand
                    break

        if not pkg or pkg not in package_index:
            unresolved.append(lib)
            continue
        if pkg in fetched_packages:
            continue
        log(f"  fetching {pkg} (provides {lib})...")
        cur_pkgs.add(pkg)

    # Now do batched downloads (avoid repeated connection setup).
    if not cur_pkgs:
        return unresolved

    base = mirror
    for pkg in sorted(cur_pkgs):
        rel = package_index[pkg]
        url = f"{base}/{rel}"
        try:
            data = http_get(url, timeout=120.0)
        except Exception as exc:  # noqa: BLE001
            log(f"  warning: failed to fetch {url}: {exc}")
            continue
        extract_deb_into(data, lib_dir)
        fetched_packages.add(pkg)
        log(f"  extracted {pkg}")

    # After extraction, recompute the unresolved list
    unresolved = [
        lib for lib in needed
        if _resolve_path_for(lib_dir, lib) is None
    ]
    return unresolved

def _is_glibc_provided(lib: str) -> bool:
    """True if the system Termux-glibc layer already ships this lib."""
    return (Path("/data/data/com.termux/files/usr/glibc/lib") / lib).exists()


def ensure_libs_for(chrome_bin: Path, lib_dir: Path, mirror: str,
                    release: str = "bookworm", arch: str = "arm64") -> None:
    """Iteratively resolves transitive libraries until chrome --version works."""
    log("Resolving shared library dependencies (one pass may not be enough)...")
    pkg_index = fetch_packages_index(mirror, release, arch)
    lib_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Static resolution of needed libraries
    for attempt in range(12):
        needed = collect_needed_libraries_transitive(chrome_bin, lib_dir)
        already = {p.name for p in lib_dir.iterdir()}
        needed = [
            lib for lib in needed
            if lib not in already and not _is_glibc_provided(lib)
        ]
        if not needed:
            break
        unresolved = fetch_libraries(needed, lib_dir, mirror, release, arch, pkg_index)
        log(f"  pass {attempt}: fetched dependencies, unresolved={unresolved}")

    # Step 2: Dynamic verification via Glibc dynamic linker
    glibc_dir = Path("/data/data/com.termux/files/usr/glibc/lib")
    ld_so = glibc_dir / "ld-linux-aarch64.so.1"
    if not ld_so.exists():
        ld_so = glibc_dir / "ld-linux-x86_64.so.2"

    if ld_so.exists():
        log("Verifying CloakBrowser binary with Glibc dynamic linker...")
        lib_path = f"{lib_dir.resolve()}:{glibc_dir.resolve()}"
        for dynamic_attempt in range(25):
            res = subprocess.run(
                [str(ld_so), "--library-path", lib_path, str(chrome_bin), "--version"],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0:
                log(f"SUCCESS: CloakBrowser verified runnable: {res.stdout.strip()}")
                return
            err = res.stderr or ""
            if "error while loading shared libraries:" in err:
                missing_lib = err.split("error while loading shared libraries:")[1].split(":")[0].strip()
                log(f"  dynamic check reported missing: {missing_lib}")
                fetch_libraries([missing_lib], lib_dir, mirror, release, arch, pkg_index)
            else:
                log(f"Dynamic check non-library error: {err.strip()[:100]}")
                break

def emit_wrapper(project_root: Path) -> Path:
    """Copy the committed ``scripts/cloakbrowser_termux/run-chrome.sh`` into
    ``.cloakbrowser/wrapper/run-chrome.sh`` so callers using
    ``AISTUDIO_BROWSER_EXECUTABLE=.cloakbrowser/wrapper/run-chrome.sh`` still work.
    """
    src = project_root / "scripts" / "cloakbrowser_termux" / "run-chrome.sh"
    dst = project_root / ".cloakbrowser" / "wrapper" / "run-chrome.sh"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        log(f"NOTE: source wrapper {src} not present; nothing to install.")
        return dst
    if dst.exists() and dst.read_bytes() == src.read_bytes():
        return dst
    dst.write_bytes(src.read_bytes())
    dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log(f"Installed wrapper: {dst}")
    return dst

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        default=str(Path.cwd()),
        help="Where to place the .cloakbrowser/ folder (default: cwd)",
    )
    parser.add_argument(
        "--mirror",
        default=DEBIAN_BOOKWORM_MIRRORS[0],
        help=f"Debian mirror (default: {DEBIAN_BOOKWORM_MIRRORS[0]})",
    )
    parser.add_argument(
        "--deb-release",
        default="bookworm",
        help="Debian release codename (default: bookworm, Debian 12)",
    )
    parser.add_argument(
        "--no-cloak-download",
        action="store_true",
        help="Skip downloading the CloakBrowser archive (only resolve libraries).",
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    cloak_dir = project_root / ".cloakbrowser"
    cloak_dir.mkdir(parents=True, exist_ok=True)

    chrome_dir: Path
    if not args.no_cloak_download:
        chrome_dir = download_cloakbrowser(cloak_dir)
    else:
        # Find existing chrome binary.
        candidates = sorted(cloak_dir.glob("chromium-*/chrome"))
        if not candidates:
            log("ERROR: --no-cloak-download but no chrome binary found in .cloakbrowser/")
            return 1
        chrome_dir = candidates[0].parent
    chrome_bin = chrome_dir / "chrome"
    lib_dir = cloak_dir / "libs"
    if not chrome_bin.exists():
        log(f"ERROR: chrome binary missing after bootstrap: {chrome_bin}")
        return 1
    if platform.system() in ("Linux", "Android"):
        # Termux uname reports Linux; platform.system() on Termux returns "Android".
        try:
            ensure_libs_for(chrome_bin, lib_dir, args.mirror, release=args.deb_release)
        except FileNotFoundError as exc:
            log(f"Skipping library resolution: {exc}")
            log("(Ensure `pkg install glibc glibc-runner patchelf-glibc` is run first.)")
        except subprocess.CalledProcessError as exc:
            log(f"Skipping library resolution: {exc}")
        except Exception as exc:  # noqa: BLE001
            log(f"Library resolution failed: {exc}")
            log("Continuing — the launch wrapper will fall back to system ld.")
    else:
        log(f"Host is {platform.system()}; assuming libraries come from system package manager.")
    wrapper = emit_wrapper(project_root)
    log(f"Bootstrap complete. Use {wrapper} or set AISTUDIO_BROWSER_EXECUTABLE={wrapper}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
