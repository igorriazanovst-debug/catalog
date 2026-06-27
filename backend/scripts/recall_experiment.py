"""
Эксперимент по recall гибридного поиска (read-only, БЕЗ LLM).

Берёт размеченный лист (review_*.csv с заполненным correct_std_id) и на этих
товарах считает recall разных стратегий ПОИСКА кандидатов:
  - vector  : вектор top-K (как сейчас);
  - keyword : keyword top-K по IDF-взвешенному совпадению лемм с названиями
              стандартов (функциональные слова приглушены через IDF, но
              категориальные слова «таблица/демонстрационный» сохраняются);
  - union   : объединение vector ∪ keyword.

recall = доля товаров, у которых правильный стандарт (correct_std_id) попал
в шортлист. Это «потолок» качества: LLM не выберет то, чего нет в шортлисте.

Считает для нескольких размеров K, чтобы увидеть, до какого recall можно дойти.

Запуск (из backend, в venv):
    python scripts/recall_experiment.py \
        --csv ../logs/review_20260627_143039_labeled.csv \
        --db-url "postgresql+asyncpg://...:5433/catalog_db"
"""

import argparse
import asyncio
import csv
import math
import os
import re
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

try:
    import pymorphy2
except ImportError:
    print("Ошибка: не установлен pymorphy2.", file=sys.stderr)
    raise

DEFAULT_DB_URL = os.getenv(
    "database_url",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
)

# Только функциональные слова (предлоги/союзы/частицы), которые проходят len>=3.
# Категориальные слова (таблица, демонстрационный, карта, комплект) НЕ глушим —
# они и есть сигнал; их вес регулирует IDF.
FUNCTION_WORDS = {
    "для", "как", "так", "где", "или", "что", "при", "после", "себя", "весь",
    "этот", "тот", "который", "также", "можно", "если", "над", "под", "про",
    "без", "при", "два", "три", "шт",
}


_morph = None


def get_morph():
    global _morph
    if _morph is None:
        _morph = pymorphy2.MorphAnalyzer()
    return _morph


def lemmatize(s: str) -> set:
    words = re.findall(r"\b[а-яА-Яa-zA-ZёЁ]+\b", (s or "").lower())
    morph = get_morph()
    out = set()
    for w in words:
        if len(w) >= 3:
            nf = morph.parse(w)[0].normal_form
            if len(nf) >= 3 and nf not in FUNCTION_WORDS:
                out.add(nf)
    return out


async def main(args):
    # 1. Эталон из размеченного листа
    rows = list(csv.DictReader(open(args.csv, encoding="utf-8-sig"), delimiter=";"))
    truth = {}
    for r in rows:
        cid = (r.get("correct_std_id") or "").strip()
        pid = (r.get("product_id") or "").strip()
        if pid and cid and cid.lower() != "none":
            try:
                truth[int(pid)] = int(cid)
            except ValueError:
                pass
    print(f"Размеченных товаров с эталоном: {len(truth)}")

    engine = create_async_engine(args.db_url, echo=False)
    async with engine.connect() as conn:
        # 2. Все стандарты: id -> леммы названия; df для IDF
        res = await conn.execute(text("SELECT id, item_name FROM industry_standards"))
        std_rows = res.fetchall()
        std_lemmas = {}
        df = {}
        for sid, name in std_rows:
            lem = lemmatize(name)
            std_lemmas[sid] = lem
            for w in lem:
                df[w] = df.get(w, 0) + 1
        N = len(std_rows)
        idf = {w: math.log(N / (c + 1)) + 1.0 for w, c in df.items()}

        # 3. По каждому размеченному товару — vector top и keyword top
        ks = [int(x) for x in args.ks.split(",")]
        maxk = max(ks)

        # счётчики попаданий по стратегиям и K
        hit = {strat: {k: 0 for k in ks} for strat in ("vector", "keyword", "union")}
        total = 0
        miss_examples = []

        for pid, correct in truth.items():
            res = await conn.execute(text(
                "SELECT name, description, embedding FROM products WHERE id = :id"
            ), {"id": pid})
            row = res.fetchone()
            if not row or row[2] is None:
                continue
            name, description, embedding = row
            total += 1

            # vector top-maxk
            vres = await conn.execute(text("""
                SELECT id FROM industry_standards
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:e AS vector)
                LIMIT :k
            """), {"e": embedding, "k": maxk})
            vec_ids = [r[0] for r in vres.fetchall()]

            # keyword top-maxk: IDF-взвешенное совпадение (name + description)
            p_lem = lemmatize(name + " " + (description or ""))
            scored = []
            for sid, lem in std_lemmas.items():
                inter = p_lem & lem
                if inter:
                    score = sum(idf.get(w, 1.0) for w in inter)
                    scored.append((score, sid))
            scored.sort(reverse=True)
            kw_ids = [sid for _, sid in scored[:maxk]]

            for k in ks:
                v = set(vec_ids[:k])
                w = set(kw_ids[:k])
                if correct in v:
                    hit["vector"][k] += 1
                if correct in w:
                    hit["keyword"][k] += 1
                if correct in (v | w):
                    hit["union"][k] += 1

            # для самого большого K зафиксируем промахи union
            if correct not in (set(vec_ids) | set(kw_ids)) and len(miss_examples) < 15:
                miss_examples.append((pid, name, correct))

        print(f"Товаров обработано: {total}")
        print()
        print(f"{'Стратегия':<10} " + " ".join(f"recall@{k:<3}" for k in ks))
        for strat in ("vector", "keyword", "union"):
            cells = " ".join(f"{hit[strat][k]/total:>8.0%}" for k in ks)
            print(f"{strat:<10} {cells}")
        print()
        print("Примеры, где эталон НЕ нашёлся даже в union (нужен либо лучший")
        print("текст для эмбеддинга, либо позиции нет/она «генерик»):")
        for pid, name, correct in miss_examples:
            print(f"  товар {pid}: {name[:60]} -> ждали std={correct}")

    await engine.dispose()


def parse_args():
    p = argparse.ArgumentParser(description="Recall гибридного поиска по размеченному листу")
    p.add_argument("--csv", required=True, help="review_*.csv с заполненным correct_std_id")
    p.add_argument("--db-url", default=DEFAULT_DB_URL)
    p.add_argument("--ks", default="8,15,30", help="Размеры шортлиста через запятую")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
