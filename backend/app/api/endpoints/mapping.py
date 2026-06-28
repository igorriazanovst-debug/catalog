from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db, async_session
from app.services.mapping_service import MappingService
from app.services.jobs import jobs, run_job
from app.services.llm_mapping_service import providers_status, provider_configured

router = APIRouter(prefix="/api/mapping", tags=["mapping"])


@router.get("/providers")
async def list_providers():
    """Провайдеры LLM-судьи с признаком, настроен ли каждый (есть ключи)."""
    return {"providers": providers_status()}


@router.post("/auto-map")
async def auto_map_products(
    confidence_threshold: float = 0.7,
    top_k: int = 20,
    supplier_id: int | None = None,
    only_unmapped: bool = False,
    provider: str | None = None,
):
    """
    Запускает автоматический маппинг товаров В ФОНЕ и сразу возвращает job_id.
    Прогресс/итог: GET /api/jobs/{job_id}.

    Пайплайн: гибридный ретрив (вектор ∪ keyword) -> LLM-судья. Маппинг тысяч
    товаров через LLM долгий, поэтому синхронный запрос упирался бы в таймаут
    шлюза (502). Если GPT даёт 100 ошибок подряд — задача завершается с понятной
    ошибкой (job.status='error', job.error=...).

    supplier_id — ограничить маппинг товарами одного поставщика
    (для «классифицировать только что загруженный прайс»).
    only_unmapped — маппить только товары без существующего маппинга.
    provider — LLM-судья: "yandex" | "groq" (по умолчанию из настроек).
    """
    # Провайдер должен быть настроен (есть ключи) — иначе сразу понятная 400,
    # а не серия ошибок внутри фоновой задачи.
    if provider and not provider_configured(provider):
        raise HTTPException(
            status_code=400,
            detail=f"Провайдер '{provider}' не настроен (нет API-ключа на сервере).",
        )

    job = jobs.create("classify")

    async def body(job):
        async with async_session() as db:
            service = MappingService(db)
            return await service.auto_map_all_products(
                llm_confidence_threshold=confidence_threshold, top_k=top_k,
                supplier_id=supplier_id, only_unmapped=only_unmapped,
                provider=provider,
                progress=lambda p, t, c: job.set_progress(p, t, c),
                max_consecutive_llm_errors=100,
            )

    run_job(job, body)
    return {"job_id": job.id}


@router.get("/candidates/{product_id}")
async def get_mapping_candidates(
    product_id: int,
    top_k: int = 20,
    db: AsyncSession = Depends(get_db)
):
    """Кандидаты для маппинга конкретного товара (без записи в БД)."""
    service = MappingService(db)
    candidates = await service.map_product_to_standards(product_id, top_k=top_k)
    if not candidates:
        raise HTTPException(status_code=404, detail="Товар не найден или нет кандидатов")
    return {"product_id": product_id, "candidates": candidates}