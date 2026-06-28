"""
Импорт товаров из CSV в БД (с генерацией эмбеддингов).

Зачем отдельный скрипт
----------------------
app/core/database.py и часть scripts/* захардкодили порт 5432. На сервере БД
может быть на другом порту (например, 5433). Этот скрипт принимает --db-url
явно и переиспользует боевую логику app.services.product_service.ProductService
(тот же парсинг цен, та же генерация эмбеддингов), не завися от захардкоженного
подключения.

Что делает:
  1. Создаёт/находит поставщика.
  2. Импортирует товары из CSV (разделитель ';', UTF-8 по умолчанию).
  3. На каждый новый товар генерирует эмбеддинг (как в ProductService —
     по полю name). description при этом сохраняется в БД.

ВНИМАНИЕ: ProductService генерирует эмбеддинг ТОЛЬКО по name. Если для качества
нужен эмбеддинг по name+description — после импорта запустите
scripts/regenerate_product_embeddings.py (там используется name+description),
не забыв указать правильный db-url.

Запуск (из каталога backend, в venv проекта):
    python scripts/import_products.py \
        --csv "../data/input/шаблон товары.csv" \
        --supplier-name "Тестовый поставщик" \
        --db-url "postgresql+asyncpg://postgres:postgres@localhost:5433/catalog_db"

Колонки CSV (обязательные): Артикул, Наименование, Себестоимость, РРЦ.
Опциональные: Описание, Ед. изм., НДС включен, Производитель.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Делаем пакет app импортируемым (скрипт лежит в backend/scripts/)
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.product_service import ProductService  # noqa: E402

DEFAULT_DB_URL = os.getenv(
    "database_url",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
)

REQUIRED_COLUMNS = ["Артикул", "Наименование", "Себестоимость", "РРЦ"]


async def main(args):
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"Файл не найден: {csv_path}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_csv(csv_path, sep=args.sep, encoding=args.encoding)
    df.columns = [c.strip() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        print(f"В CSV отсутствуют обязательные колонки: {', '.join(missing)}", file=sys.stderr)
        print(f"Найденные колонки: {list(df.columns)}", file=sys.stderr)
        sys.exit(1)

    print(f"Прочитано строк: {len(df)}")
    print(f"Колонки: {list(df.columns)}")
    print(f"Подключение: {args.db_url.split('@')[-1]}")
    print("Загрузка модели эмбеддингов (может занять минуту)...")

    engine = create_async_engine(args.db_url, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        svc = ProductService(db)
        supplier_id = await svc.get_or_create_supplier(
            name=args.supplier_name,
            short_name=args.supplier_short_name,
            inn=args.supplier_inn,
        )
        print(f"Поставщик id={supplier_id} ({args.supplier_name})")
        result = await svc.import_products_from_csv(df, supplier_id)

    await engine.dispose()

    print("")
    print("=" * 50)
    print(f"Импортировано новых:  {result['imported']}")
    print(f"Обновлено:            {result['updated']}")
    print(f"Внутр. артикул выдан: {result.get('auto_sku', 0)}")
    print(f"Ошибок:               {len(result['errors'])}")
    for err in result["errors"]:
        print(f"  - {err}")
    print("=" * 50)


def parse_args():
    p = argparse.ArgumentParser(description="Импорт товаров из CSV с генерацией эмбеддингов")
    p.add_argument("--csv", required=True, help="Путь к CSV-файлу с товарами")
    p.add_argument("--supplier-name", required=True, help="Название поставщика")
    p.add_argument("--supplier-short-name", default=None)
    p.add_argument("--supplier-inn", default=None)
    p.add_argument("--db-url", default=DEFAULT_DB_URL,
                   help="URL БД (async). По умолчанию env database_url или localhost:5432.")
    p.add_argument("--sep", default=";", help="Разделитель CSV (по умолчанию ';')")
    p.add_argument("--encoding", default="utf-8", help="Кодировка CSV (по умолчанию utf-8)")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
