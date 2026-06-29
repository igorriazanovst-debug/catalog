"""Предварительный разбор входящей сметы (xlsx) — БЕЗ LLM.

Печатает, как распозналась шапка/колонки и какие позиции извлечены. Это шаг
«предварительного разбора»: смета не фиксирована по колонкам, поэтому сначала
надо понять её структуру, а уже потом сопоставлять позиции с 838/товарами.

Запуск:
    python scripts/parse_estimate.py <файл.xlsx> [<файл2.xlsx> ...]
    python scripts/parse_estimate.py <файл.xlsx> --json out.json
"""

import argparse
import json
import sys
from pathlib import Path

# Делаем пакет app импортируемым при запуске скрипта напрямую.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.estimate_parser import parse_estimate  # noqa: E402


def _print_human(res: dict) -> None:
    print("=" * 100)
    print(f"ФАЙЛ: {res.get('file')}")
    print(f"Листы: {res.get('sheets')}  | разобран: {res.get('sheet')}")
    if not res.get("items") and not res.get("columns"):
        print("Предупреждения:", res.get("warnings"))
        return
    print(f"Строка-шапка: {res.get('header_row')}")
    print("Колонки (поле -> столбец):")
    for fld, col in res.get("columns", {}).items():
        print(f"   {fld:12s} -> {col}")
    if res.get("value_columns"):
        print(f"   {'char_value':12s} -> {', '.join(res['value_columns'])} (склеиваются)")
    print("Заголовки столбцов (как прочитаны):")
    for col, txt in res.get("column_headers", {}).items():
        print(f"   {col}: {txt[:90]}")
    if res.get("warnings"):
        print("Предупреждения по таблице:")
        for w in res["warnings"]:
            print(f"   ! {w}")

    items = res.get("items", [])
    print(f"\nПОЗИЦИЙ: {len(items)}")
    for it in items:
        print("-" * 90)
        head = f"  № {it.get('position') or '?'}: {it.get('name') or '(без имени)'}"
        print(head)
        code = it.get("code_ktru") or it.get("code_okpd2") or it.get("code_raw")
        print(f"     код: КТРУ={it.get('code_ktru')} ОКПД2={it.get('code_okpd2')} (сырой: {it.get('code_raw')})")
        qty = it.get("quantity")
        if qty or it.get("unit"):
            print(f"     кол-во: {qty} {it.get('unit') or ''}  цена: {it.get('price')}  стоимость: {it.get('total')}")
        chars = it.get("characteristics", [])
        print(f"     характеристик: {len(chars)}")
        for ch in chars[:8]:
            val = ch.get("value") or ""
            unit = ch.get("unit") or ""
            print(f"        - {ch.get('name')}: {val} {unit}".rstrip())
        if len(chars) > 8:
            print(f"        ... ещё {len(chars) - 8}")
        if it.get("warnings"):
            for w in it["warnings"]:
                print(f"     ! {w}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Предварительный разбор сметы (xlsx)")
    ap.add_argument("files", nargs="+", help="xlsx-файлы смет")
    ap.add_argument("--json", help="сохранить результат(ы) в JSON-файл")
    args = ap.parse_args()

    _SPREADSHEET_EXT = {".xlsx", ".xlsm", ".xltx", ".xltm"}
    results = []
    for f in args.files:
        path = Path(f)
        if not path.exists():
            print(f"Файл не найден: {path}", file=sys.stderr)
            continue
        if path.suffix.lower() not in _SPREADSHEET_EXT:
            hint = " (для сохранения JSON используйте флаг --json)" if path.suffix.lower() == ".json" else ""
            print(f"Пропущен (не xlsx): {path}{hint}", file=sys.stderr)
            continue
        res = parse_estimate(path)
        results.append(res)
        _print_human(res)

    if args.json:
        out = Path(args.json)
        payload = results[0] if len(results) == 1 else results
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[OK] JSON сохранён -> {out}")


if __name__ == "__main__":
    main()
