"""
Выгрузка справочника позиций Приказа 838 (industry_standards) в CSV.

Нужен как справочник при заполнении correct_std_id в листе проверки: открываешь
в Excel, ищешь позицию по слову, берёшь её id.

Формат: id;section_code;section_name;subsection_code;subsection_name;
        equipment_type;item_name   (UTF-8 with BOM, разделитель ';')

Запуск (из backend, в venv):
    python scripts/export_standards.py --db-url "postgresql+asyncpg://...:5433/catalog_db"
"""

import argparse
import asyncio
import csv
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DEFAULT_DB_URL = os.getenv(
    "database_url",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = REPO_ROOT / "logs" / "standards_838.csv"

COLUMNS = ["id", "section_code", "section_name", "subsection_code",
           "subsection_name", "equipment_type", "item_name"]


async def main(args):
    engine = create_async_engine(args.db_url, echo=False)
    async with engine.connect() as conn:
        res = await conn.execute(text(
            "SELECT id, section_code, section_name, subsection_code, "
            "subsection_name, equipment_type, item_name "
            "FROM industry_standards ORDER BY id"
        ))
        rows = res.fetchall()
    await engine.dispose()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])

    print(f"Справочник: {out_path}")
    print(f"Позиций: {len(rows)}")


def parse_args():
    p = argparse.ArgumentParser(description="Выгрузка справочника Приказа 838 в CSV")
    p.add_argument("--db-url", default=DEFAULT_DB_URL)
    p.add_argument("--out", default=str(DEFAULT_OUT))
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
