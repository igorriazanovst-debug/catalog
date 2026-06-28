"""
Сброс товаров поставщика — для повторного импорта «с чистого листа».

Когда нужно
-----------
Если поставщик был импортирован СТАРОЙ логикой (когда товары с уже существующим
артикулом «прилипали» к чужим товарам), простой повторный импорт это не починит:
поставщик уже привязан к общим товарам. Этот скрипт отвязывает товары поставщика
и удаляет осиротевшие (на которые не осталось ни одного поставщика) — вместе с их
маппингами (FK ON DELETE CASCADE). Товары, которые ещё нужны другим поставщикам,
сохраняются. После сброса заново загрузите прайс этого поставщика.

Запуск (из каталога backend, в venv проекта):
    python scripts/reset_supplier.py --supplier-id 3 --db-url "postgresql+asyncpg://...:5433/catalog_db"
    # сначала можно посмотреть, что будет удалено:
    python scripts/reset_supplier.py --supplier-id 3 --dry-run --db-url "..."
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
        sup = (await conn.execute(
            text("SELECT id, name FROM suppliers WHERE id = :id"),
            {"id": args.supplier_id},
        )).fetchone()
        if not sup:
            print(f"Поставщик id={args.supplier_id} не найден.", file=sys.stderr)
            sys.exit(1)
        print(f"Поставщик: id={sup[0]} «{sup[1]}»")

        links = (await conn.execute(
            text("SELECT COUNT(*) FROM supplier_products WHERE supplier_id = :id"),
            {"id": args.supplier_id},
        )).scalar()
        # Товары, которые осиротеют после отвязки (нет других поставщиков).
        orphans = (await conn.execute(text("""
            SELECT COUNT(*) FROM products p
            WHERE EXISTS (SELECT 1 FROM supplier_products sp
                          WHERE sp.product_id = p.id AND sp.supplier_id = :id)
              AND NOT EXISTS (SELECT 1 FROM supplier_products sp
                              WHERE sp.product_id = p.id AND sp.supplier_id <> :id)
        """), {"id": args.supplier_id})).scalar()
        print(f"Связей supplier_products: {links}")
        print(f"Товаров станут осиротевшими и будут удалены (с маппингами): {orphans}")

        if args.dry_run:
            print("DRY-RUN: ничего не изменено.")
            await engine.dispose()
            return

        await conn.execute(
            text("DELETE FROM supplier_products WHERE supplier_id = :id"),
            {"id": args.supplier_id},
        )
        deleted = (await conn.execute(text("""
            DELETE FROM products p
            WHERE NOT EXISTS (SELECT 1 FROM supplier_products sp
                              WHERE sp.product_id = p.id)
            RETURNING p.id
        """))).fetchall()
        print(f"Отвязано связей: {links}. Удалено осиротевших товаров: {len(deleted)}.")
        print("Готово. Теперь заново загрузите прайс этого поставщика.")
    await engine.dispose()


def parse_args():
    p = argparse.ArgumentParser(description="Сброс товаров поставщика для повторного импорта")
    p.add_argument("--supplier-id", type=int, required=True, help="ID поставщика")
    p.add_argument("--dry-run", action="store_true", help="Только показать, ничего не удалять")
    p.add_argument("--db-url", default=DEFAULT_DB_URL,
                   help="URL БД (async). По умолчанию env database_url или localhost:5432.")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
