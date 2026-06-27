"""
Генерация листа экспертной проверки из результатов llm_rerank_eval (JSON).

На вход — *.json от llm_rerank_eval.py. На выход — CSV (Excel-совместимый,
UTF-8 with BOM, разделитель ';'), где для каждого товара уже заполнены
предложение LLM и его уверенность, а эксперту нужно проставить вердикт.

Колонки для заполнения экспертом:
  verdict        — 'ok' если решение LLM верное; 'bad' если неверное.
                   (для null-решений: 'ok' = отказ оправдан, 'bad' = на самом
                    деле подходящий стандарт был.)
  correct_std_id — если LLM ошибся: id правильного стандарта (или 'none',
                   если подходящего в Приказе 838 вообще нет). Можно не заполнять.
  comment        — любой комментарий.

После заполнения скормите CSV в scripts/score_review.py для подсчёта метрик.

Запуск:
    python scripts/make_review_sheet.py --json ../logs/llm_rerank_YYYYMMDD_HHMMSS.json
    python scripts/make_review_sheet.py            # возьмёт самый свежий llm_rerank_*.json
"""

import argparse
import csv
import glob
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LOG_DIR = REPO_ROOT / "logs"

COLUMNS = [
    "product_id", "product_name",
    "llm_decision", "llm_std_name", "llm_confidence",
    "vector_top1_id", "vector_top1_name",
    "shortlist_ids",
    "verdict", "correct_std_id", "comment",
]


def newest_json(log_dir: Path) -> Path:
    files = sorted(glob.glob(str(log_dir / "llm_rerank_*.json")), key=os.path.getmtime)
    if not files:
        print(f"Не найдено llm_rerank_*.json в {log_dir}", file=sys.stderr)
        sys.exit(1)
    return Path(files[-1])


def main(args):
    json_path = Path(args.json) if args.json else newest_json(Path(args.log_dir))
    with open(json_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    items = report.get("items", [])
    if not items:
        print("В JSON нет items — нечего проверять.", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out) if args.out else json_path.with_name(
        json_path.stem.replace("llm_rerank_", "review_") + ".csv"
    )

    # utf-8-sig + ';' — чтобы Excel в русской локали открыл без кракозябр.
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(COLUMNS)
        for it in items:
            llm = it.get("llm", {})
            std_id = llm.get("standard_id")
            decision = str(std_id) if std_id is not None else "null"
            vt = it.get("vector_top1", {})
            shortlist_ids = ",".join(str(c["id"]) for c in it.get("shortlist", []))
            w.writerow([
                it.get("product_id", ""),
                it.get("name", ""),
                decision,
                llm.get("name", "") or "",
                f"{llm.get('confidence', 0.0):.2f}",
                vt.get("id", ""),
                vt.get("name", ""),
                shortlist_ids,
                "",   # verdict — заполняет эксперт
                "",   # correct_std_id — заполняет эксперт
                "",   # comment
            ])

    print(f"Лист проверки: {out_path}")
    print(f"Строк: {len(items)}")
    print("Заполните колонку 'verdict' (ok/bad), при ошибке — 'correct_std_id'.")


def parse_args():
    p = argparse.ArgumentParser(description="Генерация листа экспертной проверки из llm_rerank JSON")
    p.add_argument("--json", default=None, help="Путь к llm_rerank_*.json (по умолчанию — самый свежий)")
    p.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    p.add_argument("--out", default=None, help="Куда писать CSV (по умолчанию рядом с JSON)")
    return p.parse_args()


if __name__ == "__main__":
    main(parse_args())
