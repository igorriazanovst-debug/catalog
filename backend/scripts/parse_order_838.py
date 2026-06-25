"""Парсер Приказа 838 -> JSON"""
import pandas as pd
import json, re
from pathlib import Path

INPUT = Path(__file__).resolve().parent.parent.parent / "data" / "input" / "838.xlsx"
OUTPUT = Path(__file__).resolve().parent.parent.parent / "data" / "output" / "order_838_tree.json"

def parse(path):
    df = pd.read_excel(path, header=None)
    tree = {"metadata": {"order": "838"}, "sections": []}
    sec = sub = etype = None
    for _, row in df.iterrows():
        t = str(row[0]).strip() if not pd.isna(row[0]) else ''
        t2 = str(row[1]).strip() if len(row) > 1 and not pd.isna(row[1]) else ''
        if t.startswith('Раздел'):
            m = re.match(r'Раздел\s+(\d+)\.\s+(.*)', t)
            sec = {"code": m.group(1) if m else "", "name": m.group(2) if m else t, "subsections": []}
            tree["sections"].append(sec); sub = None; etype = None
        elif t.startswith('Подраздел') or t.startswith('Часть'):
            m = re.match(r'(?:Подраздел|Часть)\s+(\d+)\.\s*(.*)', t)
            sub = {"code": m.group(1) if m else "", "name": m.group(2) if m else t, "equipment_types": []}
            if sec: sec["subsections"].append(sub)
            etype = None
        elif t in ['Основное оборудование', 'Дополнительное вариативное оборудование']:
            etype = t
            if sub: sub["equipment_types"].append({"type": t, "items": []})
        elif re.match(r'^\d+\.\d+\.\d+\.', t):
            item = {"full_code": t, "name": t2, "equipment_type": etype}
            if sub and sub["equipment_types"]: sub["equipment_types"][-1]["items"].append(item)
    return tree

if __name__ == "__main__":
    if not INPUT.exists():
        print(f'Файл не найден: {INPUT}')
        print('Скопируйте 838.xlsx в data/input/')
        exit(1)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    tree = parse(str(INPUT))
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(tree, f, ensure_ascii=False, indent=2)
    print(f"[OK] Сохранено: {OUTPUT}")
