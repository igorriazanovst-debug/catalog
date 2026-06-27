"""
Импорт позиций Приказа 838 из плоского JSON в industry_standards.

Источник — JSON от нового parse_order_838.py: {"positions": [ {...}, ... ]}.
Каждая позиция несёт full_code (иерархический код), имена раздела/кабинета,
part_name, equipment_type, item_name.

Перед импортом таблица очищается (DELETE), поэтому id присваиваются заново.
Колонка full_code добавляется при необходимости (ALTER TABLE ... IF NOT EXISTS).

Запуск (из backend, в venv):
    python scripts/import_standards.py --db-url "postgresql+asyncpg://...:5433/catalog_db"
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

try:
    import pymorphy2
    _morph = pymorphy2.MorphAnalyzer()
except Exception:  # noqa: BLE001
    _morph = None

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_JSON = REPO_ROOT / "data" / "output" / "order_838_tree.json"
DEFAULT_DB_URL = os.getenv(
    "database_url",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
)


def lemmatize(s: str) -> list:
    words = re.findall(r"\b[а-яА-Яa-zA-ZёЁ]+\b", (s or "").lower())
    out = set()
    for w in words:
        if len(w) >= 3:
            nf = _morph.parse(w)[0].normal_form if _morph else w
            if len(nf) >= 3:
                out.add(nf)
    return sorted(out)


async def import_standards(json_path: Path, db_url: str):
    data = json.load(open(json_path, encoding="utf-8"))
    positions = data.get("positions") or []
    if not positions:
        print("В JSON нет positions — нечего импортировать.", file=sys.stderr)
        sys.exit(1)
    industry_code = data.get("metadata", {}).get("order", "838")
    print(f"Позиций к импорту: {len(positions)} (industry_code={industry_code})")
    if _morph is None:
        print("[!] pymorphy2 недоступен — keywords будут без лемматизации.")

    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE industry_standards ADD COLUMN IF NOT EXISTS full_code VARCHAR(20)"
        ))
        await conn.execute(text("DELETE FROM industry_standards"))
        print("Таблица очищена, колонка full_code на месте.")

        batch = 100
        for i in range(0, len(positions), batch):
            chunk = positions[i:i + batch]
            values, params = [], {}
            for j, p in enumerate(chunk):
                values.append(
                    f"(:ic_{j}, :fc_{j}, :sc_{j}, :ssc_{j}, :sn_{j}, :ssn_{j}, "
                    f":et_{j}, :inm_{j}, :kw_{j})"
                )
                params[f"ic_{j}"] = industry_code
                params[f"fc_{j}"] = p.get("full_code")
                params[f"sc_{j}"] = p.get("section_code")
                params[f"ssc_{j}"] = p.get("subsection_code")
                params[f"sn_{j}"] = p.get("section_name")
                params[f"ssn_{j}"] = p.get("subsection_name")
                params[f"et_{j}"] = p.get("equipment_type")
                params[f"inm_{j}"] = p.get("item_name")
                params[f"kw_{j}"] = lemmatize(p.get("item_name", ""))
            await conn.execute(text(
                "INSERT INTO industry_standards "
                "(industry_code, full_code, section_code, subsection_code, "
                " section_name, subsection_name, equipment_type, item_name, keywords) "
                f"VALUES {', '.join(values)}"
            ), params)
        print(f"Импортировано {len(positions)} позиций. Эмбеддинги — NULL, "
              f"запустите generate_embeddings.py")

    await engine.dispose()


def parse_args():
    p = argparse.ArgumentParser(description="Импорт Приказа 838 из плоского JSON")
    p.add_argument("--json", default=str(DEFAULT_JSON))
    p.add_argument("--db-url", default=DEFAULT_DB_URL)
    return p.parse_args()


if __name__ == "__main__":
    a = parse_args()
    asyncio.run(import_standards(Path(a.json), a.db_url))
