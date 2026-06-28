"""
Полный сброс каталога товаров «начисто».

Очищает: products, supplier_products, product_standard_mapping, suppliers
(+ каскадом estimate_items, т.к. он ссылается на products/suppliers).
ID-счётчики сбрасываются на 1 (RESTART IDENTITY).

СОХРАНЯЕТСЯ: industry_standards (справочник Приказа 838 с эмбеддингами —
дорого пересобирать) и system_settings.

Безопасность: без флага --yes скрипт только показывает, что будет удалено,
и ничего не меняет.

Запуск (из каталога backend, в venv проекта):
    python scripts/reset_catalog.py --db-url "$DBURL"          # показать (dry-run)
    python scripts/reset_catalog.py --db-url "$DBURL" --yes    # выполнить
"""

import argparse
import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DEFAULT_DB_URL = os.getenv(
    "database_url",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
)

# Таблицы под очистку (CASCADE сам подхватит estimate_items).
WIPE = ["product_standard_mapping", "supplier_products", "products", "suppliers"]
COUNT = WIPE + ["estimate_items", "industry_standards"]


async def counts(conn):
    out = {}
    for t in COUNT:
        try:
            out[t] = (await conn.execute(text(f"SELECT COUNT(*) FROM {t}"))).scalar()
        except Exception:
            out[t] = "—"
    return out


async def main(args):
    engine = create_async_engine(args.db_url, echo=False)
    print(f"Подключение: {args.db_url.split('@')[-1]}\n")
    async with engine.begin() as conn:
        before = await counts(conn)
        print("Сейчас в БД:")
        for t in COUNT:
            print(f"  {t:28} {before[t]}")

        if not args.yes:
            print("\nDRY-RUN: ничего не удалено. Для выполнения добавьте --yes.")
            print("Будут очищены (RESTART IDENTITY CASCADE):", ", ".join(WIPE))
            print("Сохранятся: industry_standards (838), system_settings.")
            await engine.dispose()
            return

        await conn.execute(text(
            f"TRUNCATE TABLE {', '.join(WIPE)} RESTART IDENTITY CASCADE"
        ))
        after = await counts(conn)
        print("\nПосле очистки:")
        for t in COUNT:
            print(f"  {t:28} {after[t]}")
        print("\nГотово. Справочник 838 сохранён. Загружайте прайсы заново.")
    await engine.dispose()


def parse_args():
    p = argparse.ArgumentParser(description="Полный сброс каталога товаров (начисто)")
    p.add_argument("--yes", action="store_true",
                   help="Подтвердить удаление (без флага — только показать)")
    p.add_argument("--db-url", default=DEFAULT_DB_URL,
                   help="URL БД (async). По умолчанию env database_url или localhost:5432.")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
