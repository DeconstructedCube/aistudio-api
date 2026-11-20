"""Local waa-signer subprocess manager using multiplexed stdio NDJSON IPC.

Lifecycle guarantees:
1. Parent-child pipe binding: when Python exits or crashes, the OS closes the
   child's stdin write end, causing Go's stdin reader to hit EOF and exit.
2. Explicit graceful shutdown (`shutdown` RPC + stdin EOF + wait/terminate/kill)
   and synchronous atexit fallback.
3. No unsafe post-fork `preexec_fn` / `ctypes` calls (avoids Bionic linker
   deadlocks and spurious SIGTERM on Android Termux / multi-threaded Python).
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import errno
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path

from aistudio_api.config import settings
from aistudio_api.domain.errors import TransportError
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("waa.daemon")

_MAX_IPC_LINE_BYTES = 64 * 1024 * 1024
_NOEXEC_PATH_PREFIXES = ("/sdcard/", "/storage/", "/mnt/sdcard/", "/mnt/user/")


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


def _format_exit_status(returncode: int | None) -> str:
    if returncode is None:
        return "running"
    if returncode < 0:
        sig_num = -returncode
        try:
            sig_name = signal.Signals(sig_num).name
        except ValueError:
            sig_name = f"SIG{sig_num}"
        return f"exit={returncode} ({sig_name})"
    return f"exit={returncode}"


def _is_termux_env(env: dict[str, str]) -> bool:
    prefix = env.get("PREFIX", "")
    return "/com.termux" in prefix or Path("/data/data/com.termux/files/usr").is_dir()


def _is_likely_noexec_path(path: Path) -> bool:
    if sys.platform == "win32":
        return False
    path_str = str(path)
    try:
        resolved_str = str(path.resolve())
    except OSError:
        resolved_str = path_str
    return any(s.startswith(_NOEXEC_PATH_PREFIXES) for s in (path_str, resolved_str))


def _stage_executable_binary(bin_path: Path) -> Path:
    """Copy binary onto an executable local directory when repo lives on Android noexec storage."""
    prefix = os.getenv("PREFIX", "").strip()
    candidates: list[Path] = []
    if prefix:
        candidates.append(Path(prefix) / "tmp" / "aistudio-api-bin")
    candidates.append(Path(tempfile.gettempdir()) / "aistudio-api-bin")
    candidates.append(Path.home() / ".cache" / "aistudio-api-bin")

    src_stat = bin_path.stat()
    for stage_dir in candidates:
        if _is_likely_noexec_path(stage_dir):
            continue
        try:
            stage_dir.mkdir(parents=True, exist_ok=True)
            staged = stage_dir / bin_path.name
            needs_copy = True
            if staged.is_file():
                dst_stat = staged.stat()
                if (
                    dst_stat.st_size == src_stat.st_size
                    and int(dst_stat.st_mtime) == int(src_stat.st_mtime)
                    and os.access(staged, os.X_OK)
                ):
                    needs_copy = False
            if needs_copy:
                tmp_staged = stage_dir / f".{bin_path.name}.tmp.{os.getpid()}"
                shutil.copy2(bin_path, tmp_staged)
                tmp_staged.chmod(0o755)
                tmp_staged.replace(staged)
            return staged
        except OSError:
            continue
    return bin_path


def _is_fatal_stderr_line(text: str) -> bool:
    lower = text.lower()
    return any(
        marker in lower
        for marker in (
            "level=error",
            "sigsys",
            "sigsegv",
            "sigill",
            "sigabrt",
            "sigbus",
            "bad system call",
            "panic:",
            "fatal error:",
            "flag provided but not defined",
            "cannot execute",
            "permission denied",
            "exec format error",
            "address already in use",
        )
    )


def _diagnose_startup_failure(
    base_reason: str,
    returncode: int | None,
    stderr_lines: list[str],
) -> str:
    details: list[str] = [base_reason]
    if returncode is not None:
        details.append(_format_exit_status(returncode))
    stderr_text = " | ".join(stderr_lines[-8:]).strip()
    if stderr_text:
        details.append(f"stderr: {stderr_text}")

    combined = f"{base_reason} {stderr_text}".lower()
    hint = ""
    if returncode == -31 or "sigsys" in combined or "bad system call" in combined:
        hint = "提示: 触发 Android SECCOMP SIGSYS，请更新仓库后重新执行 bash waa-signer/build.sh 编译最新签名器"
    elif (
        "flag provided but not defined" in combined
        or "http 服务" in combined
        or "address already in use" in combined
    ):
        hint = "提示: 检测到旧版 HTTP 模式 waa-signer 二进制，请执行 bash waa-signer/build.sh 重新编译 IPC 版本"
    elif returncode == -4 or "sigill" in combined:
        hint = "提示: CPU 指令集不兼容 (SIGILL)，请确认 Termux 为 64 位 arm64-v8a 版本并重新执行 bash waa-signer/build.sh"
    elif (
        returncode in (126, 127)
        or "permission denied" in combined
        or "exec format error" in combined
    ):
        hint = "提示: 二进制无法执行（可能位于 /sdcard noexec 分区或架构不匹配），请将项目移至 Termux 主目录 (~) 后重新执行 bash install.sh"
    elif not stderr_text and returncode is not None:
        hint = "提示: 签名进程启动后无输出直接退出，请手动运行 ./waa-signer/bin/waa-signer -check 查看具体报错，或执行 bash waa-signer/build.sh 重新编译"

    msg = f"本地 WAA 签名进程启动失败: {'; '.join(details)}"
    if hint:
        msg = f"{msg} ({hint})"
    return msg


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
        self._stderr_tail: deque[str] = deque(maxlen=30)

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

    def _resolve_state_dir(self) -> Path:
        root_dir = _repo_root()
        candidates = [
            root_dir / "data" / "waa_workers",
            Path(tempfile.gettempdir()) / "aistudio_waa_workers",
        ]
        for state_dir in candidates:
            try:
                state_dir.mkdir(parents=True, exist_ok=True)
                probe = state_dir / f".write_probe_{os.getpid()}"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                return state_dir
            except OSError:
                continue
        return candidates[-1]

    def _build_command(self, bin_path: Path) -> list[str]:
        state_dir = self._resolve_state_dir()
        cmd = [str(bin_path), f"-state-dir={state_dir}"]
        proxy = (
            os.getenv("AISTUDIO_PROXY")
            or os.getenv("HTTPS_PROXY")
            or os.getenv("https_proxy")
            or os.getenv("HTTP_PROXY")
            or os.getenv("http_proxy")
            or settings.proxy_url
            or ""
        ).strip()
        if proxy:
            cmd.append(f"-proxy={proxy}")
        timezone = (os.getenv("AISTUDIO_TIMEZONE") or settings.timezone or "").strip()
        if timezone:
            cmd.append(f"-timezone={timezone}")
        locale = (os.getenv("AISTUDIO_LOCALE") or os.getenv("WAA_LOCALE") or "").strip()
        if locale:
            cmd.append(f"-locale={locale}")
        return cmd

    @staticmethod
    def _build_child_env() -> dict[str, str]:
        child_env = os.environ.copy()
        if sys.platform == "win32":
            return child_env
        if _is_termux_env(child_env):
            # Statically linked Linux musl binary must not inherit Termux Bionic preload/library overrides.
            if "com.termux" in child_env.get("LD_PRELOAD", ""):
                child_env.pop("LD_PRELOAD", None)
            if "com.termux" in child_env.get("LD_LIBRARY_PATH", ""):
                child_env.pop("LD_LIBRARY_PATH", None)
            if not child_env.get("TMPDIR"):
                prefix = child_env.get("PREFIX", "").strip()
                termux_tmp = (
                    Path(prefix) / "tmp"
                    if prefix
                    else Path("/data/data/com.termux/files/usr/tmp")
                )
                if termux_tmp.is_dir():
                    child_env["TMPDIR"] = str(termux_tmp)
        if not child_env.get("SSL_CERT_FILE"):
            prefix = child_env.get("PREFIX", "").strip()
            for cert_cand in (
                Path(prefix) / "etc" / "tls" / "cert.pem" if prefix else None,
                Path("/data/data/com.termux/files/usr/etc/tls/cert.pem"),
            ):
                if cert_cand is not None and cert_cand.is_file():
                    child_env["SSL_CERT_FILE"] = str(cert_cand)
                    break
        if not child_env.get("SSL_CERT_DIR"):
            prefix = child_env.get("PREFIX", "").strip()
            for dir_cand in (
                Path(prefix) / "etc" / "tls" / "certs" if prefix else None,
                Path("/data/data/com.termux/files/usr/etc/tls/certs"),
                Path("/system/etc/security/cacerts"),
            ):
                if dir_cand is not None and dir_cand.is_dir():
                    child_env["SSL_CERT_DIR"] = str(dir_cand)
                    break
        return child_env

    async def ensure_started(self) -> None:
        """Start the local waa-signer IPC child process if not already running."""
        loop = self._ensure_loop_primitives()
        assert self._start_lock is not None
        async with self._start_lock:
            if self.is_running:
                return

            bin_path = self._resolve_binary()
            if not bin_path.is_file():
                raise TransportError(_missing_binary_message(bin_path))
            exec_path = bin_path
            if sys.platform != "win32":
                if not os.access(exec_path, os.X_OK):
                    with contextlib.suppress(OSError):
                        exec_path.chmod(0o755)
                if _is_likely_noexec_path(exec_path) or not os.access(
                    exec_path, os.X_OK
                ):
                    staged = _stage_executable_binary(exec_path)
                    if staged != exec_path:
                        logger.warning(
                            "检测到签名器位于受限路径 %s，已自动暂存至可执行目录: %s",
                            exec_path,
                            staged,
                        )
                        exec_path = staged
                if not os.access(exec_path, os.X_OK):
                    raise TransportError(_missing_binary_message(bin_path))

            cmd = self._build_command(exec_path)
            child_env = self._build_child_env()
            cwd_dir = bin_path.parent.parent
            if not cwd_dir.is_dir():
                cwd_dir = Path(tempfile.gettempdir())
            popen_kwargs: dict[str, object] = {
                "stdin": asyncio.subprocess.PIPE,
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
                "cwd": str(cwd_dir),
                "env": child_env,
                "limit": _MAX_IPC_LINE_BYTES,
            }
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                )

            logger.info("启动本地 WAA IPC 签名进程: %s", exec_path)
            self._ready_future = loop.create_future()
            self._pending.clear()
            self._stderr_tail.clear()
            try:
                self._proc = await asyncio.create_subprocess_exec(*cmd, **popen_kwargs)  # type: ignore[arg-type]
            except OSError as exc:
                if (
                    sys.platform != "win32"
                    and exc.errno == errno.EACCES
                    and exec_path == bin_path
                ):
                    staged = _stage_executable_binary(bin_path)
                    if staged != bin_path:
                        logger.warning(
                            "直接执行 %s 返回 EACCES，已自动暂存至 %s 重试",
                            bin_path,
                            staged,
                        )
                        cmd[0] = str(staged)
                        self._proc = await asyncio.create_subprocess_exec(
                            *cmd,
                            **popen_kwargs,  # type: ignore[arg-type]
                        )
                    else:
                        raise TransportError(
                            _diagnose_startup_failure(str(exc), None, [])
                        ) from exc
                else:
                    raise TransportError(
                        _diagnose_startup_failure(str(exc), None, [])
                    ) from exc

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
                proc = self._proc
                returncode: int | None = None
                if proc is not None:
                    with contextlib.suppress(TimeoutError, Exception):
                        returncode = await asyncio.wait_for(proc.wait(), timeout=0.5)
                if self._stderr_task is not None and not self._stderr_task.done():
                    with contextlib.suppress(TimeoutError, BaseException):
                        await asyncio.wait_for(
                            asyncio.shield(self._stderr_task), timeout=0.3
                        )
                stderr_snapshot = list(self._stderr_tail)
                await self.stop()
                diag_msg = _diagnose_startup_failure(
                    str(exc), returncode, stderr_snapshot
                )
                logger.error("%s", diag_msg)
                raise TransportError(diag_msg) from exc

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
                self._stderr_tail.append(text)
                if _is_fatal_stderr_line(text):
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
        encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode(
            "utf-8", errors="replace"
        )

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
