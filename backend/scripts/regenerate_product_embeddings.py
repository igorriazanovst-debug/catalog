import argparse
import asyncio
import os
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from sentence_transformers import SentenceTransformer

# URL БД можно задать аргументом --db-url или переменной окружения database_url.
# Дефолт оставлен на 5432 для обратной совместимости.
DEFAULT_DB_URL = os.getenv(
    "database_url",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
)
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"

async def regenerate_embeddings(database_url: str, mode: str = "name"):
    """Перегенерирует эмбеддинги товаров.

    mode='name'      — эмбеддинг только по названию (даёт более чистый матч на
                       названия позиций стандарта; на наших данных лучше).
    mode='name_desc' — по name + description (длинные описания размывают сигнал).
    """
    print(f"Подключение: {database_url.split('@')[-1]}")
    print(f"Режим эмбеддинга: {mode}")
    print(f"Загрузка модели {MODEL_NAME}...")
    model = SentenceTransformer(MODEL_NAME)
    print("Модель загружена")

    engine = create_async_engine(database_url, echo=False)
    
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT id, name, description FROM products"))
        records = result.fetchall()
        
        print(f"Найдено {len(records)} товаров для перегенерации эмбеддингов")
        
        batch_size = 50
        total_processed = 0
        
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            
            texts_for_embedding = []
            ids = []
            for record_id, name, description in batch:
                combined_text = name
                if mode == "name_desc" and description:
                    combined_text += " " + description
                texts_for_embedding.append(combined_text)
                ids.append(record_id)
            
            # Генерируем эмбеддинги
            embeddings = model.encode(texts_for_embedding, show_progress_bar=False)
            
            # Обновляем БД
            for record_id, embedding in zip(ids, embeddings):
                emb_str = "[" + ",".join(str(x) for x in embedding.tolist()) + "]"
                
                await conn.execute(
                    text("UPDATE products SET embedding = CAST(:embedding AS vector) WHERE id = :id"),
                    {"embedding": emb_str, "id": record_id}
                )
            
            total_processed += len(batch)
            print(f"Обработано {total_processed} из {len(records)} товаров")
        
        print(f"Перегенерация эмбеддингов завершена")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Перегенерация эмбеддингов товаров по name + description"
    )
    parser.add_argument(
        "--db-url", default=DEFAULT_DB_URL,
        help="URL БД (async). По умолчанию env database_url или localhost:5432.",
    )
    parser.add_argument(
        "--mode", choices=["name", "name_desc"], default="name",
        help="name — только название (по умолчанию, чище матч); "
             "name_desc — name + description.",
    )
    args = parser.parse_args()
    asyncio.run(regenerate_embeddings(args.db_url, args.mode))