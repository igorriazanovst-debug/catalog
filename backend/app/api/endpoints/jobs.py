from fastapi import APIRouter, HTTPException

from app.services.jobs import jobs

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}")
async def get_job(job_id: str):
    """Статус и прогресс фоновой задачи (импорт/классификация)."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="задача не найдена (возможно, устарела)")
    return job.to_dict()
