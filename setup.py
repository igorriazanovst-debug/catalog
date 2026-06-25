#!/usr/bin/env python3
"""Скрипт создания проекта School Equipment Catalog"""
import subprocess
from pathlib import Path

R = Path.cwd()

def w(p, c=""):
    f = R / p
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(c, encoding="utf-8")
    print(f"  + {p}")

# --- Структура ---
for d in [
    "backend/app/api/endpoints", "backend/app/core", "backend/app/models",
    "backend/app/services", "backend/app/schemas", "backend/app/utils",
    "backend/scripts", "database", "data/input", "data/output", "data/temp",
]:
    (R / d).mkdir(parents=True, exist_ok=True)
print("[OK] Структура создана")

# --- .gitignore ---
w(".gitignore", "\n".join([
    "__pycache__/", "*.py[cod]", "venv/", ".env", "*.db",
    "node_modules/", ".next/", "data/input/*.xlsx", "data/input/*.csv",
    "data/input/*.docx", "data/output/*", "data/temp/*", "*.log", "",
]))

# --- README ---
w("README.md", "\n".join([
    "# School Equipment Catalog",
    "",
    "SaaS для комплектации школ по Приказу 838.",
    "",
    "## Быстрый старт",
    "",
    "    cd backend",
    "    python -m venv venv",
    "    venv\\Scripts\\activate",
    "    pip install -r requirements.txt",
    "    uvicorn app.main:app --reload",
    "",
    "API docs: http://localhost:8000/docs",
    "",
]))

# --- requirements ---
w("backend/requirements.txt", "\n".join([
    "fastapi==0.109.0", "uvicorn[standard]==0.27.0", "python-multipart==0.0.6",
    "pydantic==2.5.3", "pydantic-settings==2.1.0", "sqlalchemy==2.0.25",
    "asyncpg==0.29.0", "psycopg2-binary==2.9.9", "pandas==2.1.4",
    "openpyxl==3.1.2", "python-docx==1.1.0", "numpy==1.26.3",
    "python-dotenv==1.0.0", "sentence-transformers==2.3.1", "",
]))

# --- Пустые __init__ ---
for p in [
    "backend/app/__init__.py", "backend/app/api/__init__.py",
    "backend/app/api/endpoints/__init__.py", "backend/app/schemas/__init__.py",
    "backend/app/core/__init__.py", "backend/app/models/__init__.py",
    "backend/app/services/__init__.py", "backend/app/utils/__init__.py",
]:
    w(p)

# --- .env ---
w("backend/.env", "\n".join([
    "DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db",
    "REDIS_URL=redis://localhost:6379/0",
    "SECRET_KEY=change-me-in-production", "",
]))

# --- main.py ---
w("backend/app/main.py", "\n".join([
    "from fastapi import FastAPI",
    "from fastapi.middleware.cors import CORSMiddleware",
    "",
    'app = FastAPI(title="School Equipment Catalog API", version="0.1.0")',
    "",
    "app.add_middleware(",
    "    CORSMiddleware,",
    '    allow_origins=["*"],',
    '    allow_methods=["*"],',
    '    allow_headers=["*"],',
    ")",
    "",
    '@app.get("/")',
    "async def root():",
    '    return {"status": "ok"}',
    "",
    '@app.get("/health")',
    "async def health():",
    '    return {"status": "healthy"}',
    "",
]))

# --- database/init.sql ---
w("database/init.sql", "\n".join([
    "CREATE EXTENSION IF NOT EXISTS vector;",
    "",
    "CREATE TABLE industry_standards (",
    "    id SERIAL PRIMARY KEY,",
    "    industry_code VARCHAR(20) NOT NULL,",
    "    section_code VARCHAR(10),",
    "    subsection_code VARCHAR(10),",
    "    section_name TEXT,",
    "    subsection_name TEXT,",
    "    item_name TEXT NOT NULL,",
    "    equipment_type VARCHAR(50),",
    "    keywords TEXT[],",
    "    okpd2_code VARCHAR(20),",
    "    ktru_code VARCHAR(20),",
    "    embedding VECTOR(768),",
    "    created_at TIMESTAMP DEFAULT NOW()",
    ");",
    "",
    "CREATE TABLE products (",
    "    id SERIAL PRIMARY KEY,",
    "    sku VARCHAR(100) UNIQUE NOT NULL,",
    "    name TEXT NOT NULL,",
    "    description TEXT,",
    "    unit VARCHAR(50) DEFAULT 'шт',",
    "    cost_price DECIMAL(15,2) NOT NULL,",
    "    retail_price DECIMAL(15,2) NOT NULL,",
    "    manufacturer TEXT,",
    "    embedding VECTOR(768),",
    "    created_at TIMESTAMP DEFAULT NOW()",
    ");",
    "",
    "CREATE TABLE product_standard_mapping (",
    "    id SERIAL PRIMARY KEY,",
    "    product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,",
    "    standard_id INTEGER REFERENCES industry_standards(id) ON DELETE CASCADE,",
    "    confidence_score DECIMAL(3,2),",
    "    mapping_type VARCHAR(20) DEFAULT 'auto',",
    "    created_at TIMESTAMP DEFAULT NOW(),",
    "    UNIQUE(product_id, standard_id)",
    ");",
    "",
]))

# --- parse_order_838.py ---
w("backend/scripts/parse_order_838.py", "\n".join([
    '"""Парсер Приказа 838 -> JSON"""',
    "import pandas as pd",
    "import json, re",
    "from pathlib import Path",
    "",
    'INPUT = Path(__file__).resolve().parent.parent.parent / "data" / "input" / "838.xlsx"',
    'OUTPUT = Path(__file__).resolve().parent.parent.parent / "data" / "output" / "order_838_tree.json"',
    "",
    "def parse(path):",
    "    df = pd.read_excel(path, header=None)",
    '    tree = {"metadata": {"order": "838"}, "sections": []}',
    "    sec = sub = etype = None",
    "    for _, row in df.iterrows():",
    "        t = str(row[0]).strip() if not pd.isna(row[0]) else ''",
    "        t2 = str(row[1]).strip() if len(row) > 1 and not pd.isna(row[1]) else ''",
    "        if t.startswith('Раздел'):",
    "            m = re.match(r'Раздел\\s+(\\d+)\\.\\s+(.*)', t)",
    '            sec = {"code": m.group(1) if m else "", "name": m.group(2) if m else t, "subsections": []}',
    '            tree["sections"].append(sec); sub = None; etype = None',
    "        elif t.startswith('Подраздел') or t.startswith('Часть'):",
    "            m = re.match(r'(?:Подраздел|Часть)\\s+(\\d+)\\.\\s*(.*)', t)",
    '            sub = {"code": m.group(1) if m else "", "name": m.group(2) if m else t, "equipment_types": []}',
    '            if sec: sec["subsections"].append(sub)',
    "            etype = None",
    "        elif t in ['Основное оборудование', 'Дополнительное вариативное оборудование']:",
    "            etype = t",
    '            if sub: sub["equipment_types"].append({"type": t, "items": []})',
    "        elif re.match(r'^\\d+\\.\\d+\\.\\d+\\.', t):",
    '            item = {"full_code": t, "name": t2, "equipment_type": etype}',
    '            if sub and sub["equipment_types"]: sub["equipment_types"][-1]["items"].append(item)',
    "    return tree",
    "",
    'if __name__ == "__main__":',
    "    if not INPUT.exists():",
    "        print(f'Файл не найден: {INPUT}')",
    "        print('Скопируйте 838.xlsx в data/input/')",
    "        exit(1)",
    "    OUTPUT.parent.mkdir(parents=True, exist_ok=True)",
    "    tree = parse(str(INPUT))",
    '    with open(OUTPUT, "w", encoding="utf-8") as f:',
    "        json.dump(tree, f, ensure_ascii=False, indent=2)",
    '    print(f"[OK] Сохранено: {OUTPUT}")',
    "",
]))

# --- Git ---
if not (R / ".git").exists():
    subprocess.run(["git", "init"], capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], capture_output=True)
    print("[OK] Git init")
r = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True)
if r.returncode != 0:
    subprocess.run(["git", "remote", "add", "origin",
        "https://github.com/igorriazanovst-debug/catalog.git"], capture_output=True)
    print("[OK] Remote added")

# --- venv ---
venv_dir = R / "backend" / "venv"
if not venv_dir.exists():
    subprocess.run(["python", "-m", "venv", str(venv_dir)], capture_output=True)
    print("[OK] venv создан")

print("\n=== ГОТОВО ===")
print("Далее:")
print("  1. Скопируйте 838.xlsx и товары.csv в data/input/")
print("  2. cd backend && venv\\Scripts\\activate")
print("  3. pip install -r requirements.txt")
print("  4. python scripts/parse_order_838.py")
print("  5. uvicorn app.main:app --reload")