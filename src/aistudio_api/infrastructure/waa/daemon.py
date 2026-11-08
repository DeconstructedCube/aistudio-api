"""Automatic local waa-signer daemon manager with strict lifecycle binding.

Prevents orphan processes by:
1. Kernel-level PR_SET_PDEATHSIG (child receives SIGTERM immediately if parent dies).
2. atexit handler ensuring cleanup on any normal or abnormal Python exit.
3. Clean process group termination without zombie residues.
"""

from __future__ import annotations

import atexit
import contextlib
import ctypes
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from aistudio_api.infrastructure.gateway.model_defaults import (
    get_active_waa_server,
    switch_active_waa_server,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("waa.daemon")

_daemon_proc: subprocess.Popen[bytes] | None = None
_PR_SET_PDEATHSIG = 1


def _set_parent_death_signal() -> None:
    """Linux kernel-level hook: child process is terminated if parent dies."""
    if sys.platform != "linux":
        return
    try:
        libc = ctypes.CDLL(None)
        # prctl(PR_SET_PDEATHSIG, SIGTERM)
        libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)
    except Exception:
        pass

def is_port_open(host: str, port: int) -> bool:
    """Check whether a TCP port is open and accepting connections."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.1)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def is_service_healthy(url: str) -> bool:
    import urllib.request

    try:
        req = urllib.request.Request(f"{url.rstrip('/')}/health")
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_signer_ready() -> None:
    """检查本地 WAA 签名服务状态，未运行时自动拉起或切换至远程节点。"""
    global _daemon_proc
    active_waa = get_active_waa_server()
    url = str(active_waa.get("url") or "")

    root_dir = Path(__file__).resolve().parents[4]
    bin_name = "waa-signer.exe" if sys.platform == "win32" else "waa-signer"
    bin_path = root_dir / "waa-signer" / "bin" / bin_name

    is_local_8081 = "127.0.0.1:8081" in url or "localhost:8081" in url
    if not is_local_8081:
        if bin_path.is_file():
            logger.info(
                "检测到本地 WAA 签名服务已就绪 (%s)。当前配置为远程节点 [%s]，如需启用本地原生引擎可在 Web 控制台或 config.yaml 中切换为 local_spidermonkey",
                bin_path.name,
                active_waa.get("id", "remote"),
            )
        return

    # 1. 检查本地 8081 是否已有健康实例在运行
    if is_service_healthy("http://127.0.0.1:8081"):
        logger.debug("本地 WAA 签名服务运行正常 (127.0.0.1:8081)")
        return

    # 2. 检查本地编译产物是否存在
    is_executable = bin_path.is_file() and (sys.platform == "win32" or os.access(bin_path, os.X_OK))
    if is_executable:
        proxy = os.getenv("AISTUDIO_PROXY") or os.getenv("HTTPS_PROXY") or ""
        cmd = [str(bin_path), "-listen", "127.0.0.1:8081"]
        if proxy:
            cmd.extend(["-proxy", proxy])

        logger.info("启动本地 WAA 签名服务: %s", bin_path)
        try:
            popen_kwargs: dict[str, object] = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "cwd": str(bin_path.parent.parent),
            }
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_kwargs["preexec_fn"] = _set_parent_death_signal

            _daemon_proc = subprocess.Popen(cmd, **popen_kwargs)  # type: ignore[call-overload]
            # 等待检查就绪（最多 2 秒）
            for _ in range(20):
                time.sleep(0.1)
                if is_service_healthy("http://127.0.0.1:8081"):
                    logger.info("本地 WAA 签名服务已就绪 (PID %d)", _daemon_proc.pid)
                    return
        except Exception as e:
            logger.warning("启动本地 WAA 签名服务失败: %s", e)

    # 3. 若无本地二进制或拉起失败，回退到远程签名节点并给予明确操作引导
    build_cmd = (
        ".\\waa-signer\\build.bat (或 .\\waa-signer\\build.ps1)"
        if sys.platform == "win32"
        else "bash waa-signer/build.sh"
    )
    logger.warning(
        "未检测到本地 WAA 签名服务二进制产物: %s\n"
        "  ==> 当前已自动降级回退至远程签名节点 (remote_spinsnow)。\n"
        "  ==> 如需获得毫秒级本地原生性能并彻底脱离外部依赖，请运行构建脚本:\n"
        "      %s",
        bin_path,
        build_cmd,
    )
    switch_active_waa_server("remote_spinsnow")
def stop_signer_daemon() -> None:
    """终止自动拉起的本地签名服务子进程，杜绝孤儿进程与僵尸进程。"""
    global _daemon_proc
    if _daemon_proc is not None:
        proc = _daemon_proc
        _daemon_proc = None
        try:
            logger.info("停止本地 WAA 签名服务 (PID %d)", proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=0.5)
        except Exception as e:
            logger.debug("停止签名服务子进程异常: %s", e)
            with contextlib.suppress(Exception):
                proc.kill()


# 注册 Python 解释器退出钩子，双重保险杜绝孤儿进程
atexit.register(stop_signer_daemon)
