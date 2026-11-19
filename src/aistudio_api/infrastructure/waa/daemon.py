"""Local waa-signer subprocess manager using multiplexed stdio NDJSON IPC.

Lifecycle guarantees:
1. Parent-child pipe binding: when Python exits or crashes, the OS closes the
   child's stdin write end, causing Go's stdin reader to hit EOF and exit.
2. Linux kernel PR_SET_PDEATHSIG (SIGTERM) on fork/exec as an extra safeguard.
3. Explicit graceful shutdown (`shutdown` RPC + stdin EOF + wait/terminate/kill)
   and synchronous atexit fallback.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import ctypes
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from aistudio_api.domain.errors import TransportError
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("waa.daemon")

_PR_SET_PDEATHSIG = 1
_MAX_IPC_LINE_BYTES = 64 * 1024 * 1024


def _repo_root() -> Path:
    """Repository root directory (4 levels above this module)."""
    return Path(__file__).resolve().parents[4]


def signer_binary_path(root_dir: Path | None = None) -> Path:
    """Return expected path to the compiled local waa-signer binary."""
    bin_name = "waa-signer.exe" if sys.platform == "win32" else "waa-signer"
    return (root_dir or _repo_root()) / "waa-signer" / "bin" / bin_name


def is_signer_binary_ready(root_dir: Path | None = None) -> bool:
    """Check whether the local waa-signer binary exists and is executable."""
    bin_path = signer_binary_path(root_dir)
    return bin_path.is_file() and (
        sys.platform == "win32" or os.access(bin_path, os.X_OK)
    )


def _missing_binary_message(bin_path: Path) -> str:
    install_cmd = ".\\install.bat" if sys.platform == "win32" else "bash install.sh"
    return (
        f"未检测到本地 WAA 签名器二进制文件: {bin_path}。"
        f"请先运行一键安装编译脚本: {install_cmd}"
    )


def _set_parent_death_signal() -> None:
    """Linux kernel-level hook: child process receives SIGTERM if parent dies."""
    if sys.platform != "linux":
        return
    try:
        libc = ctypes.CDLL(None)
        libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)
    except Exception:
        pass


class LocalSignerProcess:
    """Manages a single local waa-signer child process over multiplexed stdio NDJSON."""

    def __init__(self, binary_path: Path | None = None) -> None:
        self._binary_path = binary_path
        self._proc: asyncio.subprocess.Process | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._start_lock: asyncio.Lock | None = None
        self._write_lock: asyncio.Lock | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, object]]] = {}
        self._ready_future: asyncio.Future[dict[str, object]] | None = None
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None

    def _resolve_binary(self) -> Path:
        return self._binary_path or signer_binary_path()

    def _ensure_loop_primitives(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop = loop
            self._start_lock = asyncio.Lock()
            self._write_lock = asyncio.Lock()
        return loop

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def _build_command(self, bin_path: Path) -> list[str]:
        root_dir = _repo_root()
        state_dir = root_dir / "data" / "waa_workers"
        state_dir.mkdir(parents=True, exist_ok=True)
        cmd = [str(bin_path), "-state-dir", str(state_dir)]
        proxy = (
            os.getenv("AISTUDIO_PROXY")
            or os.getenv("HTTPS_PROXY")
            or os.getenv("https_proxy")
            or ""
        ).strip()
        if proxy:
            cmd.extend(["-proxy", proxy])
        timezone = os.getenv("AISTUDIO_TIMEZONE", "").strip()
        if timezone:
            cmd.extend(["-timezone", timezone])
        locale = (os.getenv("AISTUDIO_LOCALE") or os.getenv("WAA_LOCALE") or "").strip()
        if locale:
            cmd.extend(["-locale", locale])
        return cmd

    async def ensure_started(self) -> None:
        """Start the local waa-signer IPC child process if not already running."""
        loop = self._ensure_loop_primitives()
        assert self._start_lock is not None
        async with self._start_lock:
            if self.is_running:
                return

            bin_path = self._resolve_binary()
            if not (
                bin_path.is_file()
                and (sys.platform == "win32" or os.access(bin_path, os.X_OK))
            ):
                raise TransportError(_missing_binary_message(bin_path))

            cmd = self._build_command(bin_path)
            popen_kwargs: dict[str, object] = {
                "stdin": asyncio.subprocess.PIPE,
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
                "cwd": str(bin_path.parent.parent),
                "limit": _MAX_IPC_LINE_BYTES,
            }
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                )
            else:
                popen_kwargs["preexec_fn"] = _set_parent_death_signal

            logger.info("启动本地 WAA IPC 签名进程: %s", bin_path)
            self._ready_future = loop.create_future()
            self._pending.clear()
            self._proc = await asyncio.create_subprocess_exec(*cmd, **popen_kwargs)  # type: ignore[arg-type]
            self._stdout_task = asyncio.create_task(self._read_stdout_loop())
            self._stderr_task = asyncio.create_task(self._read_stderr_loop())

            try:
                ready = await asyncio.wait_for(self._ready_future, timeout=10.0)
                logger.info(
                    "本地 WAA IPC 签名进程已就绪 (PID=%d, engine=%s)",
                    self._proc.pid,
                    ready.get("engine", "spidermonkey-152"),
                )
            except Exception as exc:
                await self.stop()
                raise TransportError(f"本地 WAA 签名进程启动失败: {exc}") from exc

    async def _read_stdout_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        close_reason = "waa-signer IPC stdout closed"
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "忽略非法的 waa-signer IPC 响应: %s (%s)", text[:200], exc
                    )
                    continue
                if not isinstance(msg, dict):
                    continue

                if msg.get("event") == "ready":
                    if self._ready_future is not None and not self._ready_future.done():
                        self._ready_future.set_result(msg)
                    continue

                raw_id = msg.get("id")
                if isinstance(raw_id, int):
                    fut = self._pending.pop(raw_id, None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
        except asyncio.CancelledError:
            close_reason = "waa-signer IPC reader cancelled"
            raise
        except Exception as exc:
            close_reason = f"waa-signer IPC stdout error: {exc}"
            logger.warning("读取 waa-signer IPC 输出异常: %s", exc)
        finally:
            if self._ready_future is not None and not self._ready_future.done():
                self._ready_future.set_exception(TransportError(close_reason))
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(TransportError(close_reason))
            self._pending.clear()

    async def _read_stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if not text:
                    continue
                if "level=ERROR" in text:
                    logger.error("[waa-signer] %s", text)
                elif "level=WARN" in text:
                    logger.warning("[waa-signer] %s", text)
                else:
                    logger.debug("[waa-signer] %s", text)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def request(
        self,
        method: str,
        params: dict[str, object] | None = None,
        *,
        timeout: float = 95.0,
    ) -> dict[str, object]:
        """Send one IPC request over stdin and await its correlated response."""
        await self.ensure_started()
        loop = self._ensure_loop_primitives()
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            raise TransportError("本地 WAA 签名进程不可用")

        assert self._write_lock is not None
        self._next_id += 1
        req_id = self._next_id
        fut: asyncio.Future[dict[str, object]] = loop.create_future()
        self._pending[req_id] = fut

        payload: dict[str, object] = {"id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")

        try:
            async with self._write_lock:
                proc.stdin.write(encoded)
                await proc.stdin.drain()
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.CancelledError:
            self._pending.pop(req_id, None)
            raise
        except TimeoutError as exc:
            self._pending.pop(req_id, None)
            raise TransportError(f"waa-signer IPC 请求超时 ({method})") from exc
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            self._pending.pop(req_id, None)
            await self.stop()
            raise TransportError(f"waa-signer IPC 管道中断: {exc}") from exc
        except Exception:
            self._pending.pop(req_id, None)
            raise

    async def health(self, *, timeout: float = 5.0) -> dict[str, object]:
        """Query local IPC signer health and active worker count."""
        return await self.request("health", timeout=timeout)

    async def stop(self) -> None:
        """Gracefully shut down the local waa-signer child process."""
        proc = self._proc
        self._proc = None

        if proc is not None and proc.returncode is None:
            pid = proc.pid
            logger.info("停止本地 WAA IPC 签名进程 (PID %d)", pid)
            if proc.stdin is not None:
                with contextlib.suppress(Exception):
                    self._next_id += 1
                    shutdown_msg = (
                        json.dumps({"id": self._next_id, "method": "shutdown"}) + "\n"
                    ).encode("utf-8")
                    proc.stdin.write(shutdown_msg)
                    await proc.stdin.drain()
                with contextlib.suppress(Exception):
                    proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                with contextlib.suppress(Exception):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=1.0)
                except TimeoutError:
                    with contextlib.suppress(Exception):
                        proc.kill()
                        await proc.wait()

        for task in (self._stdout_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
        self._stdout_task = None
        self._stderr_task = None
        self._ready_future = None
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    def stop_sync(self) -> None:
        """Best-effort synchronous cleanup for interpreter exit (atexit)."""
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(Exception):
            if proc.stdin is not None:
                proc.stdin.close()
        with contextlib.suppress(Exception):
            proc.terminate()


_signer_process: LocalSignerProcess | None = None


def get_signer_process() -> LocalSignerProcess:
    """Return the shared singleton local IPC signer process manager."""
    global _signer_process
    if _signer_process is None:
        _signer_process = LocalSignerProcess()
    return _signer_process


async def ensure_signer_ready() -> None:
    """Verify binary and start local waa-signer IPC process on service startup."""
    bin_path = signer_binary_path()
    if not is_signer_binary_ready():
        logger.error("%s", _missing_binary_message(bin_path))
        return
    try:
        await get_signer_process().ensure_started()
    except Exception as exc:
        logger.warning("预启动本地 WAA IPC 签名进程失败（将在首次请求时重试）: %s", exc)


async def stop_signer_daemon() -> None:
    """Stop the shared local waa-signer IPC process cleanly."""
    if _signer_process is not None:
        await _signer_process.stop()


def _atexit_cleanup() -> None:
    if _signer_process is not None:
        _signer_process.stop_sync()


atexit.register(_atexit_cleanup)
