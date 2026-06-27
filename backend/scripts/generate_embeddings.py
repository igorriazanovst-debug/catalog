import argparse
import asyncio
import os
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from sentence_transformers import SentenceTransformer

# Подключение к БД: --db-url или env database_url, дефолт 5432 для совместимости
DEFAULT_DB_URL = os.getenv(
    "database_url",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
)

# Модель для эмбеддингов
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"

async def generate_embeddings(database_url: str):
    """Генерирует векторные эмбеддинги для всех позиций в industry_standards"""
    print(f"Подключение: {database_url.split('@')[-1]}")
    print(f"Загрузка модели {MODEL_NAME}...")
    model = SentenceTransformer(MODEL_NAME)
    print("Модель загружена")

    engine = create_async_engine(database_url, echo=False)
    
    async with engine.begin() as conn:
        # Получаем все записи без эмбеддингов
        result = await conn.execute(text(
            "SELECT id, item_name FROM industry_standards WHERE embedding IS NULL"
        ))
        records = result.fetchall()
        
        print(f"Найдено {len(records)} записей без эмбеддингов")
        
        if not records:
            print("Все записи уже имеют эмбеддинги")
            return
        
        # Генерируем эмбеддинги батчами
        batch_size = 100
        total_processed = 0
        
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            
            # Извлекаем названия
            texts = [record[1] for record in batch]
            ids = [record[0] for record in batch]
            
            # Генерируем эмбеддинги
            embeddings = model.encode(texts, show_progress_bar=False)
            
            # Обновляем каждую запись отдельно
            for record_id, embedding in zip(ids, embeddings):
                # Преобразуем numpy array в строку формата "[0.1, 0.2, ...]"
                emb_str = "[" + ",".join(str(x) for x in embedding.tolist()) + "]"
                
                # Используем CAST вместо :: для совместимости с asyncpg
                await conn.execute(
                    text("UPDATE industry_standards SET embedding = CAST(:embedding AS vector) WHERE id = :id"),
                    {"embedding": emb_str, "id": record_id}
                )
            
            total_processed += len(batch)
            print(f"Обработано {total_processed} из {len(records)} записей")
        
        print(f"Генерация эмбеддингов завершена. Всего обработано {total_processed} записей")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Генерация эмбеддингов для industry_standards"
    )
    parser.add_argument(
        "--db-url", default=DEFAULT_DB_URL,
        help="URL БД (async). По умолчанию env database_url или localhost:5432.",
    )
    args = parser.parse_args()
    asyncio.run(generate_embeddings(args.db_url))