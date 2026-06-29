"""Подбор товаров под входящую смету (БЕЗ LLM): разбор xlsx + сопоставление.

Пайплайн на КАЖДУЮ позицию сметы:
  1) разбор файла (`estimate_parser`);
  2) позиция → стандарт 838: по коду КТРУ/ОКПД2, иначе текстовый ретрив;
  3) стандарт → товары → самое дешёвое доступное предложение поставщика;
  4) итог сметы + НДС (`system_settings.vat_rate`).

Сервис read-only — в БД ничего не пишет (валидация качества подбора).

Запуск (из backend, в venv):
    python scripts/match_estimate.py ../data/input/smeta-1.xlsx --db-url "postgresql+asyncpg://...:5433/catalog_db"
    python scripts/match_estimate.py smeta.xlsx --db-url ... --price cost --top-k 20
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.estimate_parser import parse_estimate          # noqa: E402
from app.services.estimate_service import EstimateMatcher        # noqa: E402

DEFAULT_DB_URL = os.getenv(
    "database_url",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
)


def _money(x) -> str:
    return "—" if x is None else f"{x:,.2f}".replace(",", " ")


def _print_result(res: dict) -> None:
    print("=" * 100)
    print(f"СМЕТА (лист: {res.get('sheet')}) — позиций: {len(res['items'])}")
    for r in res["items"]:
        line = r["line"]
        print("-" * 100)
        print(f"  № {line.get('position') or '?'}: {line.get('name') or '(без имени)'}")
        print(f"     код: КТРУ={line.get('code_ktru')} ОКПД2={line.get('code_okpd2')} "
              f"| кол-во: {line.get('quantity')} {line.get('unit') or ''}")
        std = r["standard"]
        method = r["match_method"]
        if std:
            print(f"     → 838 [{method}]: #{std['standard_id']} "
                  f"{std.get('full_code') or ''} {std.get('standard_name')}")
        else:
            print(f"     → 838 [{method}]: НЕ сопоставлено")
        offer = r["chosen_offer"]
        if offer:
            ms = offer.get("match_score")
            ms_s = "—" if ms is None else f"{ms:.2f}"
            kind = "ручной" if offer.get("is_manual") else "авто"
            print(f"     ✓ товар: {offer['product_name']} "
                  f"(арт. {offer.get('sku')}, {offer.get('manufacturer') or '—'}) "
                  f"[маппинг: {kind}, score={ms_s}]")
            print(f"       поставщик: {offer['supplier_name']} | "
                  f"РРЦ={_money(offer.get('retail_price'))} "
                  f"себест.={_money(offer.get('cost_price'))} | "
                  f"срок={offer.get('delivery_days')} остаток={offer.get('stock_quantity')}")
            print(f"       цена×кол-во = {_money(r['unit_price'])} × {line['quantity']} "
                  f"= {_money(r['total_price'])}")
            if r["alternatives"]:
                print(f"       другие предложения ({len(r['alternatives'])}):")
                for a in r["alternatives"][:3]:
                    a_ms = a.get("match_score")
                    a_ms_s = "—" if a_ms is None else f"{a_ms:.2f}"
                    print(f"          · {_money(a.get('retail_price'))} — "
                          f"{a['product_name'][:55]} ({a['supplier_name']}, score={a_ms_s})")
        # Для текстового матча покажем топ кандидатов 838 — проверить качество.
        if method == "text" and r["standard_candidates"]:
            print("       кандидаты 838 (ретрив):")
            for c in r["standard_candidates"][:3]:
                vs = c.get("vector_similarity")
                vs_s = "—" if vs is None else f"{vs:.3f}"
                print(f"          · #{c['standard_id']} {c['standard_name'][:70]} "
                      f"[{'+'.join(c.get('sources', []))} vec={vs_s}]")
        for w in r["warnings"]:
            print(f"       ! {w}")

    s = res["summary"]
    print("=" * 100)
    print("ИТОГ:")
    print(f"  позиций: {s['positions']} | с подбором: {s['matched_with_offer']} "
          f"| по коду: {s['resolved_by_code']} | по тексту: {s['resolved_by_text']} "
          f"| не сопоставлено: {s['unresolved']}")
    print(f"  сумма ({s['price_basis']}): {_money(s['subtotal'])}  "
          f"НДС {s['vat_rate']*100:.0f}%: {_money(s['vat_amount'])}  "
          f"ИТОГО с НДС: {_money(s['total_with_vat'])}")


def _print_availability(av: dict) -> None:
    print("ДОСТУПНОСТЬ В БД (что можно сматчить):")
    print(f"  стандартов 838: {av['standards_total']} "
          f"(с КТРУ: {av['standards_with_ktru']}, с ОКПД2: {av['standards_with_okpd2']})")
    print(f"  товаров: {av['products_total']} (с КТРУ: {av['products_with_ktru']}) | "
          f"активных маппингов: {av['mappings_active']} | "
          f"предложений поставщиков: {av['supplier_offers']}")
    if av["standards_with_ktru"] == 0 and av["standards_with_okpd2"] == 0:
        print("  ⚠ У стандартов 838 не заполнены КТРУ/ОКПД2 — код-матч работать не будет, "
              "только текстовый фоллбэк.")
    if av["mappings_active"] == 0:
        print("  ⚠ Нет активных маппингов товар→стандарт — подбор товара/цены вернёт пусто.")


async def main(args):
    paths = [Path(f) for f in args.files
             if Path(f).suffix.lower() in {".xlsx", ".xlsm", ".xltx", ".xltm"}]
    if not paths:
        print("Нет входных xlsx-файлов.", file=sys.stderr)
        return

    engine = create_async_engine(args.db_url, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with Session() as db:
            matcher = EstimateMatcher(db, price_basis=args.price, top_k=args.top_k)
            _print_availability(await matcher.db_code_availability())
            for path in paths:
                if not path.exists():
                    print(f"Файл не найден: {path}", file=sys.stderr)
                    continue
                parsed = parse_estimate(path)
                result = await matcher.match_estimate(parsed)
                result_file = str(path)
                print()
                print(f"ФАЙЛ: {result_file}")
                _print_result(result)
    finally:
        await engine.dispose()


def parse_args():
    p = argparse.ArgumentParser(description="Подбор товаров под смету (без LLM)")
    p.add_argument("files", nargs="+", help="xlsx-файлы смет")
    p.add_argument("--db-url", default=DEFAULT_DB_URL)
    p.add_argument("--price", choices=["retail", "cost"], default="cost",
                   help="по какой цене выбирать «дешевле» и считать итог (по умолч. cost/себестоимость)")
    p.add_argument("--top-k", type=int, default=20, help="размер пула текстового ретрива")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
