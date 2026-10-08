"""Optional, isolated 1111 background channels for model-shadow comparison.

No channel is enabled by default. This module does not modify specialist
results, public manifests, Markdown, ranking, or asset publication.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable
import uuid


Builder = Callable[[dict[str, Any]], tuple[dict[str, Any], Any]]
Command = Callable[[Path, Path], list[str]]
Merger = Callable[[Any, dict[str, Any]], dict[str, Any]]


def run_bounded_process(command, *, capture_output=True, text=True, timeout, env):
    """Kill the owned process group, not only its leader, on timeout.

    Output goes to temporary files; only a bounded tail is retained. This
    cannot upload data and is safe to run in an offline test directory.
    """
    if not isinstance(command, list) or not command or timeout <= 0:
        raise ValueError("invalid private process command or timeout")
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   env=env, start_new_session=os.name == "posix")
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait(timeout=5)
            raise
        finally:
            # A badly behaved leader can exit while leaving a child alive.
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        def tail(stream):
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 4096))
            return stream.read().decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(command, process.returncode, tail(stdout), tail(stderr))


def _assert_private(value):
    """Fail closed on absent flags as well as explicitly public payloads."""
    if not isinstance(value, dict) or any(value.get(key) is not False
        for key in ("display_allowed", "production_release_authorized")):
        raise RuntimeError("private response needs explicit false privacy flags")
    if value.get("public_effect", False) is not False:
        raise RuntimeError("private response cannot have public effects")


@dataclass(frozen=True)
class Channel:
    name: str
    model_domain: str
    enabled_env: str
    builder: Builder
    command: Command
    merger: Merger | None = None
    timeout_seconds: int = 60
    workers: int = 1
    queue_size: int = 8

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name):
            raise ValueError("invalid private shadow channel name")
        if self.workers < 1 or self.queue_size < self.workers or self.timeout_seconds < 1:
            raise ValueError("invalid private shadow resource limits")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _safe_taskid(taskid: str) -> str:
    if not isinstance(taskid, str) or taskid in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", taskid):
        raise ValueError("invalid taskid for private shadow path")
    return taskid


class PrivateShadowDispatcher:
    def __init__(self, results_root: Path, channels: list[Channel], *, process_runner=None) -> None:
        self.results_root = results_root.resolve()
        if len({channel.name for channel in channels}) != len(channels):
            raise ValueError("duplicate private shadow channel")
        self.channels = {channel.name: channel for channel in channels}
        self.pools = {
            channel.name: ThreadPoolExecutor(max_workers=channel.workers, thread_name_prefix=f"shadow-{channel.name}")
            for channel in channels
        }
        self.slots = {channel.name: threading.BoundedSemaphore(channel.queue_size) for channel in channels}
        self.process_runner = process_runner or run_bounded_process
        self._closed = False

    def close(self, *, wait=True) -> None:
        self._closed = True
        for pool in self.pools.values():
            pool.shutdown(wait=wait, cancel_futures=True)

    def submit(self, name: str, taskid: str, specialist_result: dict[str, Any]) -> Future[dict[str, Any]] | None:
        channel = self.channels[name]
        if self._closed:
            return None
        if os.getenv(channel.enabled_env, "0").strip().casefold() not in {"1", "true", "yes", "on"}:
            return None
        if not isinstance(specialist_result, dict) or specialist_result.get("model_domain") != channel.model_domain:
            return None
        _safe_taskid(taskid)
        if not self.slots[name].acquire(blocking=False):
            notice = self.results_root / taskid / "shadow" / channel.name / f"notice-{time.time_ns()}.json"
            _atomic_json(notice, {"mode": "shadow", "status": "queue_full", "channel": name,
                                  "public_effect": False, "display_allowed": False,
                                  "production_release_authorized": False})
            return None
        # Existing presentation code enriches result after submission; freeze
        # the input to avoid reading a concurrently mutated response.
        frozen = deepcopy(specialist_result)
        try:
            future = self.pools[name].submit(self.run_sync, name, taskid, frozen)
        except Exception:
            self.slots[name].release()
            raise
        future.add_done_callback(lambda _future: self.slots[name].release())
        return future

    def run_sync(self, name: str, taskid: str, specialist_result: dict[str, Any]) -> dict[str, Any]:
        channel = self.channels[name]
        _safe_taskid(taskid)
        if not isinstance(specialist_result, dict) or specialist_result.get("model_domain") != channel.model_domain:
            raise ValueError("specialist domain does not match private shadow channel")
        batch_id = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"
        directory = (self.results_root / taskid / "shadow" / channel.name / batch_id).resolve()
        if not directory.is_relative_to(self.results_root):
            raise ValueError("private shadow path escaped results root")
        directory.mkdir(parents=True, exist_ok=False)
        request_path, response_path = directory / "request.json", directory / "response.json"
        record: dict[str, Any] = {
            "channel": channel.name, "batch_id": batch_id, "model_domain": channel.model_domain,
            "mode": "shadow", "status": "running", "public_effect": False,
            "display_allowed": False, "production_release_authorized": False,
            "formal_result_mutated": False,
        }
        started = time.perf_counter()
        try:
            payload, mapping = channel.builder(specialist_result)
            items = payload.get("items", payload.get("requests"))
            if not isinstance(items, list):
                raise ValueError("private shadow builder returned no request array")
            record["request_count"] = len(items)
            if not items:
                record["status"] = "skipped_no_candidates"
                return record
            _atomic_json(request_path, payload)
            env = dict(os.environ)
            for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
                env[key] = "1"
            completed = self.process_runner(
                channel.command(request_path, response_path), capture_output=True, text=True,
                timeout=channel.timeout_seconds, env=env,
            )
            if completed.returncode != 0 or not response_path.is_file():
                raise RuntimeError(f"private shadow process exited {completed.returncode}")
            if response_path.is_symlink() or response_path.stat().st_size > 16 * 1024 * 1024:
                raise RuntimeError("private response path or size is invalid")
            response = json.loads(response_path.read_text(encoding="utf-8"))
            _assert_private(response)
            if response.get("ok") is not True:
                raise RuntimeError("private shadow response failed or violated privacy contract")
            if channel.merger:
                diagnostic = channel.merger(mapping, response)
                _assert_private(diagnostic)
                _atomic_json(directory / "comparison.json", diagnostic)
                rows = diagnostic.get("comparisons", [])
            else:
                rows = response.get("comparisons", response.get("results", []))
            if not isinstance(rows, list):
                raise RuntimeError("private shadow results must be a list")
            status_counts: dict[str, int] = {}
            for row in rows:
                _assert_private(row)
                status = str(row.get("status") or "unknown")
                status_counts[status] = status_counts.get(status, 0) + 1
            record.update({"status": "completed", "result_count": len(rows), "status_counts": status_counts,
                           "model": response.get("model")})
        except Exception as exc:
            record.update({"status": "failed", "error_type": type(exc).__name__})
        finally:
            record["elapsed_seconds"] = round(time.perf_counter() - started, 4)
            _atomic_json(directory / "manifest.json", record)
        return record
