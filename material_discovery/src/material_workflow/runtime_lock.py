"""Interprocess bounded GPU queue for shadow/OMatG requests sharing this server."""
from contextlib import contextmanager
import os
from pathlib import Path
import time


@contextmanager
def gpu_job_lock():
    import fcntl
    path = Path(os.getenv("OMATG_GPU_LOCK", "/data/se42/omatg_local/runtime/gpu.lock"))
    path.parent.mkdir(parents=True, exist_ok=True)
    timeout = int(os.getenv("OMATG_QUEUE_TIMEOUT_SEC", "900"))
    start = time.monotonic()
    with path.open("a+") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic()-start >= timeout:
                    raise TimeoutError("OMatG GPU queue exceeded its wait budget; no generation was started")
                time.sleep(0.25)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
