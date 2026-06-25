import json
import asyncio
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
import re

# Путь к JSON файлу
JSON_PATH = Path(__file__).parent.parent.parent / "data" / "output" / "order_838_tree.json"

# Подключение к БД
DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db"

def extract_keywords(item_name: str) -> list[str]:
    """Извлекает ключевые слова из названия позиции"""
    # Убираем специальные символы и разбиваем на слова
    words = re.findall(r'\b[а-яА-Яa-zA-ZёЁ]+\b', item_name.lower())
    # Убираем слишком короткие слова (менее 3 символов)
    keywords = [w for w in words if len(w) >= 3]
    # Убираем дубликаты
    return list(set(keywords))

async def import_standards():
    """Импортирует данные из JSON в таблицу industry_standards"""
    print(f"Чтение JSON из {JSON_PATH}")
    
    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    industry_code = data['metadata']['order']
    print(f"Код отрасли: {industry_code}")
    
    engine = create_async_engine(DATABASE_URL, echo=False)
    
    async with engine.begin() as conn:
        # Очищаем таблицу перед импортом
        await conn.execute(text("DELETE FROM industry_standards"))
        print("Таблица очищена")
        
        records = []
        
        for section in data['sections']:
            section_code = section['code']
            section_name = section['name']
            
            for subsection in section.get('subsections', []):
                subsection_code = subsection['code']
                subsection_name = subsection['name']
                
                for equipment_type_data in subsection.get('equipment_types', []):
                    equipment_type = equipment_type_data['type']
                    
                    for item in equipment_type_data.get('items', []):
                        item_name = item['name']
                        keywords = extract_keywords(item_name)
                        
                        records.append({
                            'industry_code': industry_code,
                            'section_code': section_code,
                            'subsection_code': subsection_code,
                            'section_name': section_name,
                            'subsection_name': subsection_name,
                            'item_name': item_name,
                            'equipment_type': equipment_type,
                            'keywords': keywords
                        })
        
        print(f"Найдено {len(records)} позиций для импорта")
        
        # Вставляем данные батчами по 100 записей
        batch_size = 100
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            
            # Формируем SQL запрос
            values = []
            params = {}
            for idx, record in enumerate(batch):
                values.append(f"(:industry_code_{idx}, :section_code_{idx}, :subsection_code_{idx}, "
                            f":section_name_{idx}, :subsection_name_{idx}, :item_name_{idx}, "
                            f":equipment_type_{idx}, :keywords_{idx})")
                
                params[f'industry_code_{idx}'] = record['industry_code']
                params[f'section_code_{idx}'] = record['section_code']
                params[f'subsection_code_{idx}'] = record['subsection_code']
                params[f'section_name_{idx}'] = record['section_name']
                params[f'subsection_name_{idx}'] = record['subsection_name']
                params[f'item_name_{idx}'] = record['item_name']
                params[f'equipment_type_{idx}'] = record['equipment_type']
                params[f'keywords_{idx}'] = record['keywords']
            
            query = f"""
                INSERT INTO industry_standards 
                (industry_code, section_code, subsection_code, section_name, subsection_name, 
                 item_name, equipment_type, keywords)
                VALUES {', '.join(values)}
            """
            
            await conn.execute(text(query), params)
            
            if (i + batch_size) % 500 == 0:
                print(f"Импортировано {i + batch_size} записей...")
        
        print(f"Импорт завершен. Всего импортировано {len(records)} записей")

if __name__ == "__main__":
    asyncio.run(import_standards())