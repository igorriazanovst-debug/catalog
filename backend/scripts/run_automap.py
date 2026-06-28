"""
Полный авто-маппинг всех товаров (роутер → гибридный ретрив → LLM-судья).

Записывает результаты в product_standard_mapping (upsert) и печатает
распределение: сколько закрыто правилом, LLM, ушло на ручную, без совпадения.

Прогресс пишется через логгер уровня INFO (каждые 50 товаров).

ВНИМАНИЕ: использует полную модель yandexgpt из .env; на ~934 товарах
(минус ~60% роутером) это ~350-400 вызовов LLM — несколько десятков минут.

Запуск (из backend, в venv):
    python scripts/run_automap.py --db-url "postgresql+asyncpg://...:5433/catalog_db"
    python scripts/run_automap.py --db-url ... --threshold 0.7 --top-k 20
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.mapping_service import MappingService  # noqa: E402

DEFAULT_DB_URL = os.getenv(
    "database_url",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
)


async def main(args):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = create_async_engine(args.db_url, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        service = MappingService(db)
        result = await service.auto_map_all_products(
            llm_confidence_threshold=args.threshold, top_k=args.top_k
        )
    await engine.dispose()

    print("")
    print("=" * 50)
    print("ИТОГ АВТО-МАППИНГА")
    print("=" * 50)
    total = result["total_products"]
    print(f"Всего товаров:        {total}")
    print(f"Закрыто правилом:     {result['by_rule']}")
    print(f"Закрыто LLM:          {result['by_llm']}")
    print(f"Авто (записано):      {result['auto_mapped']}")
    print(f"На ручную проверку:   {result['needs_review']}")
    print(f"Без совпадения (null):{result['no_match']}")
    if result["errors"]:
        print(f"Ошибок:               {len(result['errors'])}")
        for e in result["errors"][:10]:
            print(f"  - {e}")


def parse_args():
    p = argparse.ArgumentParser(description="Полный авто-маппинг товаров")
    p.add_argument("--db-url", default=DEFAULT_DB_URL)
    p.add_argument("--threshold", type=float, default=0.7,
                   help="Порог уверенности LLM для авто (иначе на ручную)")
    p.add_argument("--top-k", type=int, default=20)
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
