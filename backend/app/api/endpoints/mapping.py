from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.services.mapping_service import MappingService

router = APIRouter(prefix="/api/mapping", tags=["mapping"])

@router.post("/auto-map")
async def auto_map_products(
    confidence_threshold: float = 0.7,
    top_k: int = 15,
    db: AsyncSession = Depends(get_db)
):
    """
    Автоматический маппинг всех товаров: гибридный ретрив (вектор ∪ keyword)
    -> LLM-судья. confidence_threshold — порог уверенности LLM для авто-маппинга
    (ниже порога товар помечается на ручную проверку).
    """
    service = MappingService(db)
    result = await service.auto_map_all_products(
        llm_confidence_threshold=confidence_threshold, top_k=top_k
    )
    return result

@router.get("/candidates/{product_id}")
async def get_mapping_candidates(
    product_id: int,
    top_k: int = 15,
    db: AsyncSession = Depends(get_db)
):
    """
    Получить кандидатов для маппинга конкретного товара
    """
    service = MappingService(db)
    candidates = await service.map_product_to_standards(product_id, top_k=top_k)
    
    if not candidates:
        raise HTTPException(status_code=404, detail="Товар не найден или нет кандидатов")
    
    return {
        "product_id": product_id,
        "candidates": candidates
    }