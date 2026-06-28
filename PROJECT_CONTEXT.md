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
- **LLM (судья):** переключаемый провайдер — **YandexGPT** (полная модель
  `yandexgpt`, НЕ lite), **Groq** (OpenAI-совместимый, `llama-3.3-70b-versatile`;
  геоблок РФ → нужен `LLM_PROXY`) или **AITunnel** (OpenAI-совместимый агрегатор
  `api.aitunnel.ru`, доступен из РФ, по умолч. `gemini-2.5-flash`). Выбор
  провайдера — в UI перед классификацией.
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
- `products` — товары поставщиков + embedding(768) + properties(JSONB) + okpd2/ktru.
  **`sku` НЕ глобально уникален**: товары ведутся per-supplier (один артикул у разных
  поставщиков = разные товары/предложения, каждое классифицируется отдельно)
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
  построчные ошибки) → `POST /api/products/upload`. Пустой «Артикул» не ошибка:
  товару присваивается внутренний детерминированный артикул `AUTO-<hash(name|произв.)>`
  (повторная загрузка не плодит дубли); пустое «Наименование» — ошибка строки.
  Парсинг ячеек NaN-безопасный (раньше пустые поля превращались в строку `"nan"`).
- **Карточка поставщика** (`/app/supplier/:id`) — кнопки «Классифицировать новые»
  (`only_unmapped`) / «Переклассифицировать все» (`POST /api/mapping/auto-map?supplier_id=`),
  таблица товаров со статусом и позицией 838, фильтры, модальная проверка с
  кандидатами (approve / reassign / reject через `/api/review/*`).

Новые backend-эндпоинты под фронт: `GET /api/products/suppliers` (поставщики со
счётчиками), `GET /api/products?supplier_id=&status=` (товары с маппингом и ценой),
`auto_map_all_products(supplier_id, only_unmapped)` — фильтр по поставщику/новизне.

**Импорт per-supplier:** товар матчится В РАМКАХ поставщика (по `sku` среди
привязанных к нему), поэтому прайс нового поставщика заводит ВСЕ товары как
отдельные предложения, даже если артикул совпал с чужим. Затем их классифицируют
кнопкой «Классифицировать новые» (`only_unmapped`).

**Фоновые задачи + прогресс.** Импорт и классификация — длинные операции
(тысячи товаров, LLM по секунде), поэтому выполняются в фоне, а не синхронным
HTTP (иначе таймаут шлюза → 502). `POST /api/products/upload` и
`POST /api/mapping/auto-map` сразу возвращают `{job_id}`; UI опрашивает
`GET /api/jobs/{id}` (статус running/done/error, processed/total, счётчики,
message, error, result) и рисует прогресс-бар. Реестр задач — in-memory
(`app/services/jobs.py`, один процесс uvicorn). Тяжёлый `encode` эмбеддингов
вынесен в поток (`asyncio.to_thread`), чтобы опрос статуса не вис.
**Обрыв при сбое GPT:** если LLM даёт `max_consecutive_llm_errors` (=100)
ошибок подряд, классификация завершается `status=error` с понятным сообщением
(проверить ключи/квоту и запустить снова). Роутер (rule) GPT не трогает и серию
не сбивает; честный ответ модели серию обнуляет.

**Провайдеры LLM (yandex/groq).** `app/services/llm_mapping_service.py` —
диспетчер: общий промпт/парсер/ретраи, разные эндпоинты. `GET /api/mapping/providers`
отдаёт список с признаком `configured` (есть ли ключ); UI на странице поставщика
показывает селектор перед кнопками классификации, ненастроенные — задизейблены.
`POST /api/mapping/auto-map?provider=groq|yandex`. Ключи в `backend/.env`:
`GROQ_API_KEY=…` (+ опц. `GROQ_MODEL=…`), `AITUNNEL_API_KEY=…` (+ опц.
`AITUNNEL_MODEL=…`, `AITUNNEL_BASE_URL=…`), `LLM_PROVIDER=yandex|groq|aitunnel` —
провайдер по умолчанию. После добавления ключа — перезапустить сервер.
Новый OpenAI-совместимый провайдер добавляется ~3 строками: ключ в config,
ветка в `provider_configured`, обёртка через `_call_openai_compatible`. UI
подхватывает его автоматически (селектор рендерит `/api/mapping/providers`).

**Geo-блок Groq из РФ:** с российского IP Groq отдаёт `403 {"error":{"message":
"Forbidden"}}` на любой запрос. Решение — прокси ТОЛЬКО для LLM-запросов:
`LLM_PROXY=http://user:pass@host:port` (или `socks5://…`, нужен `httpx[socks]`) в
`backend/.env`. Применяется лишь к вызовам провайдеров (Yandex и загрузка моделей —
напрямую). Если задан — в `uvicorn.log` при старте строка `[startup] LLM-запросы
через прокси: …`. `.env` — строго UTF-8 (иначе сервер не стартует, см. config.py).

Миграция боевой БД (одноразово, снимает старый глобальный UNIQUE с `products.sku`):
`python scripts/migrate_drop_sku_unique.py --db-url "$DBURL"`.
Поставщика, импортированного старой логикой, пересобрать:
`python scripts/reset_supplier.py --supplier-id N --db-url "$DBURL"` (можно с
`--dry-run`), затем заново загрузить его прайс.
Полный сброс каталога начисто (товары/поставщики/маппинги; справочник 838
сохраняется): `python scripts/reset_catalog.py --db-url "$DBURL" --yes`
(без `--yes` — только показывает, что будет удалено).

Перезапуск API после `git pull` (код/статика подхватываются только при старте):
`bash backend/scripts/restart_server.sh` (гасит старый uvicorn, ждёт освобождения
порта, стартует заново, проверяет лог). Импорт считает эмбеддинги новых товаров
одним батчем (`encode(names, batch_size=64)`) — иначе тысячи товаров кодируются
по одному и загрузка занимает минуты.

Сборка/деплой: `cd frontend && npm ci && npm run build`. Каталог `frontend/dist`
**коммитится** (сервер работает по «git pull + запуск», Node на нём нет). Подробности
в `frontend/README.md`. Открывается на сервере: `http://<host>:8001/app/`.

## 9. Команды для работы
```powershell
docker-compose up -d
cd backend
uvicorn app.main:app --reload
docker exec -it catalog_db psql -U postgres -d catalog_db -c "\dt"