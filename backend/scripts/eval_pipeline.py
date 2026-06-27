"""
Сквозная оценка нового пайплайна маппинга на размеченной выборке (с LLM).

Прогоняет БОЕВОЙ MappingService (гибридный ретрив вектор ∪ keyword) + LLM-судью
на товарах с известным эталоном (correct_std_id) и считает реальную точность.

Метрики:
  - accuracy   : доля товаров, где выбор LLM == эталон;
  - recall     : доля, где эталон попал в гибридный пул (потолок);
  - precision@pool: точность LLM среди тех, где эталон БЫЛ в пуле;
  - null-rate  : доля «нет подходящего»;
  - точность по уровню уверенности LLM.

Запуск (из backend, в venv):
    python scripts/eval_pipeline.py \
        --csv ../logs/review_20260627_143039_labeled.csv \
        --db-url "postgresql+asyncpg://...:5433/catalog_db"
"""

import argparse
import asyncio
import csv
import os
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.mapping_service import MappingService  # noqa: E402
from app.services.llm_mapping_service import get_llm_mapping  # noqa: E402

DEFAULT_DB_URL = os.getenv(
    "database_url",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
)


async def main(args):
    rows = list(csv.DictReader(open(args.csv, encoding="utf-8-sig"), delimiter=";"))
    truth = {}
    for r in rows:
        pid = (r.get("product_id") or "").strip()
        cid = (r.get("correct_std_id") or "").strip()
        if pid and cid and cid.lower() != "none":
            try:
                truth[int(pid)] = int(cid)
            except ValueError:
                pass
    print(f"Размеченных товаров: {len(truth)}")
    print("Загрузка модели/индекса (первый прогон — минута)...")

    engine = create_async_engine(args.db_url, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    total = 0
    correct = 0
    name_correct = 0   # совпало по НАЗВАНИЮ позиции (а не только по id)
    recall = 0
    null_cnt = 0
    present_total = 0   # эталон был в пуле
    present_correct = 0
    conf_bands = {"[0.9,1.0]": [], "[0.7,0.9)": [], "[<0.7)": []}
    dump = []          # построчная выгрузка для анализа ошибок

    async with Session() as db:
        # справочник id -> название (для name-совпадения и выгрузки)
        sres = await db.execute(__import__("sqlalchemy").text(
            "SELECT id, item_name FROM industry_standards"))
        std_name = {r[0]: r[1] for r in sres.fetchall()}

        service = MappingService(db)
        n_truth = len(truth)
        for idx, (pid, gold) in enumerate(truth.items(), 1):
            print(f"  [{idx}/{n_truth}] товар {pid} ...", file=sys.stderr, flush=True)
            pr = await db.execute(
                __import__("sqlalchemy").text(
                    "SELECT name, description, properties FROM products WHERE id = :id"
                ),
                {"id": pid},
            )
            prow = pr.fetchone()
            if not prow:
                continue
            name, description, properties = prow

            pool = await service.map_product_to_standards(pid, top_k=args.top_k)
            if not pool:
                continue
            total += 1
            pool_ids = [c["standard_id"] for c in pool]
            in_pool = gold in pool_ids
            if in_pool:
                recall += 1

            llm = await get_llm_mapping(
                {"name": name, "description": description or "", "properties": properties or {}},
                [{"id": c["standard_id"], "standard_name": c.get("llm_label", c["standard_name"])}
                 for c in pool],
            )
            llm_id = llm.get("standard_id")
            conf = llm.get("confidence", 0.0) or 0.0

            if llm_id is None:
                null_cnt += 1
            ok = (llm_id == gold)
            correct += ok
            # совпадение по названию (одинаковый item_name = та же позиция в др. кабинете)
            name_ok = (llm_id is not None
                       and std_name.get(llm_id, "\0") == std_name.get(gold, "\1"))
            name_correct += name_ok
            if in_pool:
                present_total += 1
                present_correct += ok
                if llm_id is not None:
                    b = "[0.9,1.0]" if conf >= 0.9 else "[0.7,0.9)" if conf >= 0.7 else "[<0.7)"
                    conf_bands[b].append(ok)

            dump.append({
                "product_id": pid,
                "product_name": name,
                "gold_id": gold,
                "gold_name": std_name.get(gold, ""),
                "llm_id": llm_id if llm_id is not None else "null",
                "llm_name": std_name.get(llm_id, "") if llm_id is not None else "",
                "id_ok": int(ok),
                "name_ok": int(name_ok),
                "in_pool": int(in_pool),
                "confidence": f"{conf:.2f}",
            })

            if args.sleep > 0:
                await asyncio.sleep(args.sleep)

    await engine.dispose()

    # выгрузка по строкам
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dump_path = Path(args.csv).resolve().parent / f"eval_dump_{ts}.csv"
    with open(dump_path, "w", encoding="utf-8-sig", newline="") as f:
        import csv as _csv
        w = _csv.DictWriter(f, fieldnames=list(dump[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(dump)

    print("")
    print("=" * 60)
    print("СКВОЗНАЯ ОЦЕНКА НОВОГО ПАЙПЛАЙНА")
    print("=" * 60)
    if total:
        print(f"Товаров обработано:               {total}")
        print(f"Accuracy (LLM == эталон по id):    {correct}/{total} = {correct/total:.0%}")
        print(f"Accuracy по НАЗВАНИЮ позиции:      {name_correct}/{total} = {name_correct/total:.0%}")
        print(f"Recall (эталон в пуле):           {recall}/{total} = {recall/total:.0%}")
        if present_total:
            print(f"Precision@pool (когда эталон в пуле): "
                  f"{present_correct}/{present_total} = {present_correct/present_total:.0%}")
        print(f"LLM сказал null:                  {null_cnt}")
        print(f"Выгрузка по строкам:              {dump_path}")
        print("")
        print("Точность по уверенности LLM (где эталон был в пуле):")
        for b, v in conf_bands.items():
            if v:
                print(f"  conf {b}: {sum(v)}/{len(v)} = {sum(v)/len(v):.0%}")


def parse_args():
    p = argparse.ArgumentParser(description="Сквозная оценка пайплайна на размеченной выборке")
    p.add_argument("--csv", required=True)
    p.add_argument("--db-url", default=DEFAULT_DB_URL)
    p.add_argument("--top-k", type=int, default=15)
    p.add_argument("--sleep", type=float, default=0.2)
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
