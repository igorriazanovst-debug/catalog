# Контекст проекта: Школьный каталог оборудования

## 1. О проекте
SaaS-каталог для автоматизации подбора школьного оборудования по Приказу Минпросвещения РФ №838 от 28.11.2024. 
**Основная задача:** поставщик загружает свои товары (CSV), система автоматически сопоставляет их с позициями стандарта (Приказ 838) и помогает формировать сметы для школ с учетом множества факторов (ОКПД2, КТРУ, сроки поставки, цены, характеристики).

## 2. Стек технологий
- **Backend:** Python 3.10, FastAPI, SQLAlchemy 2.0 (async), asyncpg
- **База данных:** PostgreSQL 16 + pgvector (Docker, образ `pgvector/pgvector:pg16`)
- **Эмбеддинги:** `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` (768 dim, multilingual)
- **Лемматизация:** pymorphy2
- **LLM (планируется):** YandexGPT
- **OS:** Windows 11 (локальная разработка), перенос на Ubuntu

## 3. Структура проекта
C:\Temp\2026\catalog
├── database/
│ └── init.sql
├── data/
│ ├── output/order_838_tree.json
│ ├── шаблон товары.csv
│ └── 838.xlsx
├── backend/
│ ├── app/
│ │ ├── main.py
│ │ ├── core/database.py
│ │ ├── api/endpoints/products.py
│ │ ├── api/endpoints/mapping.py
│ │ └── services/product_service.py
│ └── scripts/
│ ├── import_standards.py
│ ├── generate_embeddings.py
│ ├── regenerate_product_embeddings.py
│ └── regenerate_standard_keywords.py
└── docker-compose.yml

## 4. Схема БД (финальная)
- `industry_standards` — 1377 позиций Приказа 838 + embedding(768) + keywords[]
- `products` — товары поставщиков + embedding(768) + properties(JSONB) + okpd2/ktru
- `suppliers` — поставщики (name, short_name, inn, contacts)
- `supplier_products` — связь M:N: цены, delivery_days, stock_quantity, supplier_sku
- `product_standard_mapping` — маппинг: match_score, match_reason, is_manual, rejected
- `system_settings` — key-value: vat_rate=0.22, currency=RUB, company_name
- `estimates` / `estimate_items` — сметы и их позиции

## 5. Что уже сделано ✅
1. Docker + PostgreSQL 16 + pgvector подняты
2. Схема БД создана (8 таблиц + GIN/IVFFLAT индексы)
3. Настройки: НДС 22%, валюта RUB
4. Приказ 838 распарсен → 1377 позиций в `industry_standards`
5. Эмбеддинги для стандартов сгенерированы (1377 шт.)
6. Keywords в `industry_standards` лемматизированы через pymorphy2
7. API `POST /api/products/upload` работает — загружает CSV + создает поставщика
8. Тестовые 10 товаров от "ИП МНВ" загружены с эмбеддингами
9. Базовый гибридный маппинг работает (30% vector + 70% keywords)

## 6. Текущая проблема с маппингом ❌
- Для товара "Набор таблиц Словарные слова" топ-1: "Лобзик учебный" (score 0.53)
- Максимальный match_score ≈ 0.53 (при пороге 0.5)
- Auto-mapped: 4 из 10, 6 требуют ручной проверки
- **Причины:** модель эмбеддингов плохо ловит контекст школьных пособий, общие слова ("набор", "комплект") создают шум, Jaccard слишком строгий.

## 7. План на следующий чат: Комбинированный подход
**Архитектура маппинга:**
1. **Этап 1 — Гибридный поиск:** Vector(30%) + Keywords(70%). Порог авто: 0.5
2. **Этап 2 — YandexGPT:** Если `match_score < 0.5` → отправляем в LLM. LLM получает товар + топ-5 кандидатов, возвращает лучший + пояснение + confidence
3. **Этап 3 — Ручная проверка:** Если LLM неуверен → помечаем для UI-валидации

**Что реализовать:**
- [ ] Интеграция YandexGPT API (получить ключ)
- [ ] Сервис `llm_mapping_service.py` с системным промптом
- [ ] Обновить `mapping_service.py`: fallback на LLM при low score
- [ ] Сохранять `match_reason` с пояснением от LLM
- [ ] Тестирование на 10 товарах

## 8. Ключевые договоренности
- НДС 22%, берется из `system_settings`
- Цены: `cost_price` = закупка, `retail_price` = РРЦ
- Поставщики: в каталоге показываем ВСЕ товары от ВСЕХ поставщиков
- Свойства товаров: JSONB в `products.properties`
- Срок поставки: в `supplier_products`
- Модель эмбеддингов: размерность 768, можно менять

## 9. Команды для работы
```powershell
docker-compose up -d
cd backend
uvicorn app.main:app --reload
docker exec -it catalog_db psql -U postgres -d catalog_db -c "\dt"