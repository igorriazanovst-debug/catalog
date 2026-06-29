"""Миграция: хранение исходного файла сметы и привязки позиций к строкам файла.

Идемпотентно. Нужно для:
  * аннотированного экспорта (дописываем наши колонки в ОРИГИНАЛЬНЫЙ файл сметы);
  * отображения исходного наименования/описания позиции и пер-строчной
    классификации.

estimates:      source_filename, source_file_b64 (xlsx в base64), sheet_name, header_row
estimate_items: source_description (характеристики строки), source_row (№ строки в файле)

Запуск (из backend, в venv):
    python scripts/migrate_estimates_source.py --db-url "postgresql+asyncpg://...:5433/catalog_db"
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_DB_URL = os.getenv(
    "database_url",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
)

DDL = [
    "ALTER TABLE estimates ADD COLUMN IF NOT EXISTS source_filename TEXT",
    "ALTER TABLE estimates ADD COLUMN IF NOT EXISTS source_file_b64 TEXT",
    "ALTER TABLE estimates ADD COLUMN IF NOT EXISTS sheet_name TEXT",
    "ALTER TABLE estimates ADD COLUMN IF NOT EXISTS header_row INTEGER",
    "ALTER TABLE estimate_items ADD COLUMN IF NOT EXISTS source_description TEXT",
    "ALTER TABLE estimate_items ADD COLUMN IF NOT EXISTS source_row INTEGER",
]


async def main(db_url: str):
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        for stmt in DDL:
            await conn.execute(text(stmt))
            print(f"[OK] {stmt}")
    await engine.dispose()
    print("Миграция estimates (source) завершена.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Миграция: исходный файл сметы")
    ap.add_argument("--db-url", default=DEFAULT_DB_URL)
    asyncio.run(main(ap.parse_args().db_url))
