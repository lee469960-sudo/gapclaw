"""Dedicated in-process queue for Code runs, separate from Standard tasks."""

from __future__ import annotations

import queue
import threading
import uuid
import logging


logger = logging.getLogger(__name__)


class CodeRunQueue:
    def __init__(self, workers: int = 20):
        self.workers = max(1, workers)
        self._jobs: queue.Queue = queue.Queue()
        self._started = False
        self._lock = threading.Lock()

    def _ensure_started(self) -> None:
        with self._lock:
            if self._started:
                return
            for index in range(self.workers):
                threading.Thread(
                    target=self._worker,
                    name=f"code-run-{index + 1}",
                    daemon=True,
                ).start()
            self._started = True

    def _worker(self) -> None:
        while True:
            _job_id, function, args = self._jobs.get()
            try:
                function(*args)
            except Exception:
                logger.exception("dedicated Code run job failed job_id=%s", _job_id)
            finally:
                self._jobs.task_done()

    def submit(self, function, *args) -> str:
        self._ensure_started()
        job_id = uuid.uuid4().hex
        self._jobs.put((job_id, function, args))
        return job_id

    @property
    def pending(self) -> int:
        return self._jobs.qsize()


code_run_queue = CodeRunQueue()
