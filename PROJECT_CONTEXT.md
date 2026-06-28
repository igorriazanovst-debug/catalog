# Контекст проекта: Школьный каталог оборудования

## 1. О проекте
SaaS-каталог для автоматизации подбора школьного оборудования по Приказу Минпросвещения РФ №838 от 28.11.2024. 
**Основная задача:** поставщик загружает свои товары (CSV), система автоматически сопоставляет их с позициями стандарта (Приказ 838) и помогает формировать сметы для школ с учетом множества факторов (ОКПД2, КТРУ, сроки поставки, цены, характеристики).

## 2. Стек технологий
- **Frontend:** React 18 + Vite + TypeScript (SPA, `frontend/`), react-router
- **Backend:** Python 3.10, FastAPI, SQLAlchemy 2.0 (async), asyncpg
- **База данных:** PostgreSQL 16 + pgvector (Docker, образ `pgvector/pgvector:pg16`)
- **Эмбеддинги:** `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` (768 dim, multilingual)
- **Лемматизация:** pymorphy2
- **LLM (судья):** YandexGPT — полная модель `yandexgpt` (НЕ lite)
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
- `industry_standards` — 1888 позиций Приказа 838 + full_code + embedding(768) + keywords[]
- `products` — товары поставщиков + embedding(768) + properties(JSONB) + okpd2/ktru
- `suppliers` — поставщики (name, short_name, inn, contacts)
- `supplier_products` — связь M:N: цены, delivery_days, stock_quantity, supplier_sku
- `product_standard_mapping` — маппинг: match_score, match_reason, is_manual, rejected
- `system_settings` — key-value: vat_rate=0.22, currency=RUB, company_name
- `estimates` / `estimate_items` — сметы и их позиции

## 5. Архитектура маппинга (актуальная)
Пайплайн `MappingService.classify_product` (товар → позиция 838):

1. **Детерминированный роутер** (`_rule_match`, до LLM). Очевидные регулярные
   классы решаются правилами без модели. Сейчас закрыт самый частый —
   демонстрационные/учебные таблицы:
   - «Таблицы демонстрационные…» / «Комплект таблиц…» (не раздаточные, не
     электронные, не мебель для хранения) → `2.17` «Комплект демонстрационных
     учебных таблиц (по предметной области)»; если предмет — физика → `2.14.137`.
   - На размеченной выборке роутер срабатывает на ~60% товаров с точностью ~98%.
2. **Гибридный ретрив** (`map_product_to_standards`) для остального:
   пул кандидатов = вектор top-K (pgvector по эмбеддингу `name`) **∪**
   keyword-IDF top-K (леммы `name+description`, глушим только функциональные
   слова) **∪** все 22 «по предметной области» генерик-позиции.
   `recall@20(union)` ≈ 91% против ≈ 39% у чистого вектора. `top_k=20`.
3. **LLM-судья** (`llm_mapping_service.get_llm_mapping`): пул отдаётся YandexGPT.
   Кандидаты помечены областью/кабинетом («[По предметной области] …»,
   «[Кабинет химии] …»). Промпт матчит по ТИПУ изделия (таблицы≠карты≠модели≠
   пособия), знает конвенцию «по предметной области», русский≠иностранный,
   возвращает `null` если тип не совпал. **Требуется полная `yandexgpt`**
   (на `yandexgpt-lite` precision@pool падает ~84%→~58%).
4. **Решение:** `confidence` LLM ≥ порога → авто-маппинг, иначе → ручная проверка.

**Измеренная точность (выборка 99 товаров с экспертным эталоном):**
accuracy 81%, recall 91%, precision@pool 89% (роутер 98% на 60% выборки,
LLM 54% на трудном хвосте). Эмбеддинг-модель и IDF-индекс стандартов —
процессные синглтоны.

## 6. Что сделано ✅
1. Docker + PostgreSQL 16 + pgvector; схема БД (8 таблиц + индексы); НДС 22%, RUB.
2. Приказ 838 перепарсен корректно → **1888 позиций** (старый парсер терял 511,
   включая все 22 генерик-позиции «по предметной области»). Иерархия берётся из
   кода в xlsx; есть колонка `full_code`. Эмбеддинги и keywords сгенерированы.
3. API `POST /api/products/upload` (CSV + поставщик), `POST /api/mapping/auto-map`
   (`confidence_threshold`, `top_k`), `GET /api/mapping/candidates/{id}`.
4. Гибридный ретрив + детерминированный роутер + LLM-судья реализованы и измерены.

## 7. Что дальше (бэклог)
- Калибровка авто/ручная: уверенность LLM малоинформативна (почти всё 0.9–1.0) —
  нужен другой сигнал отбора на ручную (согласие каналов ретрива / явная метка
  «спорно»).
- Дотянуть recall (9% промахов: «веера», часть карт) и расширить роутер на другие
  регулярные типы (карты, раздаточные карточки).
- Retry/backoff для YandexGPT; батчинг/конкурентность LLM для тысяч товаров.
- UI ручной проверки; экспорт результатов (CSV/Excel, UTF-8 BOM).
- Почистить пару спорных меток эталона (ЭОР/карты, ошибочно помеченные таблицами).

## 8. Ключевые договоренности
- НДС 22%, берется из `system_settings`
- Цены: `cost_price` = закупка, `retail_price` = РРЦ
- Поставщики: в каталоге показываем ВСЕ товары от ВСЕХ поставщиков
- Свойства товаров: JSONB в `products.properties`
- Срок поставки: в `supplier_products`
- Модель эмбеддингов: размерность 768, можно менять

## 10. Фронтенд (SPA) — `frontend/`
React 18 + Vite + TypeScript. Раздаётся самим FastAPI под путём **`/app`**
(монтирование `StaticFiles` из `frontend/dist` в `main.py`; `SPAStaticFiles`
делает fallback на `index.html` для клиентских маршрутов). Один origin — без CORS.

Экраны:
- **Поставщики** (`/app/`) — список со счётчиками (товаров / авто / на проверке / без маппинга).
- **Загрузка прайса** (`/app/upload`) — форма поставщика + CSV (drag&drop, прогресс,
  построчные ошибки) → `POST /api/products/upload`.
- **Карточка поставщика** (`/app/supplier/:id`) — кнопки «Классифицировать новые»
  (`only_unmapped`) / «Переклассифицировать все» (`POST /api/mapping/auto-map?supplier_id=`),
  таблица товаров со статусом и позицией 838, фильтры, модальная проверка с
  кандидатами (approve / reassign / reject через `/api/review/*`).

Новые backend-эндпоинты под фронт: `GET /api/products/suppliers` (поставщики со
счётчиками), `GET /api/products?supplier_id=&status=` (товары с маппингом и ценой),
`auto_map_all_products(supplier_id, only_unmapped)` — фильтр по поставщику/новизне.

Сборка/деплой: `cd frontend && npm ci && npm run build`. Каталог `frontend/dist`
**коммитится** (сервер работает по «git pull + запуск», Node на нём нет). Подробности
в `frontend/README.md`. Открывается на сервере: `http://<host>:8001/app/`.

## 9. Команды для работы
```powershell
docker-compose up -d
cd backend
uvicorn app.main:app --reload
docker exec -it catalog_db psql -U postgres -d catalog_db -c "\dt"