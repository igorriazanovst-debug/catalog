"""
Простой in-memory реестр фоновых задач (импорт прайса, классификация).

Зачем: импорт тысяч товаров и классификация через LLM — длинные операции.
Синхронный HTTP-запрос на них упирается в таймаут шлюза (502) и не даёт
прогресса. Поэтому endpoint стартует задачу в фоне (asyncio.create_task),
сразу отдаёт job_id, а UI опрашивает GET /api/jobs/{id}.

Состояние живёт в памяти процесса (uvicorn один процесс) — этого достаточно.
Завершённые задачи старше часа подчищаются.
"""

import asyncio
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


class Job:
    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind                     # 'import' | 'classify'
        self.status = "running"              # running | done | error
        self.total = 0
        self.processed = 0
        self.counters: dict[str, Any] = {}
        self.message = ""
        self.error: Optional[str] = None
        self.result: Any = None
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self._task: Optional[asyncio.Task] = None

    def set_progress(self, processed: int, total: int,
                     counters: Optional[dict] = None, message: str = ""):
        self.processed = processed
        self.total = total
        if counters is not None:
            self.counters = counters
        if message:
            self.message = message

    def to_dict(self) -> dict:
        now = time.time()
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "total": self.total,
            "processed": self.processed,
            "counters": self.counters,
            "message": self.message,
            "error": self.error,
            "result": self.result,
            "elapsed": round((self.finished_at or now) - self.started_at, 1),
        }


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}

    def create(self, kind: str) -> Job:
        self._gc()
        job = Job(kind)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def _gc(self, keep_seconds: float = 3600):
        now = time.time()
        for jid in [j.id for j in self._jobs.values()
                    if j.finished_at and now - j.finished_at > keep_seconds]:
            self._jobs.pop(jid, None)


jobs = JobManager()


def run_job(job: Job, body: Callable[[Job], Awaitable[Any]]) -> None:
    """Запускает корутину body(job) в фоне, проставляя статус/ошибку/итог."""
    async def runner():
        try:
            job.result = await body(job)
            if job.status == "running":
                job.status = "done"
        except Exception as e:  # noqa: BLE001
            logger.exception("Фоновая задача %s (%s) упала", job.id, job.kind)
            job.status = "error"
            job.error = str(e)
        finally:
            job.finished_at = time.time()

    job._task = asyncio.create_task(runner())
