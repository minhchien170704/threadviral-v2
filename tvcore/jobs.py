"""
Job queue cho render video.

Render trước đây chạy blocking subprocess ngay trong event loop -> chặn server.
Bây giờ mỗi render là 1 job chạy nền, client poll tiến trình qua SSE.
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# job_id -> trạng thái
_JOBS: Dict[str, "RenderJob"] = {}
_JOBS_LOCK = asyncio.Lock()

# Giữ tối đa 100 job gần nhất
_MAX_JOBS = 100


@dataclass
class RenderJob:
    id: str
    status: str = "queued"          # queued | running | done | error
    progress: float = 0.0
    message: str = "Đang xếp hàng..."
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.id,
            "status": self.status,
            "progress": round(self.progress, 3),
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "elapsed": round(time.time() - self.created_at, 1),
        }


def _prune() -> None:
    """Xoá job cũ nếu vượt quá giới hạn."""
    if len(_JOBS) <= _MAX_JOBS:
        return
    ordered = sorted(_JOBS.values(), key=lambda j: j.created_at)
    for job in ordered[: len(_JOBS) - _MAX_JOBS]:
        if job.status in ("done", "error"):
            _JOBS.pop(job.id, None)


async def create_job() -> RenderJob:
    job = RenderJob(id=uuid.uuid4().hex[:12])
    async with _JOBS_LOCK:
        _JOBS[job.id] = job
        _prune()
    return job


def get_job(job_id: str) -> Optional[RenderJob]:
    return _JOBS.get(job_id)


async def update_job(job_id: str, progress: float, message: str) -> None:
    job = _JOBS.get(job_id)
    if job:
        job.progress = max(0.0, min(1.0, progress))
        job.message = message
        job.updated_at = time.time()


async def finish_job(job_id: str, result: Dict[str, Any]) -> None:
    job = _JOBS.get(job_id)
    if job:
        job.status = "done"
        job.progress = 1.0
        job.message = "Hoàn tất!"
        job.result = result
        job.updated_at = time.time()


async def fail_job(job_id: str, error: str) -> None:
    job = _JOBS.get(job_id)
    if job:
        job.status = "error"
        job.message = "Thất bại"
        job.error = error
        job.updated_at = time.time()


def list_jobs() -> List[Dict[str, Any]]:
    return [j.as_dict() for j in sorted(_JOBS.values(), key=lambda x: x.created_at, reverse=True)]
