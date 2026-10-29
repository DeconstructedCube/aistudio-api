"""Session state machine, context descriptor, and atomic readiness gate."""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aistudio_api.infrastructure.browser.cdp_client import CDPPage


class SessionState(StrEnum):
    UNINITIALIZED = "UNINITIALIZED"
    PREPARING = "PREPARING"
    READY = "READY"
    FAILED = "FAILED"


@dataclass
class SessionContext:
    """Encapsulates a fully-bootstrapped, verified AI Studio page context."""

    page: CDPPage
    snap_key: str
    auth_user: str
    auth_file: str | None
    base_template: dict[str, object]
    tier: str = "Free"
    context_id: int = field(default_factory=itertools.count(1).__next__)
    created_at: float = field(default_factory=time.time)
    in_flight: int = 0
    drain_event: asyncio.Event = field(default_factory=asyncio.Event)

    def decr_in_flight(self) -> None:
        """Decrement in_flight count and signal drain_event when empty."""
        self.in_flight = max(0, self.in_flight - 1)
        if self.in_flight == 0:
            self.drain_event.set()

    def is_usable(self) -> bool:
        return not self.page.is_closed() and bool(self.snap_key)


class SessionReadinessGate:
    """Atomic readiness gate synchronizing concurrent requests during session transitions."""

    def __init__(self) -> None:
        self._state = SessionState.UNINITIALIZED
        self._active_context: SessionContext | None = None
        self._last_error: Exception | None = None
        self._event = asyncio.Event()

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def active_context(self) -> SessionContext | None:
        return self._active_context

    def is_ready(self) -> bool:
        return (
            self._state == SessionState.READY
            and self._active_context is not None
            and self._active_context.is_usable()
        )

    def set_preparing(self) -> None:
        """Mark session as transitioning / preparing; locks the gate for new requests."""
        self._state = SessionState.PREPARING
        self._event.clear()
        self._last_error = None

    def set_ready(self, context: SessionContext) -> None:
        """Mark session as fully verified; opens the gate for queued requests."""
        self._active_context = context
        self._state = SessionState.READY
        self._last_error = None
        self._event.set()

    def set_failed(self, exc: Exception) -> None:
        """Mark session transition as failed; wakes waiters to fail fast immediately."""
        self._state = SessionState.FAILED
        self._last_error = exc
        self._event.set()

    def reset(self) -> None:
        """Reset gate to UNINITIALIZED state."""
        self._state = SessionState.UNINITIALIZED
        self._active_context = None
        self._last_error = None
        self._event.clear()

    async def wait_ready(self, timeout_s: float = 120.0) -> SessionContext:
        """Wait until session is READY, or fail fast if transition failed."""
        if self.is_ready() and self._active_context is not None:
            return self._active_context

        if self._state == SessionState.FAILED and self._last_error:
            raise RuntimeError(
                f"Session transition failed: {self._last_error}"
            ) from self._last_error

        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout_s)
        except TimeoutError as err:
            raise TimeoutError(
                f"Timed out waiting for session readiness after {timeout_s}s"
            ) from err

        if self._state == SessionState.READY and self._active_context is not None:
            if self._active_context.is_usable():
                return self._active_context
            raise RuntimeError("Session active page closed unexpectedly")

        if self._state == SessionState.FAILED and self._last_error:
            raise RuntimeError(
                f"Session transition failed: {self._last_error}"
            ) from self._last_error

        raise RuntimeError(f"Session not ready: current state={self._state}")
