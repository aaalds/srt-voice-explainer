#!/usr/bin/env python3
"""Shared local-runtime guards for long-running media/ML stages."""

from __future__ import annotations

import atexit
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def configure_local_runtime(cfg) -> Path:
    """Force network-offline model use and put compiler caches in project storage."""
    fallback = cfg.work / ".cache" / "numba"
    requested = os.environ.get("NUMBA_CACHE_DIR")
    cache_dir = Path(requested).expanduser() if requested else fallback
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        probe = cache_dir / f".write-probe-{os.getpid()}"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError:
        cache_dir = fallback
        cache_dir.mkdir(parents=True, exist_ok=True)

    os.environ["NUMBA_CACHE_DIR"] = str(cache_dir.resolve())
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return cache_dir


class FileLock:
    """One-byte advisory lock that is released automatically when the process dies."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self.handle = self.path.open("r+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError(
                f"another process already holds the runtime lock: {self.path}"
            ) from exc

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


class RuntimeSession:
    """Heartbeat, state file, non-overlap lock, and hard per-stage watchdog."""

    def __init__(
        self,
        *,
        name: str,
        lock_path: Path,
        state_path: Path,
        heartbeat_s: float = 20.0,
    ):
        self.name = name
        self.lock = FileLock(lock_path)
        self.state_path = state_path
        self.heartbeat_s = max(5.0, float(heartbeat_s))
        self.started_monotonic = time.monotonic()
        self.stop_event = threading.Event()
        self.state_guard = threading.Lock()
        self.state = {
            "name": name,
            "pid": os.getpid(),
            "status": "starting",
            "phase": "startup",
            "beat": None,
            "attempt": None,
            "started_at": self._now(),
            "updated_at": self._now(),
            "elapsed_s": 0.0,
            "message": "",
        }
        self.thread = None
        self.finalized = False

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _write_state(self) -> None:
        with self.state_guard:
            self.state["updated_at"] = self._now()
            self.state["elapsed_s"] = round(
                time.monotonic() - self.started_monotonic, 1
            )
            payload = json.dumps(self.state, ensure_ascii=False, indent=2)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(
            f".{self.state_path.name}.{os.getpid()}.tmp"
        )
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.state_path)

    def start(self) -> None:
        self.lock.acquire()
        self.state["status"] = "running"
        self._write_state()
        self.thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"{self.name}-heartbeat",
            daemon=True,
        )
        self.thread.start()
        atexit.register(self._finish_on_exit)

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.wait(self.heartbeat_s):
            self._write_state()
            with self.state_guard:
                phase = self.state["phase"]
                beat = self.state["beat"]
                elapsed = self.state["elapsed_s"]
            suffix = f" beat={beat}" if beat else ""
            print(
                f"[heartbeat] {self.name} phase={phase}{suffix} "
                f"elapsed={elapsed:.0f}s",
                flush=True,
            )

    def update(
        self,
        phase: str,
        *,
        beat: str | None = None,
        attempt: int | None = None,
        message: str = "",
    ) -> None:
        with self.state_guard:
            self.state.update(
                phase=phase,
                beat=beat,
                attempt=attempt,
                message=message,
            )
        self._write_state()

    @contextmanager
    def stage(
        self,
        phase: str,
        timeout_s: float,
        *,
        beat: str | None = None,
        attempt: int | None = None,
    ):
        self.update(phase, beat=beat, attempt=attempt)
        timer = threading.Timer(
            float(timeout_s),
            self._timeout_exit,
            kwargs={
                "phase": phase,
                "timeout_s": float(timeout_s),
                "beat": beat,
                "attempt": attempt,
            },
        )
        timer.daemon = True
        timer.start()
        try:
            yield
        finally:
            timer.cancel()

    def _timeout_exit(
        self,
        *,
        phase: str,
        timeout_s: float,
        beat: str | None,
        attempt: int | None,
    ) -> None:
        message = f"stage exceeded hard timeout of {timeout_s:.0f}s"
        with self.state_guard:
            self.state.update(
                status="timed_out",
                phase=phase,
                beat=beat,
                attempt=attempt,
                message=message,
            )
        self._write_state()
        print(
            f"[timeout] {self.name} phase={phase} beat={beat or '-'} "
            f"after {timeout_s:.0f}s",
            file=sys.stderr,
            flush=True,
        )
        self._finalize_resources()
        os._exit(124)

    def complete(self, message: str = "") -> None:
        with self.state_guard:
            self.state.update(status="completed", phase="done", message=message)
        self._write_state()
        self._finalize_resources()

    def fail(self, message: str) -> None:
        if self.finalized:
            return
        with self.state_guard:
            self.state.update(status="failed", message=message)
        self._write_state()
        self._finalize_resources()

    def _finish_on_exit(self) -> None:
        if self.finalized:
            return
        with self.state_guard:
            self.state.update(
                status="interrupted",
                message="process exited before marking the run complete",
            )
        self._write_state()
        self._finalize_resources()

    def _finalize_resources(self) -> None:
        if self.finalized:
            return
        self.finalized = True
        self.stop_event.set()
        if (
            self.thread is not None
            and self.thread.is_alive()
            and threading.current_thread() is not self.thread
        ):
            self.thread.join(timeout=1.0)
        self.lock.release()

