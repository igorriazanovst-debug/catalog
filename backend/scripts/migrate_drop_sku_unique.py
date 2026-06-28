"""
Миграция: снять ГЛОБАЛЬНУЮ уникальность с products.sku.

Зачем
-----
Изначально products.sku был UNIQUE на всю таблицу, поэтому при импорте прайса
нового поставщика товары с уже существующим артикулом «прилипали» к чужому
товару (ветка update), а не создавались как отдельное предложение. Теперь товары
ведутся per-supplier (одинаковый артикул у разных поставщиков = разные товары),
и глобальный UNIQUE мешает вставке. Уникальность предложения по-прежнему держит
supplier_products UNIQUE(supplier_id, product_id).

Что делает (идемпотентно)
-------------------------
Находит и удаляет все UNIQUE-ограничения на таблице products (это и есть
products_sku_key). Первичный ключ (contype='p') не трогается. Обычный
неуникальный индекс idx_products_sku остаётся для быстрых выборок.

Запуск (из каталога backend, в venv проекта):
    python scripts/migrate_drop_sku_unique.py \
        --db-url "postgresql+asyncpg://postgres:...@localhost:5433/catalog_db"
"""

import argparse
import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DEFAULT_DB_URL = os.getenv(
    "database_url",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
)


async def main(args):
    engine = create_async_engine(args.db_url, echo=False)
    print(f"Подключение: {args.db_url.split('@')[-1]}")
    async with engine.begin() as conn:
        rows = (await conn.execute(text("""
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            WHERE rel.relname = 'products' AND con.contype = 'u'
        """))).fetchall()

        if not rows:
            print("UNIQUE-ограничений на products нет — миграция уже применена.")
        for (conname,) in rows:
            print(f"DROP CONSTRAINT {conname} …")
            # имя ограничения из системного каталога, не пользовательский ввод
            await conn.execute(
                text(f'ALTER TABLE products DROP CONSTRAINT IF EXISTS "{conname}"')
            )
        print("Готово.")
    await engine.dispose()


def parse_args():
    p = argparse.ArgumentParser(description="Снять UNIQUE с products.sku")
    p.add_argument("--db-url", default=DEFAULT_DB_URL,
                   help="URL БД (async). По умолчанию env database_url или localhost:5432.")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
