"""
Диагностика качества гибридного маппинга (read-only, без LLM).

Назначение
----------
Скрипт НЕ изменяет данные. Он не пишет в product_standard_mapping и не трогает
products / industry_standards. Его можно запускать сколько угодно раз.

Что делает:
  1. Проверяет состояние БД (preflight): сколько стандартов/товаров, у скольких
     есть эмбеддинги и keywords.
  2. Проверяет, лемматизированы ли keywords у стандартов (частый источник
     keyword_similarity = 0). Сравнивает хранимые keywords с лемматизацией
     названия на выборке.
  3. Для каждого товара выгружает:
       - лемматизированные keywords товара;
       - топ-K кандидатов из ВЕКТОРНОГО поиска (как в проде) с разбивкой
         vector / keyword / hybrid и пересечением keywords;
       - топ-K кандидатов из KEYWORD-поиска по ВСЕМ стандартам (диагностика:
         показывает, что keyword-сигнал «не видит» из-за того, что пул
         кандидатов формируется только вектором);
       - итоговое решение по текущей логике порога.
  4. Сводную статистику: гистограмма лучших hybrid-скоров, доля авто/на-ручную,
     как часто топ-1 по вектору != топ-1 по keyword (расхождение пула).

Формула скоринга ПОВТОРЯЕТ app/services/mapping_service.py:
    keyword_similarity = jaccard + overlap_bonus,
        overlap_bonus = min(intersection / 5.0, 0.3)
    hybrid_score = 0.3 * vector_similarity + 0.7 * keyword_similarity
ВАЖНО: при изменении логики в mapping_service.py синхронизируйте этот файл.

Запуск (на сервере, из каталога backend, в venv проекта):
    python scripts/diagnose_mapping.py
    python scripts/diagnose_mapping.py --limit 50 --threshold 0.7
    python scripts/diagnose_mapping.py --db-url postgresql+asyncpg://user:pass@host:5432/db

Результаты пишутся в ../logs/diagnose_YYYYMMDD_HHMMSS.log (человекочитаемо, UTF-8)
и ../logs/diagnose_YYYYMMDD_HHMMSS.json (для последующего анализа).
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

try:
    import pymorphy2
except ImportError:
    print("Ошибка: не установлен pymorphy2. Установите зависимости проекта.", file=sys.stderr)
    raise

# Та же модель скоринга, что и в mapping_service.py
VECTOR_WEIGHT = 0.3
KEYWORD_WEIGHT = 0.7

DEFAULT_DB_URL = os.getenv(
    "database_url",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "logs"


class Tee:
    """Пишет одновременно в файл (UTF-8) и в stdout."""

    def __init__(self, file_handle):
        self.file_handle = file_handle

    def write(self, line: str = ""):
        print(line)
        self.file_handle.write(line + "\n")


# ---------------------------------------------------------------------------
# Логика keyword (идентична mapping_service.extract_keywords_lemmatized)
# ---------------------------------------------------------------------------

_morph = None


def get_morph():
    global _morph
    if _morph is None:
        _morph = pymorphy2.MorphAnalyzer()
    return _morph


def extract_keywords_lemmatized(s: str) -> set:
    words = re.findall(r"\b[а-яА-Яa-zA-ZёЁ]+\b", (s or "").lower())
    morph = get_morph()
    keywords = set()
    for word in words:
        if len(word) >= 3:
            parsed = morph.parse(word)
            if parsed:
                normal_form = parsed[0].normal_form
                if len(normal_form) >= 3:
                    keywords.add(normal_form)
    return keywords


def keyword_similarity(product_keywords: set, std_keywords: set):
    """Возвращает (similarity, intersection_set) по той же формуле, что в проде."""
    if product_keywords and std_keywords:
        intersection = product_keywords & std_keywords
        inter = len(intersection)
        union = len(product_keywords | std_keywords)
        jaccard = inter / union if union > 0 else 0.0
        overlap_bonus = min(inter / 5.0, 0.3)
        return jaccard + overlap_bonus, intersection
    return 0.0, set()


# ---------------------------------------------------------------------------
# Диагностика
# ---------------------------------------------------------------------------


async def preflight(conn, out: Tee) -> dict:
    out.write("=" * 78)
    out.write("РАЗДЕЛ A. Состояние БД (preflight)")
    out.write("=" * 78)

    async def scalar(sql):
        res = await conn.execute(text(sql))
        return res.scalar()

    stats = {}
    stats["standards_total"] = await scalar("SELECT COUNT(*) FROM industry_standards")
    stats["standards_with_embedding"] = await scalar(
        "SELECT COUNT(*) FROM industry_standards WHERE embedding IS NOT NULL"
    )
    stats["standards_with_keywords"] = await scalar(
        "SELECT COUNT(*) FROM industry_standards "
        "WHERE keywords IS NOT NULL AND array_length(keywords, 1) > 0"
    )
    stats["products_total"] = await scalar("SELECT COUNT(*) FROM products")
    stats["products_with_embedding"] = await scalar(
        "SELECT COUNT(*) FROM products WHERE embedding IS NOT NULL"
    )
    stats["mappings_total"] = await scalar("SELECT COUNT(*) FROM product_standard_mapping")

    out.write(f"  industry_standards всего:            {stats['standards_total']}")
    out.write(f"  industry_standards с эмбеддингом:    {stats['standards_with_embedding']}")
    out.write(f"  industry_standards с keywords:       {stats['standards_with_keywords']}")
    out.write(f"  products всего:                      {stats['products_total']}")
    out.write(f"  products с эмбеддингом:              {stats['products_with_embedding']}")
    out.write(f"  product_standard_mapping (записей):  {stats['mappings_total']}")
    out.write("")

    warnings = []
    if stats["standards_with_embedding"] == 0:
        warnings.append("У стандартов НЕТ эмбеддингов — векторный поиск не сработает. "
                        "Запустите scripts/generate_embeddings.py")
    if stats["products_with_embedding"] == 0:
        warnings.append("У товаров НЕТ эмбеддингов — векторный поиск не сработает. "
                        "Запустите scripts/regenerate_product_embeddings.py")
    if stats["standards_with_keywords"] == 0:
        warnings.append("У стандартов НЕТ keywords — keyword_similarity всегда будет 0. "
                        "Запустите scripts/regenerate_standard_keywords.py")
    for w in warnings:
        out.write(f"  [!] {w}")
    if warnings:
        out.write("")

    stats["warnings"] = warnings
    return stats


async def check_keyword_lemmatization(conn, out: Tee, sample_size: int = 30) -> dict:
    """Проверяет, лемматизированы ли хранимые keywords стандартов.

    Сравнивает stored keywords с тем, что даёт extract_keywords_lemmatized
    по item_name. Большое расхождение => keywords не перегенерировались
    лемматизатором, и пересечение с keywords товара будет искусственно низким.
    """
    out.write("=" * 78)
    out.write("РАЗДЕЛ B. Проверка лемматизации keywords стандартов")
    out.write("=" * 78)

    res = await conn.execute(
        text(
            "SELECT id, item_name, keywords FROM industry_standards "
            "WHERE keywords IS NOT NULL AND array_length(keywords, 1) > 0 "
            "ORDER BY id LIMIT :n"
        ),
        {"n": sample_size},
    )
    rows = res.fetchall()

    if not rows:
        out.write("  Нет стандартов с keywords для проверки.")
        out.write("")
        return {"sampled": 0, "match_ratio": None}

    matches = 0
    mismatches = []
    for std_id, item_name, stored in rows:
        stored_set = set(stored or [])
        expected_set = extract_keywords_lemmatized(item_name)
        if stored_set == expected_set:
            matches += 1
        elif len(mismatches) < 5:
            mismatches.append((std_id, item_name, sorted(stored_set), sorted(expected_set)))

    ratio = matches / len(rows)
    out.write(f"  Проверено стандартов:                {len(rows)}")
    out.write(f"  keywords совпали с лемматизацией:     {matches} ({ratio:.0%})")
    if ratio < 0.8:
        out.write("  [!] keywords стандартов, похоже, НЕ лемматизированы корректно.")
        out.write("      Это занижает keyword_similarity. Запустите "
                  "scripts/regenerate_standard_keywords.py")
    out.write("")
    for std_id, name, stored, expected in mismatches:
        out.write(f"    пример расхождения std={std_id}: {name}")
        out.write(f"      хранится:    {stored}")
        out.write(f"      ожидалось:   {expected}")
    if mismatches:
        out.write("")

    return {"sampled": len(rows), "match_ratio": ratio}


async def load_all_standard_keywords(conn) -> list:
    """Загружает (id, item_name, keywords_set) для всех стандартов — для
    keyword-поиска по всему набору."""
    res = await conn.execute(text("SELECT id, item_name, keywords FROM industry_standards"))
    out = []
    for std_id, item_name, kw in res.fetchall():
        out.append((std_id, item_name, set(kw) if kw else set()))
    return out


async def vector_candidates(conn, product_embedding, top_k: int) -> list:
    """Топ-K кандидатов по векторной близости (как в mapping_service)."""
    query = """
        SELECT
            id,
            item_name,
            keywords,
            1 - (embedding <=> CAST(:embedding AS vector)) AS vector_similarity
        FROM industry_standards
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:embedding AS vector)
        LIMIT :top_k
    """
    res = await conn.execute(
        text(query), {"embedding": product_embedding, "top_k": top_k}
    )
    return res.fetchall()


def histogram(values, edges) -> dict:
    buckets = {}
    labels = []
    for i in range(len(edges) - 1):
        labels.append(f"[{edges[i]:.2f}, {edges[i+1]:.2f})")
        buckets[labels[-1]] = 0
    for v in values:
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            if lo <= v < hi or (i == len(edges) - 2 and v == hi):
                buckets[labels[i]] += 1
                break
    return buckets


async def diagnose(args):
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = out_dir / f"diagnose_{ts}.log"
    json_path = out_dir / f"diagnose_{ts}.json"

    engine = create_async_engine(args.db_url, echo=False)

    report = {
        "generated_at": datetime.now().isoformat(),
        "params": {
            "threshold": args.threshold,
            "top_k": args.top_k,
            "limit": args.limit,
            "db_url": re.sub(r"//[^@]+@", "//***@", args.db_url),
        },
    }

    with open(log_path, "w", encoding="utf-8") as fh:
        out = Tee(fh)
        out.write("ДИАГНОСТИКА ГИБРИДНОГО МАППИНГА (read-only, без LLM)")
        out.write(f"Время:     {report['generated_at']}")
        out.write(f"Порог:     {args.threshold}   top_k: {args.top_k}   "
                  f"limit: {args.limit or 'все'}")
        out.write(f"Формула:   hybrid = {VECTOR_WEIGHT}*vector + {KEYWORD_WEIGHT}*keyword")
        out.write("")

        async with engine.connect() as conn:
            stats = await preflight(conn, out)
            report["db_stats"] = stats

            # Если нет эмбеддингов — векторный поиск невозможен, выходим аккуратно.
            if stats["products_with_embedding"] == 0 or stats["standards_with_embedding"] == 0:
                out.write("Векторный поиск невозможен из-за отсутствия эмбеддингов. "
                          "Диагностика товаров пропущена.")
                report["aborted"] = "no_embeddings"
                _dump_json(json_path, report)
                out.write("")
                out.write(f"Лог:  {log_path}")
                out.write(f"JSON: {json_path}")
                await engine.dispose()
                return log_path

            lemma_check = await check_keyword_lemmatization(conn, out)
            report["lemmatization_check"] = lemma_check

            all_standards = await load_all_standard_keywords(conn)

            # Берём товары
            limit_clause = f"LIMIT {args.limit}" if args.limit else ""
            res = await conn.execute(
                text(
                    "SELECT id, name, description, embedding FROM products "
                    f"WHERE embedding IS NOT NULL ORDER BY id {limit_clause}"
                )
            )
            products = res.fetchall()

            out.write("=" * 78)
            out.write(f"РАЗДЕЛ C. Подробности по товарам (всего {len(products)})")
            out.write("=" * 78)

            per_product = []
            best_scores = []
            decisions = {"auto_hybrid": 0, "below_threshold": 0}
            pool_divergence = 0  # топ-1 вектор != топ-1 keyword

            for prod_id, name, description, embedding in products:
                product_text = name + (" " + description if description else "")
                prod_keywords = extract_keywords_lemmatized(product_text)

                # --- Векторный пул (как в проде) ---
                vrows = await vector_candidates(conn, embedding, args.top_k)
                vec_cands = []
                for std_id, item_name, std_kw, vsim in vrows:
                    std_set = set(std_kw) if std_kw else set()
                    ksim, inter = keyword_similarity(prod_keywords, std_set)
                    hybrid = VECTOR_WEIGHT * float(vsim) + KEYWORD_WEIGHT * ksim
                    vec_cands.append({
                        "standard_id": std_id,
                        "standard_name": item_name,
                        "vector_similarity": round(float(vsim), 4),
                        "keyword_similarity": round(ksim, 4),
                        "hybrid_score": round(hybrid, 4),
                        "intersection": sorted(inter),
                    })
                vec_cands.sort(key=lambda c: c["hybrid_score"], reverse=True)

                # --- Keyword-пул по ВСЕМ стандартам (диагностика) ---
                kw_scored = []
                for std_id, item_name, std_set in all_standards:
                    ksim, inter = keyword_similarity(prod_keywords, std_set)
                    if ksim > 0:
                        kw_scored.append((ksim, std_id, item_name, sorted(inter)))
                kw_scored.sort(key=lambda t: t[0], reverse=True)
                kw_top = kw_scored[: args.top_k]

                best = vec_cands[0] if vec_cands else None
                best_hybrid = best["hybrid_score"] if best else 0.0
                best_scores.append(best_hybrid)

                if best_hybrid >= args.threshold:
                    decision = "AUTO (гибрид)"
                    decisions["auto_hybrid"] += 1
                else:
                    decision = "НИЖЕ ПОРОГА -> LLM/ручная"
                    decisions["below_threshold"] += 1

                # Расхождение пула: топ-1 по вектору и топ-1 по keyword — разные стандарты
                vec_top1_id = vrows[0][0] if vrows else None
                kw_top1_id = kw_top[0][1] if kw_top else None
                diverged = bool(kw_top1_id and vec_top1_id and kw_top1_id != vec_top1_id)
                if diverged:
                    pool_divergence += 1

                # --- Вывод по товару (можно отключить --summary-only) ---
                if not args.summary_only:
                    out.write("")
                    out.write(f"--- Товар {prod_id}: {name}")
                    if description:
                        out.write(f"    Описание: {description}")
                    out.write(f"    keywords(товар): {sorted(prod_keywords) or '∅'}")
                    out.write(f"    Векторный пул (top-{args.top_k}, как в проде):")
                    for c in vec_cands:
                        out.write(
                            f"      std={c['standard_id']:<6} hyb={c['hybrid_score']:.3f} "
                            f"(vec={c['vector_similarity']:.3f} kw={c['keyword_similarity']:.3f}) "
                            f"| {c['standard_name']}"
                        )
                        if c["intersection"]:
                            out.write(f"           ∩keywords: {c['intersection']}")
                    if kw_top:
                        out.write(f"    Keyword-пул по ВСЕМ стандартам (top-{args.top_k}, диагностика):")
                        for ksim, std_id, item_name, inter in kw_top:
                            in_vec = " (есть в вект.пуле)" if std_id in {r[0] for r in vrows} else ""
                            out.write(
                                f"      std={std_id:<6} kw={ksim:.3f} | {item_name}{in_vec}"
                            )
                            out.write(f"           ∩keywords: {inter}")
                    else:
                        out.write("    Keyword-пул: пусто (нет пересечений ни с одним стандартом)")
                    if diverged:
                        out.write("    [!] Расхождение пула: лучший по keyword отсутствует/ниже "
                                  "в векторном пуле — keyword-сигнал теряется.")
                    out.write(f"    Решение: {decision} (лучший hybrid={best_hybrid:.3f})")

                per_product.append({
                    "product_id": prod_id,
                    "name": name,
                    "product_keywords": sorted(prod_keywords),
                    "vector_pool": vec_cands,
                    "keyword_pool_global": [
                        {"standard_id": s, "standard_name": n,
                         "keyword_similarity": round(k, 4), "intersection": inter}
                        for k, s, n, inter in kw_top
                    ],
                    "best_hybrid": round(best_hybrid, 4),
                    "decision": decision,
                    "pool_diverged": diverged,
                })

            # --- Сводка ---
            out.write("")
            out.write("=" * 78)
            out.write("РАЗДЕЛ D. Сводная статистика")
            out.write("=" * 78)
            n = len(products)
            out.write(f"  Товаров обработано:                  {n}")
            if n:
                out.write(f"  AUTO (hybrid >= {args.threshold}):              "
                          f"{decisions['auto_hybrid']} ({decisions['auto_hybrid']/n:.0%})")
                out.write(f"  Ниже порога (-> LLM/ручная):         "
                          f"{decisions['below_threshold']} ({decisions['below_threshold']/n:.0%})")
                out.write(f"  Товаров с пустыми keywords товара:   "
                          f"{sum(1 for p in per_product if not p['product_keywords'])}")
                out.write(f"  Товаров с пустым keyword-пулом:      "
                          f"{sum(1 for p in per_product if not p['keyword_pool_global'])}")
                out.write(f"  Расхождение пула (kw-top1 != vec-top1): "
                          f"{pool_divergence} ({pool_divergence/n:.0%})")
                out.write("")
                out.write("  Гистограмма лучших hybrid-скоров:")
                edges = [0.0, 0.2, 0.4, 0.5, 0.6, 0.65, 0.7, 0.75, 0.8, 0.9, 1.001]
                hist = histogram(best_scores, edges)
                for label, count in hist.items():
                    bar = "#" * count
                    out.write(f"    {label:<14} {count:>4}  {bar}")
                avg = sum(best_scores) / n
                out.write("")
                out.write(f"  Средний лучший hybrid:               {avg:.3f}")

            report["summary"] = {
                "products_processed": n,
                "decisions": decisions,
                "pool_divergence": pool_divergence,
                "avg_best_hybrid": round(sum(best_scores) / n, 4) if n else None,
            }
            report["products"] = per_product

            out.write("")
            out.write(f"Лог:  {log_path}")
            out.write(f"JSON: {json_path}")

    _dump_json(json_path, report)
    await engine.dispose()
    return log_path


def _dump_json(path: Path, report: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def parse_args():
    p = argparse.ArgumentParser(description="Read-only диагностика гибридного маппинга")
    p.add_argument("--db-url", default=DEFAULT_DB_URL,
                   help="URL БД (async). По умолчанию env database_url или localhost.")
    p.add_argument("--threshold", type=float, default=0.7,
                   help="Порог авто-маппинга по hybrid (как в /auto-map). По умолчанию 0.7")
    p.add_argument("--top-k", type=int, default=5, help="Сколько кандидатов показывать")
    p.add_argument("--limit", type=int, default=0,
                   help="Ограничить число товаров (0 = все)")
    p.add_argument("--summary-only", action="store_true",
                   help="Не печатать разбор по каждому товару, только сводку "
                        "(рекомендуется для больших наборов).")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR),
                   help="Куда писать логи (по умолчанию <repo>/logs)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    log = asyncio.run(diagnose(args))
    print(f"\nГотово. Лог: {log}")
