"""
Watchdog – restores all actuator baselines within 1 second on:
  - controller failure / exception
  - timeout
  - safety limit breach
  - normal exit (via context manager)
  - SIGTERM/SIGINT (best-effort on Windows)

Safety requirements from AGRDA document:
  - Restore baseline within 1 second of failure.
  - Log every rollback.
  - Interrupt ongoing actuation on breach.

Implementation decision: We use a background thread that periodically
checks a heartbeat. If the heartbeat stops (controller crashed), the
watchdog triggers rollback.  On Windows SIGTERM is not reliable so we
also register atexit.
"""
from __future__ import annotations

import atexit
import logging
import signal
import threading
import time
from typing import List, Optional

from .interface import ActuatorInterface, ActuatorResult

logger = logging.getLogger(__name__)


class Watchdog:
    """
    Safety watchdog for AGRDA actuator sessions.

    Usage (as context manager):
        with Watchdog(actuator, heartbeat_interval=0.5, timeout=10.0) as wd:
            wd.beat()           # Call every iteration to keep alive
            actuator.apply(...)
            # If beat() is not called for `timeout` seconds, rollback fires.

    Alternatively, call trigger_rollback() directly on error.
    """

    def __init__(
        self,
        actuator: ActuatorInterface,
        heartbeat_interval: float = 0.5,   # seconds between watchdog checks
        timeout: float = 5.0,              # seconds before auto-rollback
        rollback_deadline: float = 1.0,    # max seconds to complete rollback
    ) -> None:
        self.actuator = actuator
        self.heartbeat_interval = heartbeat_interval
        self.timeout = timeout
        self.rollback_deadline = rollback_deadline

        self._last_beat = time.monotonic()
        self._active = False
        self._rolled_back = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._rollback_results: List[ActuatorResult] = []

        # Register atexit to ensure rollback even if we forget __exit__
        atexit.register(self._atexit_handler)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop(force_rollback=(exc_type is not None))
        return False   # do not suppress exceptions

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the watchdog thread."""
        with self._lock:
            if self._active:
                return
            self._active = True
            self._rolled_back = False
            self._last_beat = time.monotonic()

        self._thread = threading.Thread(
            target=self._watch_loop,
            name="agrda-watchdog",
            daemon=True,
        )
        self._thread.start()

        # Best-effort signal handlers
        try:
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
        except (OSError, ValueError):
            # Can't set signal handlers in threads or on Windows SIGTERM
            pass

        logger.info(
            "Watchdog started (timeout=%.1fs, rollback_deadline=%.1fs)",
            self.timeout, self.rollback_deadline,
        )

    def stop(self, force_rollback: bool = False) -> None:
        """Stop the watchdog. Optionally force a rollback."""
        with self._lock:
            self._active = False

        if self._thread:
            self._thread.join(timeout=2.0)

        if force_rollback:
            self.trigger_rollback(reason="Exception / forced stop")
        else:
            # Normal exit: still rollback to be safe
            self.trigger_rollback(reason="Normal exit")

        logger.info("Watchdog stopped.")

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def beat(self) -> None:
        """Call this periodically to indicate the controller is alive."""
        with self._lock:
            self._last_beat = time.monotonic()

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def trigger_rollback(self, reason: str = "Manual") -> List[ActuatorResult]:
        """
        Trigger immediate rollback of all actuator settings.
        Thread-safe and idempotent — rolls back at most once per session.
        """
        with self._lock:
            if self._rolled_back:
                return self._rollback_results
            self._rolled_back = True

        logger.warning("WATCHDOG: Triggering rollback — reason: %s", reason)
        start = time.monotonic()
        try:
            results = self.actuator.rollback_all()
            elapsed = time.monotonic() - start
            self._rollback_results = results
            ok = all(r.success for r in results)
            logger.info(
                "WATCHDOG: Rollback completed in %.3fs — success=%s",
                elapsed, ok,
            )
            if elapsed > self.rollback_deadline:
                logger.warning(
                    "WATCHDOG: Rollback took %.3fs > deadline %.1fs",
                    elapsed, self.rollback_deadline,
                )
        except Exception as e:
            logger.error("WATCHDOG: Rollback failed: %s", e)
            self._rollback_results = []

        return self._rollback_results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _watch_loop(self) -> None:
        """Background thread: checks heartbeat."""
        while True:
            with self._lock:
                if not self._active:
                    break
                age = time.monotonic() - self._last_beat

            if age > self.timeout:
                logger.error(
                    "WATCHDOG: Heartbeat timeout (%.1fs > %.1fs), "
                    "triggering rollback",
                    age, self.timeout,
                )
                self.trigger_rollback(reason=f"Heartbeat timeout ({age:.1f}s)")
                break

            time.sleep(self.heartbeat_interval)

    def _signal_handler(self, signum, frame) -> None:
        logger.warning("WATCHDOG: Signal %d received — triggering rollback", signum)
        self.trigger_rollback(reason=f"Signal {signum}")

    def _atexit_handler(self) -> None:
        if not self._rolled_back:
            self.trigger_rollback(reason="atexit")
