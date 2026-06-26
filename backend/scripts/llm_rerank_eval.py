"""
Оценка LLM-переранжирования векторного шортлиста (read-only, БЕЗ записи в БД).

Для каждого товара:
  1. Берёт векторный шортлист (top-N кандидатов из industry_standards).
  2. Прогоняет YandexGPT поверх шортлиста (та же боевая функция
     app.services.llm_mapping_service.get_llm_mapping) — LLM выбирает один
     стандарт или говорит «подходящего нет» (null).
  3. Сравнивает выбор LLM с выбором чистого вектора (top-1).

Скрипт НИЧЕГО не пишет в product_standard_mapping. Его задача — измерить, что
даёт LLM на сложных случаях, прежде чем менять боевой mapping_service.

ВНИМАНИЕ: нужен рабочий YandexGPT — переменные YANDEX_GPT_API_KEY,
YANDEX_GPT_FOLDER_ID, YANDEX_GPT_MODEL_URI в backend/.env (их читает settings).
Если ключей нет — get_llm_mapping вернёт null, и это будет видно в логе.

Перед запуском желательно вернуть эмбеддинги на name-only:
    python scripts/regenerate_product_embeddings.py --mode name --db-url ...

Запуск (из backend, в venv):
    python scripts/llm_rerank_eval.py --db-url "postgresql+asyncpg://...:5433/catalog_db"
    python scripts/llm_rerank_eval.py --pool 8 --limit 10
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.llm_mapping_service import get_llm_mapping  # noqa: E402

DEFAULT_DB_URL = os.getenv(
    "database_url",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
)
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_OUT_DIR = REPO_ROOT / "logs"


class Tee:
    def __init__(self, fh):
        self.fh = fh

    def write(self, line: str = ""):
        print(line)
        self.fh.write(line + "\n")


async def vector_shortlist(conn, embedding, pool: int):
    q = text("""
        SELECT id, item_name, 1 - (embedding <=> CAST(:e AS vector)) AS vsim
        FROM industry_standards
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:e AS vector)
        LIMIT :k
    """)
    res = await conn.execute(q, {"e": embedding, "k": pool})
    return [(r[0], r[1], float(r[2])) for r in res.fetchall()]


async def run(args):
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = out_dir / f"llm_rerank_{ts}.log"
    json_path = out_dir / f"llm_rerank_{ts}.json"

    engine = create_async_engine(args.db_url, echo=False)
    report = {"generated_at": datetime.now().isoformat(),
              "params": {"pool": args.pool, "limit": args.limit,
                         "llm_threshold": args.llm_threshold},
              "items": []}

    with open(log_path, "w", encoding="utf-8") as fh:
        out = Tee(fh)
        out.write("LLM-ПЕРЕРАНЖИРОВАНИЕ ВЕКТОРНОГО ШОРТЛИСТА (read-only)")
        out.write(f"Время: {report['generated_at']}")
        out.write(f"Шортлист (вектор top-N): {args.pool}   "
                  f"Порог уверенности LLM: {args.llm_threshold}")
        out.write("")

        async with engine.connect() as conn:
            std_name = {}
            r = await conn.execute(text("SELECT id, item_name FROM industry_standards"))
            for sid, nm in r.fetchall():
                std_name[sid] = nm

            limit_clause = f"LIMIT {args.limit}" if args.limit else ""
            res = await conn.execute(text(
                "SELECT id, name, description, properties, embedding FROM products "
                f"WHERE embedding IS NOT NULL ORDER BY id {limit_clause}"
            ))
            products = res.fetchall()

            n = 0
            agree = 0           # LLM выбрал того же, что и вектор top-1
            llm_null = 0        # LLM сказал «подходящего нет»
            llm_confident = 0   # confidence >= порога
            llm_failed = 0      # LLM не ответил (нет ключей/ошибка)

            for pid, name, description, properties, embedding in products:
                shortlist = await vector_shortlist(conn, embedding, args.pool)
                if not shortlist:
                    continue
                n += 1
                vec_top1_id, vec_top1_name, vec_top1_sim = shortlist[0]

                product_data = {
                    "name": name,
                    "description": description or "",
                    "properties": properties or {},
                }
                candidates = [{"id": sid, "standard_name": nm} for sid, nm, _ in shortlist]

                llm = await get_llm_mapping(product_data, candidates)
                llm_id = llm.get("standard_id")
                llm_conf = llm.get("confidence", 0.0) or 0.0
                llm_reason = llm.get("reason", "")

                # Классификация результата.
                # «Не ответил» = техническая ошибка/нет ключей (по тексту reason из
                # get_llm_mapping), а не осознанный отказ модели.
                reason_str = str(llm_reason)
                fail_markers = ("Error", "not configured", "invalid JSON",
                                "empty alternatives", "Unexpected error")
                is_failure = llm_id is None and any(m in reason_str for m in fail_markers)
                if is_failure:
                    llm_failed += 1
                elif llm_id is None:
                    llm_null += 1
                if llm_id is not None and llm_conf >= args.llm_threshold:
                    llm_confident += 1
                if llm_id is not None and llm_id == vec_top1_id:
                    agree += 1

                out.write("")
                out.write(f"--- Товар {pid}: {name}")
                out.write(f"    Вектор top-1: std={vec_top1_id} ({vec_top1_sim:.3f}) "
                          f"| {vec_top1_name}")
                out.write(f"    Шортлист (вектор top-{args.pool}):")
                for sid, nm, sim in shortlist:
                    out.write(f"      std={sid:<5} {sim:.3f} | {nm}")
                if llm_id is not None:
                    same = " (== вектор top-1)" if llm_id == vec_top1_id else " (ОТЛИЧАЕТСЯ от вектора)"
                    out.write(f"    LLM выбрал: std={llm_id} | {std_name.get(llm_id, '?')}")
                    out.write(f"    LLM уверенность: {llm_conf:.2f}{same}")
                else:
                    out.write(f"    LLM выбрал: НЕТ подходящего (null)")
                out.write(f"    LLM обоснование: {llm_reason}")

                report["items"].append({
                    "product_id": pid, "name": name,
                    "vector_top1": {"id": vec_top1_id, "name": vec_top1_name,
                                    "vsim": round(vec_top1_sim, 4)},
                    "shortlist": [{"id": s, "name": nm, "vsim": round(v, 4)}
                                  for s, nm, v in shortlist],
                    "llm": {"standard_id": llm_id, "confidence": llm_conf,
                            "reason": llm_reason,
                            "name": std_name.get(llm_id) if llm_id else None},
                    "agree_with_vector": (llm_id == vec_top1_id),
                })

            out.write("")
            out.write("=" * 78)
            out.write("СВОДКА")
            out.write("=" * 78)
            out.write(f"  Товаров: {n}")
            if n:
                out.write(f"  LLM согласен с вектором top-1:        {agree} ({agree/n:.0%})")
                out.write(f"  LLM выбрал другой стандарт:           "
                          f"{n - agree - llm_null - llm_failed}")
                out.write(f"  LLM сказал «подходящего нет» (null):  {llm_null}")
                out.write(f"  LLM уверен (conf >= {args.llm_threshold}):           {llm_confident}")
                out.write(f"  LLM не ответил (нет ключей/ошибка):   {llm_failed}")
            if llm_failed == n and n:
                out.write("")
                out.write("  [!] LLM не ответил ни разу — проверьте YANDEX_GPT_* в backend/.env")
            out.write("")
            out.write(f"Лог:  {log_path}")
            out.write(f"JSON: {json_path}")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    await engine.dispose()
    return log_path


def parse_args():
    p = argparse.ArgumentParser(description="Оценка LLM-переранжирования (read-only)")
    p.add_argument("--db-url", default=DEFAULT_DB_URL)
    p.add_argument("--pool", type=int, default=8, help="Размер векторного шортлиста для LLM")
    p.add_argument("--limit", type=int, default=0, help="Ограничить число товаров (0=все)")
    p.add_argument("--llm-threshold", type=float, default=0.7,
                   help="Порог уверенности LLM для зачёта в авто (для статистики)")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    return p.parse_args()


if __name__ == "__main__":
    log = asyncio.run(run(parse_args()))
    print(f"\nГотово. Лог: {log}")
