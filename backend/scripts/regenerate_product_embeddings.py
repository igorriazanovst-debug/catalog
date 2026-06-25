import asyncio
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from sentence_transformers import SentenceTransformer

DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db"
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"

async def regenerate_embeddings():
    """Перегенерирует эмбеддинги товаров с учетом description"""
    print(f"Загрузка модели {MODEL_NAME}...")
    model = SentenceTransformer(MODEL_NAME)
    print("Модель загружена")
    
    engine = create_async_engine(DATABASE_URL, echo=False)
    
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT id, name, description FROM products"))
        records = result.fetchall()
        
        print(f"Найдено {len(records)} товаров для перегенерации эмбеддингов")
        
        batch_size = 50
        total_processed = 0
        
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            
            # Конкатенируем name + description
            texts_for_embedding = []
            ids = []
            for record_id, name, description in batch:
                # Переименовали переменную, чтобы не конфликтовала с импортом
                combined_text = name
                if description:
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
    asyncio.run(regenerate_embeddings())