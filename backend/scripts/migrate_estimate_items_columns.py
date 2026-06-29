"""Миграция: добавить в estimate_items колонки для входящих смет.

Идемпотентно (ADD COLUMN IF NOT EXISTS). Нужно для записи результата подбора:
  * source_name — исходное наименование строки/вложения сметы (чтобы
    несопоставленные позиции не теряли текст потребности);
  * group_name  — наименование строки-набора, если позиция получена разложением
    набора на вложения (NULL — обычная позиция);
  * unit        — единица измерения из сметы;
  * match_method/match_reason — как сопоставилось (ktru/okpd2/rule/text+llm/text).

Запуск (из backend, в venv):
    python scripts/migrate_estimate_items_columns.py --db-url "postgresql+asyncpg://...:5433/catalog_db"
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_DB_URL = os.getenv(
    "database_url",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
)

DDL = [
    "ALTER TABLE estimate_items ADD COLUMN IF NOT EXISTS source_name TEXT",
    "ALTER TABLE estimate_items ADD COLUMN IF NOT EXISTS group_name TEXT",
    "ALTER TABLE estimate_items ADD COLUMN IF NOT EXISTS unit VARCHAR(50)",
    "ALTER TABLE estimate_items ADD COLUMN IF NOT EXISTS match_method VARCHAR(20)",
    "ALTER TABLE estimate_items ADD COLUMN IF NOT EXISTS match_reason TEXT",
]


async def main(db_url: str):
    from sqlalchemy import text
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        for stmt in DDL:
            await conn.execute(text(stmt))
            print(f"[OK] {stmt}")
    await engine.dispose()
    print("Миграция estimate_items завершена.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Миграция колонок estimate_items")
    ap.add_argument("--db-url", default=DEFAULT_DB_URL)
    asyncio.run(main(ap.parse_args().db_url))
