"""
Подсчёт метрик качества по заполненному листу экспертной проверки.

На вход — CSV от make_review_sheet.py, в котором эксперт заполнил колонку
'verdict' (ok/bad). Считает:
  - точность маппинга (precision) среди НЕ-null решений LLM;
  - точность отказов среди null-решений;
  - общую долю верных решений;
  - разбивку точности по уровню уверенности LLM (калибровка порога);
  - (если заполнен correct_std_id) сколько раз правильный стандарт ВООБЩЕ был
    в шортлисте вектора — оценка «потолка» (recall векторного шортлиста).

Запуск:
    python scripts/score_review.py --csv ../logs/review_YYYYMMDD_HHMMSS.csv
"""

import argparse
import csv
import sys
from pathlib import Path


def load_rows(path: Path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def main(args):
    rows = load_rows(Path(args.csv))
    labeled = [r for r in rows if (r.get("verdict") or "").strip().lower() in ("ok", "bad")]

    if not labeled:
        print("Нет размеченных строк (verdict = ok/bad). Заполните лист и повторите.",
              file=sys.stderr)
        sys.exit(1)

    def is_ok(r):
        return (r.get("verdict") or "").strip().lower() == "ok"

    nonnull = [r for r in labeled if (r.get("llm_decision") or "").strip().lower() != "null"]
    nulls = [r for r in labeled if (r.get("llm_decision") or "").strip().lower() == "null"]

    total = len(labeled)
    total_ok = sum(1 for r in labeled if is_ok(r))

    print("=" * 60)
    print("МЕТРИКИ ПО ЛИСТУ ПРОВЕРКИ")
    print("=" * 60)
    print(f"Размечено строк:                 {total}")
    print(f"Верных решений всего:            {total_ok} ({total_ok/total:.0%})")
    print("")

    if nonnull:
        ok_nn = sum(1 for r in nonnull if is_ok(r))
        print(f"Маппинг (LLM выбрал стандарт):   {len(nonnull)} шт")
        print(f"  precision (верных выборов):    {ok_nn}/{len(nonnull)} ({ok_nn/len(nonnull):.0%})")
    if nulls:
        ok_n = sum(1 for r in nulls if is_ok(r))
        print(f"Отказы (LLM сказал null):        {len(nulls)} шт")
        print(f"  точность отказов:              {ok_n}/{len(nulls)} ({ok_n/len(nulls):.0%})")
    print("")

    # Калибровка по уверенности
    print("Точность по уровню уверенности LLM:")
    bands = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.0001)]
    for lo, hi in bands:
        band = []
        for r in nonnull:
            try:
                c = float((r.get("llm_confidence") or "0").replace(",", "."))
            except ValueError:
                c = 0.0
            if lo <= c < hi:
                band.append(r)
        if band:
            ok_b = sum(1 for r in band if is_ok(r))
            print(f"  conf [{lo:.1f}, {hi:.1f}): {ok_b}/{len(band)} верных ({ok_b/len(band):.0%})")
    print("")

    # Recall шортлиста (если эксперт указал правильный id)
    with_truth = [r for r in labeled
                  if (r.get("correct_std_id") or "").strip()
                  and (r.get("correct_std_id") or "").strip().lower() != "none"]
    if with_truth:
        in_shortlist = 0
        for r in with_truth:
            correct = (r.get("correct_std_id") or "").strip()
            ids = [x.strip() for x in (r.get("shortlist_ids") or "").split(",") if x.strip()]
            if correct in ids:
                in_shortlist += 1
        print(f"Правильный стандарт был в шортлисте вектора: "
              f"{in_shortlist}/{len(with_truth)} ({in_shortlist/len(with_truth):.0%})")
        print("  (это «потолок» для LLM: если правильного нет в шортлисте — LLM не сможет его выбрать)")


def parse_args():
    p = argparse.ArgumentParser(description="Метрики по заполненному листу проверки")
    p.add_argument("--csv", required=True, help="Путь к заполненному review_*.csv")
    return p.parse_args()


if __name__ == "__main__":
    main(parse_args())
