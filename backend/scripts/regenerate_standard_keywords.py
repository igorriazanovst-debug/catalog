import asyncio
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
import pymorphy2
import re

DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db"

async def regenerate_keywords():
    """Перегенерирует keywords для industry_standards с лемматизацией"""
    print("Инициализация pymorphy2...")
    morph = pymorphy2.MorphAnalyzer()
    
    engine = create_async_engine(DATABASE_URL, echo=False)
    
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT id, item_name FROM industry_standards"))
        records = result.fetchall()
        
        print(f"Найдено {len(records)} записей для перегенерации keywords")
        
        batch_size = 100
        total_processed = 0
        
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            
            for record_id, item_name in batch:
                # Извлекаем слова
                words = re.findall(r'\b[а-яА-Яa-zA-ZёЁ]+\b', item_name.lower())
                
                # Лемматизируем
                keywords = set()
                for word in words:
                    if len(word) >= 3:
                        parsed = morph.parse(word)
                        if parsed:
                            normal_form = parsed[0].normal_form
                            if len(normal_form) >= 3:
                                keywords.add(normal_form)
                
                # Передаем как Python list, SQLAlchemy сам сконвертирует в PostgreSQL array
                keywords_list = list(keywords)
                
                await conn.execute(
                    text("UPDATE industry_standards SET keywords = :keywords WHERE id = :id"),
                    {"keywords": keywords_list, "id": record_id}
                )
            
            total_processed += len(batch)
            print(f"Обработано {total_processed} из {len(records)} записей")
        
        print(f"Перегенерация keywords завершена")

if __name__ == "__main__":
    asyncio.run(regenerate_keywords())