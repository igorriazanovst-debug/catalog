from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import pandas as pd
import io
from app.core.database import get_db
from app.services.product_service import ProductService
from sentence_transformers import SentenceTransformer

router = APIRouter(prefix="/api/products", tags=["products"])

# Глобальная модель для эмбеддингов (загружается один раз)
embedding_model = None

def get_embedding_model():
    global embedding_model
    if embedding_model is None:
        embedding_model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-mpnet-base-v2")
    return embedding_model

@router.post("/upload")
async def upload_products(
    file: UploadFile = File(...),
    supplier_name: str = Form(...),
    supplier_short_name: str = Form(None),
    supplier_inn: str = Form(None),
    supplier_contact_person: str = Form(None),
    supplier_phone: str = Form(None),
    supplier_email: str = Form(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Загрузка товаров из CSV файла с данными поставщика
    """
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Файл должен быть в формате CSV")
    
    try:
        # Читаем CSV
        content = await file.read()
        df = pd.read_csv(io.BytesIO(content), sep=';', encoding='utf-8')
        
        # Проверяем обязательные колонки
        required_columns = ['Артикул', 'Наименование', 'Себестоимость', 'РРЦ']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise HTTPException(
                status_code=400, 
                detail=f"Отсутствуют обязательные колонки: {', '.join(missing_columns)}"
            )
        
        # Создаем или находим поставщика
        supplier_service = ProductService(db)
        supplier_id = await supplier_service.get_or_create_supplier(
            name=supplier_name,
            short_name=supplier_short_name,
            inn=supplier_inn,
            contact_person=supplier_contact_person,
            phone=supplier_phone,
            email=supplier_email
        )
        
        # Импортируем товары
        result = await supplier_service.import_products_from_csv(df, supplier_id)
        
        return {
            "status": "success",
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "products_imported": result['imported'],
            "products_updated": result['updated'],
            "errors": result['errors']
        }
        
    except pd.errors.EmptyDataError:
        raise HTTPException(status_code=400, detail="CSV файл пустой")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при обработке файла: {str(e)}")


@router.get("/suppliers")
async def list_suppliers(db: AsyncSession = Depends(get_db)):
    """Список поставщиков со счётчиками товаров и статусом маппинга."""
    res = await db.execute(text("""
        SELECT
            s.id, s.name, s.short_name, s.inn, s.created_at,
            COUNT(DISTINCT sp.product_id) AS products_total,
            COUNT(DISTINCT m.product_id) FILTER (WHERE NOT m.rejected) AS mapped,
            COUNT(DISTINCT m.product_id) FILTER (WHERE NOT m.is_manual AND NOT m.rejected) AS auto,
            COUNT(DISTINCT m.product_id) FILTER (WHERE m.is_manual AND NOT m.rejected) AS manual
        FROM suppliers s
        LEFT JOIN supplier_products sp ON sp.supplier_id = s.id
        LEFT JOIN product_standard_mapping m ON m.product_id = sp.product_id
        GROUP BY s.id
        ORDER BY s.created_at DESC, s.id DESC
    """))
    items = []
    for r in res.fetchall():
        total = r[5] or 0
        mapped = r[6] or 0
        items.append({
            "id": r[0], "name": r[1], "short_name": r[2], "inn": r[3],
            "created_at": r[4].isoformat() if r[4] else None,
            "products_total": total,
            "mapped": mapped,
            "auto": r[7] or 0,
            "manual": r[8] or 0,
            "unmapped": total - mapped,
        })
    return {"items": items}


@router.get("")
async def list_products(
    supplier_id: int = None,
    status: str = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """
    Список товаров с текущим маппингом и ценой поставщика.

    Фильтры:
      supplier_id — только товары этого поставщика;
      status — 'auto' | 'manual' | 'rejected' | 'unmapped'.
    """
    where = []
    params = {"limit": limit, "offset": offset}
    join_supplier = ""
    if supplier_id is not None:
        join_supplier = (
            "JOIN supplier_products sp ON sp.product_id = p.id "
            "AND sp.supplier_id = :supplier_id"
        )
        params["supplier_id"] = supplier_id
    else:
        join_supplier = "LEFT JOIN supplier_products sp ON sp.product_id = p.id"

    if status == "auto":
        where.append("m.id IS NOT NULL AND NOT m.is_manual AND NOT m.rejected")
    elif status == "manual":
        where.append("m.id IS NOT NULL AND m.is_manual AND NOT m.rejected")
    elif status == "rejected":
        where.append("m.id IS NOT NULL AND m.rejected")
    elif status == "unmapped":
        where.append("m.id IS NULL")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    res = await db.execute(text(f"""
        SELECT DISTINCT ON (p.id)
            p.id, p.name, p.sku, p.description, p.manufacturer, p.unit,
            sp.cost_price, sp.retail_price,
            m.id AS mapping_id, m.standard_id, m.is_manual, m.rejected,
            m.match_score, m.match_reason,
            s.item_name, s.full_code, s.subsection_name
        FROM products p
        {join_supplier}
        LEFT JOIN product_standard_mapping m ON m.product_id = p.id
        LEFT JOIN industry_standards s ON s.id = m.standard_id
        {where_sql}
        ORDER BY p.id, (m.rejected IS TRUE), m.id DESC
        LIMIT :limit OFFSET :offset
    """), params)

    items = []
    for r in res.fetchall():
        mapping_id = r[8]
        rejected = r[11]
        is_manual = r[10]
        if mapping_id is None:
            mstatus = "unmapped"
        elif rejected:
            mstatus = "rejected"
        elif is_manual:
            mstatus = "manual"
        else:
            mstatus = "auto"
        items.append({
            "id": r[0], "name": r[1], "sku": r[2], "description": r[3],
            "manufacturer": r[4], "unit": r[5],
            "cost_price": float(r[6]) if r[6] is not None else None,
            "retail_price": float(r[7]) if r[7] is not None else None,
            "mapping_id": mapping_id,
            "standard_id": r[9],
            "status": mstatus,
            "match_score": r[12],
            "match_reason": r[13],
            "standard_name": r[14],
            "full_code": r[15],
            "subsection_name": r[16],
        })
    return {"items": items}