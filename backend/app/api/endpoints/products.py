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