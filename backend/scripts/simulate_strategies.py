"""
Сравнение стратегий скоринга маппинга (read-only, без LLM, без записи в БД).

Считает на одних и тех же товарах несколько вариантов скоринга бок о бок,
чтобы числами выбрать лучший ДО изменения боевого mapping_service.

Стратегии (каждая ранжирует внутри одного и того же векторного пула top-N):
  A  vec-only   : score = vector_similarity
  B  current    : 0.3*vec + 0.7*kw, kw по ХРАНИМЫМ keywords стандарта
                  (точная копия прода: keywords стандарта НЕ лемматизированы)
  C  +lemmas    : то же 0.3/0.7, но keywords стандарта лемматизируются на лету
  D  +stopwords : C + удаление стоп-слов (паразитов) из обоих наборов keywords
  E  +reweight  : D, но вес 0.7*vec + 0.3*kw (keyword как бонус, не как драйвер)

Изолированные эффекты: B->C лемматизация, C->D стоп-слова, D->E веса.

Ground truth не задан, поэтому выбор «правильности» — за человеком: скрипт
показывает, какой стандарт выбрала каждая стратегия. Стратегия A (чистый вектор)
используется как опорная точка сравнения.

Запуск (из backend, в venv):
    python scripts/simulate_strategies.py --db-url "postgresql+asyncpg://...:5433/catalog_db"
    python scripts/simulate_strategies.py --pool 15 --threshold 0.7
"""

import argparse
import asyncio
import os
import re
import sys
from collections import Counter
from datetime import datetime
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
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "logs"

# Явные стоп-слова (доменные паразиты), которые проходят фильтр len>=3,
# но не несут различающего смысла в названиях позиций/товаров.
EXPLICIT_STOPWORDS = {
    "для", "как", "так", "где", "или", "что", "при", "после", "себя", "весь",
    "этот", "тот", "каждый", "который", "также", "можно", "если", "при",
    "набор", "комплект", "комплектность", "паспорт", "штука", "размер",
    "формат", "таблица", "пособие", "материал", "использование", "использовать",
    "использоваться", "состоять", "представлять", "представить", "являться",
    "предназначить", "входить", "содержать", "состав", "вид", "тип", "часть",
    "один", "два", "три", "шт", "класс", "урок", "школа", "начальный",
    "демонстрационный", "раздаточный", "учебный", "наглядный",
}


class Tee:
    def __init__(self, fh):
        self.fh = fh

    def write(self, line: str = ""):
        print(line)
        self.fh.write(line + "\n")


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
            if len(nf) >= 3:
                out.add(nf)
    return out


def kw_sim(a: set, b: set) -> float:
    """jaccard + overlap_bonus — как в mapping_service."""
    if a and b:
        inter = len(a & b)
        union = len(a | b)
        jaccard = inter / union if union else 0.0
        overlap_bonus = min(inter / 5.0, 0.3)
        return jaccard + overlap_bonus
    return 0.0


def best_pick(candidates, score_fn):
    """candidates: list of dict. Возвращает (cand, score) с максимальным score."""
    best, best_s = None, -1.0
    for c in candidates:
        s = score_fn(c)
        if s > best_s:
            best, best_s = c, s
    return best, best_s


async def run(args):
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = out_dir / f"simulate_{ts}.log"

    engine = create_async_engine(args.db_url, echo=False)

    with open(log_path, "w", encoding="utf-8") as fh:
        out = Tee(fh)
        out.write("СИМУЛЯЦИЯ СТРАТЕГИЙ СКОРИНГА (read-only, без LLM)")
        out.write(f"Время: {datetime.now().isoformat()}")
        out.write(f"Пул кандидатов (вектор top-N): {args.pool}   Порог: {args.threshold}")
        out.write("")

        async with engine.connect() as conn:
            # 1. Лемматизируем ВСЕ стандарты на лету + строим document frequency.
            res = await conn.execute(text("SELECT id, item_name, keywords FROM industry_standards"))
            std_rows = res.fetchall()
            std_lemmas = {}      # id -> set лемм
            std_stored = {}      # id -> set хранимых keywords
            std_name = {}        # id -> item_name
            df = Counter()       # лемма -> в скольких стандартах встречается
            for std_id, item_name, stored in std_rows:
                lem = lemmatize(item_name)
                std_lemmas[std_id] = lem
                std_stored[std_id] = set(stored) if stored else set()
                std_name[std_id] = item_name
                for w in lem:
                    df[w] += 1
            n_std = len(std_rows)

            # 2. Авто-стоп-слова: леммы, встречающиеся слишком часто (>5% стандартов).
            auto_stop = {w for w, c in df.items() if c / n_std > 0.05}
            stopwords = EXPLICIT_STOPWORDS | auto_stop

            out.write("=" * 78)
            out.write("СТОП-СЛОВА")
            out.write("=" * 78)
            out.write(f"  Стандартов всего: {n_std}")
            out.write("  Топ-25 самых частых лемм в стандартах (кандидаты в стоп-слова):")
            for w, c in df.most_common(25):
                out.write(f"    {w:<22} в {c} стандартах ({c/n_std:.1%})")
            out.write(f"  Авто-стоп (>5% стандартов): {sorted(auto_stop)}")
            out.write(f"  Итоговый стоп-лист (явные + авто): {len(stopwords)} слов")
            out.write("")

            # 3. Товары
            res = await conn.execute(
                text("SELECT id, name, description, embedding FROM products "
                     "WHERE embedding IS NOT NULL ORDER BY id")
            )
            products = res.fetchall()

            strategies = ["A vec-only", "B current", "C +lemmas", "D +stopwords", "E +reweight"]
            # Сбор статистики по стратегиям
            auto70 = {s: 0 for s in strategies}
            auto_thr = {s: 0 for s in strategies}
            sum_score = {s: 0.0 for s in strategies}
            agree_A = {s: 0 for s in strategies}

            out.write("=" * 78)
            out.write(f"ПО ТОВАРАМ (всего {len(products)})")
            out.write("=" * 78)

            for pid, name, description, embedding in products:
                ptext = name + ((" " + description) if description else "")
                p_lem = lemmatize(ptext)
                p_lem_f = p_lem - stopwords

                # Векторный пул top-N
                q = text("""
                    SELECT id, 1 - (embedding <=> CAST(:e AS vector)) AS vsim
                    FROM industry_standards
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> CAST(:e AS vector)
                    LIMIT :k
                """)
                vres = await conn.execute(q, {"e": embedding, "k": args.pool})
                cands = []
                for std_id, vsim in vres.fetchall():
                    cands.append({
                        "id": std_id,
                        "vsim": float(vsim),
                        "stored": std_stored.get(std_id, set()),
                        "lem": std_lemmas.get(std_id, set()),
                        "lem_f": std_lemmas.get(std_id, set()) - stopwords,
                    })

                if not cands:
                    continue

                def score_A(c): return c["vsim"]
                def score_B(c): return 0.3 * c["vsim"] + 0.7 * kw_sim(p_lem, c["stored"])
                def score_C(c): return 0.3 * c["vsim"] + 0.7 * kw_sim(p_lem, c["lem"])
                def score_D(c): return 0.3 * c["vsim"] + 0.7 * kw_sim(p_lem_f, c["lem_f"])
                def score_E(c): return 0.7 * c["vsim"] + 0.3 * kw_sim(p_lem_f, c["lem_f"])

                picks = {
                    "A vec-only":   best_pick(cands, score_A),
                    "B current":    best_pick(cands, score_B),
                    "C +lemmas":    best_pick(cands, score_C),
                    "D +stopwords": best_pick(cands, score_D),
                    "E +reweight":  best_pick(cands, score_E),
                }

                a_id = picks["A vec-only"][0]["id"]

                out.write("")
                out.write(f"--- Товар {pid}: {name}")
                for s in strategies:
                    cand, sc = picks[s]
                    mark = " =A" if cand["id"] == a_id else "   "
                    auto = "AUTO" if sc >= args.threshold else "    "
                    out.write(f"   {s:<13}{mark} {auto} {sc:.3f}  std={cand['id']:<5} "
                              f"{std_name[cand['id']]}")
                    sum_score[s] += sc
                    if sc >= 0.7:
                        auto70[s] += 1
                    if sc >= args.threshold:
                        auto_thr[s] += 1
                    if cand["id"] == a_id:
                        agree_A[s] += 1

            # Сводка
            n = len(products)
            out.write("")
            out.write("=" * 78)
            out.write("СВОДКА ПО СТРАТЕГИЯМ")
            out.write("=" * 78)
            out.write(f"  Товаров: {n}")
            out.write("")
            out.write(f"  {'Стратегия':<14} {'AUTO@0.70':>10} {'AUTO@'+str(args.threshold):>10} "
                      f"{'avg_score':>10} {'выбор==A':>10}")
            for s in strategies:
                out.write(f"  {s:<14} {auto70[s]:>10} {auto_thr[s]:>10} "
                          f"{sum_score[s]/n:>10.3f} {agree_A[s]:>10}/{n}")
            out.write("")
            out.write("  Подсказка: 'выбор==A' = насколько стратегия согласна с чистым вектором.")
            out.write("  Чем ближе keyword-стратегия к A, но с более высоким score, тем лучше:")
            out.write("  она сохраняет правильные векторные матчи и добавляет уверенности.")
            out.write("")
            out.write(f"Лог: {log_path}")

    await engine.dispose()
    return log_path


def parse_args():
    p = argparse.ArgumentParser(description="Сравнение стратегий скоринга (read-only)")
    p.add_argument("--db-url", default=DEFAULT_DB_URL)
    p.add_argument("--pool", type=int, default=15, help="Размер векторного пула кандидатов")
    p.add_argument("--threshold", type=float, default=0.7)
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    return p.parse_args()


if __name__ == "__main__":
    log = asyncio.run(run(parse_args()))
    print(f"\nГотово. Лог: {log}")
