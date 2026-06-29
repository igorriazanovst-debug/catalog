# HANDOFF — резюме для следующей сессии

Дата: 2026-06-29. Документ самодостаточный: следующая сессия должна стартовать
без разведки и лишних вопросов.

**Цель следующей сессии: ВХОДЯЩИЕ СМЕТЫ** (см. раздел 9 — там и постановка, и
открытые вопросы, которые надо задать пользователю в начале).

---

## 0. КАК МЫ РАБОТАЕМ (важно!)

Среда ассистента — **эфемерный контейнер**, отдельный от боевого сервера.
Прямого доступа к серверу/БД у ассистента НЕТ. Цикл:

1. Ассистент пишет/правит код → **коммитит** → **пушит** в ветку
   `claude/handoff-review-7z5w0i` (origin = GitHub `igorriazanovst-debug/catalog`).
2. Пользователь на сервере `git pull`, перезапускает сервер, гоняет скрипты,
   присылает вывод/логи в чат.
3. Ассистент читает вывод, правит код. И так по кругу.

Git:
- Рабочая ветка: **`claude/handoff-review-7z5w0i`** (НЕ main). Эта ветка уже
  включает всю прошлую работу (была fast-forward с `claude/charming-maxwell-11bdtk`)
  плюс весь фронтенд/джобы/провайдеры этой сессии.
- Автор коммитов: `Claude <noreply@anthropic.com>` (требуется; в контейнере
  коммитим через `git -c user.name="Claude" -c user.email="noreply@anthropic.com"`).
- Пуш: `git push -u origin claude/handoff-review-7z5w0i`.
- **Фронт коммитим вместе со сборкой `frontend/dist`** (на сервере нет Node —
  он раздаёт уже собранный SPA). После любой правки фронта: `cd frontend &&
  npm run build`, затем коммит вместе с `dist`.

---

## 1. ЧТО ЗА ПРОЕКТ

SaaS-каталог школьного оборудования. Поставщик грузит прайс (CSV) → система
авто-сопоставляет товары с позициями **Приказа Минпросвещения РФ №838** →
помогает формировать **сметы** для школ. Репозиторий: backend (FastAPI) +
frontend (React SPA).

---

## 2. БОЕВОЙ СЕРВЕР (точные факты)

- Путь: **`/opt/catalog`**, backend `/opt/catalog/backend`, venv там же, Python **3.10**, Ubuntu.
- **PostgreSQL 16 + pgvector в Docker на порту 5433** (НЕ 5432). Креды в
  `/opt/catalog/backend/.env` (`database_url=postgresql://...@localhost:5433/catalog_db`).
- **`.env` строго в UTF-8** и **без русских комментариев** — иначе сервер не
  стартует (pydantic читает .env как UTF-8). Теперь падает с понятным сообщением
  (`config.py` проверяет заранее). Если поломали кодировку — чистка:
  `python3 -c "p='/opt/catalog/backend/.env'; b=open(p,'rb').read(); open(p+'.bak','wb').write(b); open(p,'w',encoding='utf-8').write(b.decode('utf-8','ignore'))"`.
- **API-сервер:** uvicorn на `0.0.0.0:8001`, запускается скриптом
  **`bash backend/scripts/restart_server.sh`** (гасит старый процесс, ждёт
  освобождения порта, ждёт реального ответа HTTP до 90с, печатает хвост лога при
  сбое). Лог: `/opt/catalog/uvicorn.log`. **Код/статика подхватываются только при
  старте — после `git pull` ОБЯЗАТЕЛЬНО перезапуск.**
- Перед сервером есть **обратный прокси (nginx)** — при упавшем uvicorn снаружи
  отдаёт **502**. Снаружи: `http://31.192.110.121:8001`.
- **UI:** SPA на **`http://31.192.110.121:8001/app/`**; служебная страница ручной
  проверки (старая, самодостаточный HTML) — **`/api/review`**.
- Данные на сервере (НЕ в git): прайсы поставщиков в `/opt/catalog/data/input/...`,
  справочник 838 — `838.xlsx`.

Универсальный сниппет для серверных скриптов (строка БД из .env, схема под asyncpg):
```bash
cd /opt/catalog/backend && source venv/bin/activate
DBURL="$(grep -E '^database_url=' .env | cut -d= -f2- | tr -d '\"' | sed 's#^postgresql://#postgresql+asyncpg://#')"
# далее: python scripts/<script>.py --db-url "$DBURL"
```

---

## 3. СОСТОЯНИЕ БД (на конец сессии)

- `industry_standards`: **1888 позиций** Приказа 838 (full_code, embedding(768),
  keywords[]). СТАБИЛЬНО, не трогаем; `reset_catalog.py` его сохраняет.
- `products` / `supplier_products` / `product_standard_mapping`: **в процессе
  перезаливки**. В этой сессии каталог чистили `reset_catalog.py` (всё в 0),
  потом пользователь заново грузил прайс (~2188 строк) и пытался классифицировать.
  Классификация упёрлась в недоступность LLM (Yandex — проблемы доступа; Groq —
  геоблок РФ 403). Поэтому маппинги, скорее всего, пустые/частичные — уточнить в
  начале сессии: `GET /api/products/suppliers` или SQL `SELECT count(*) FROM products;`.

---

## 4. АРХИТЕКТУРА МАППИНГА (товар → позиция 838)

Точка входа: `MappingService.classify_product(product_id, provider=...)` в
`backend/app/services/mapping_service.py`. Пайплайн:
1. **Детерминированный роутер** (`_rule_match`, без LLM): демонстрационные таблицы
   → код `2.17` (физика → `2.14.137`). ~60% товаров, точность ~98%. GPT не трогает.
2. **Гибридный ретрив** (`map_product_to_standards`, top_k=20): пул = вектор
   top-K (pgvector по эмбеддингу name) ∪ keyword-IDF top-K ∪ все 22 «по предметной
   области» генерик-позиции. recall@20 ≈ 91%.
3. **LLM-судья** (`llm_mapping_service.get_llm_mapping`, переключаемый провайдер):
   выбирает один стандарт или null. Промпт матчит по ТИПУ изделия.
4. **Калибровка авто/ручная** по согласию каналов ретрива (уверенность LLM
   неинформативна): подтвердили И вектор, И keyword → авто; иначе → ручная.
Измеренная (на Yandex, выборка 99): accuracy ~82%, авто-канал ~99% точности.
Качество Groq/Gemini как судьи НЕ измерялось.

---

## 5. LLM-ПРОВАЙДЕРЫ (судья) — переключаемые

Реестр и вызовы — `backend/app/services/llm_mapping_service.py`.
`PROVIDERS = {yandex, groq, aitunnel}`. Выбор провайдера — в UI ПЕРЕД
классификацией (селектор на странице поставщика), либо `?provider=` в API.
Ненастроенные (нет ключа) в селекторе задизейблены.

Ключи/настройки в `backend/.env`:
- **YandexGPT:** `YANDEX_GPT_API_KEY`, `YANDEX_GPT_FOLDER_ID`,
  `YANDEX_GPT_MODEL_URI=gpt://<folder>/yandexgpt/latest` (полная, НЕ lite).
  Статус на конец сессии: у пользователя проблемы с доступом, чинит сам.
- **Groq:** `GROQ_API_KEY` (+ `GROQ_MODEL`, дефолт `llama-3.3-70b-versatile`).
  **Геоблокирует РФ** → `403 {"error":{"message":"Forbidden"}}`. Работает только
  через прокси: `LLM_PROXY=http://user:pass@host:port` или `socks5://...`
  (для socks нужен `httpx[socks]`, уже в requirements). Прокси применяется ТОЛЬКО
  к LLM-запросам. Пользователь от прокси пока отказался.
- **AITunnel:** `AITUNNEL_API_KEY` (+ `AITUNNEL_MODEL` дефолт `gemini-2.5-flash`,
  `AITUNNEL_BASE_URL` дефолт `https://api.aitunnel.ru/v1`). OpenAI-совместимый
  агрегатор, **доступен из РФ напрямую**. Добавлен последним — это текущий рабочий
  путь для классификации. Платный (оплата за токены).
- `LLM_PROVIDER=yandex|groq|aitunnel` — провайдер по умолчанию.

Добавить нового OpenAI-совместимого провайдера = ~3 правки: ключ в `config.py`,
ветка в `provider_configured`, обёртка через `_call_openai_compatible(...)`. UI
подхватит автоматически.

Обрыв при сбое: если LLM даёт **100 ошибок подряд** (`max_consecutive_llm_errors`),
классификация завершается `status=error` с понятным текстом (тело ответа провайдера
теперь видно и в UI, и в логе). Роутер серию не сбивает; честный ответ модели её
обнуляет.

---

## 6. ФОНОВЫЕ ЗАДАЧИ (импорт и классификация)

Обе операции — длинные (тысячи товаров, LLM), поэтому НЕ синхронные (иначе таймаут
шлюза → 502). Реестр — `backend/app/services/jobs.py` (in-memory, один процесс
uvicorn). Запуск: `run_job(job, body)` через `asyncio.create_task` со своей
async-сессией БД.
- `POST /api/products/upload` и `POST /api/mapping/auto-map` сразу возвращают
  `{job_id}` (upload — ещё `supplier_id`, `supplier_name`).
- `GET /api/jobs/{id}` → `{status: running|done|error, processed, total, counters,
  message, error, result, elapsed}`. UI опрашивает (`pollJob`) и рисует прогресс.
- Тяжёлый `encode` эмбеддингов вынесен в поток (`asyncio.to_thread`), модель —
  ленивый синглтон, чтобы опрос статуса не вис.

---

## 7. КАРТА ФАЙЛОВ

### Backend (`backend/app/`)
- `main.py` — FastAPI, роутеры (products, mapping, review, jobs), монтирование SPA
  на `/app` (`SPAStaticFiles` с fallback на index.html), `print` статуса SPA в лог.
- `core/config.py` — Settings (читает `.env`); **guard на не-UTF-8 .env**; ключи
  всех провайдеров + `LLM_PROXY`.
- `core/database.py` — async engine + `async_session` (используется фоновыми
  задачами), `get_db` (DI). URL из `settings.database_url`, схема под asyncpg.
- `services/mapping_service.py` — ядро: роутер + гибридный ретрив +
  `classify_product(provider=)` + `auto_map_all_products(supplier_id, only_unmapped,
  provider, progress, max_consecutive_llm_errors)`; `LLMUnavailableError`.
- `services/llm_mapping_service.py` — диспетчер провайдеров, общий
  `_call_openai_compatible`, `_call_yandex`, `_post_with_retry` (ретраи + тело
  ошибки в reason), `providers_status()`, `provider_configured()`, `_make_client()`
  (httpx с опц. прокси).
- `services/product_service.py` — импорт CSV: `_cell` (NaN-safe), `_internal_sku`
  (AUTO-артикул при пустом «Артикул»), **матчинг per-supplier**, batch-эмбеддинги
  через to_thread, `progress`-callback; ленивый синглтон модели (`get_embedding_model`).
- `services/jobs.py` — `Job`, `JobManager` (`jobs`), `run_job`.
- `services/estimate_parser.py` — **предварительный разбор входящей сметы (xlsx),
  БЕЗ LLM и БЕЗ БД**. `parse_estimate(path)` / `parse_worksheet(ws)`. Эвристика:
  ищет строку-шапку (по числу распознанных полей), сопоставляет колонки полям
  (`num/name/code/quantity/unit/price/total/char_name/char_value/char_unit`),
  группирует строки в позиции (новая позиция = заполнен №/наименование; ниже —
  её характеристики), вытаскивает код КТРУ/ОКПД2 из колонки ИЛИ из текста
  наименования («Код: …»), склеивает значение характеристики из колонок
  значение/оператор/Min/Max. Возвращает нормализованные позиции + диагностику
  (какая строка-шапка, как легли колонки, предупреждения). Это «контракт» для
  следующих шагов (позиция→838→товары→цены).
- `services/estimate_service.py` — **сопоставление позиций сметы с каталогом,
  БЕЗ LLM, read-only (в БД не пишет)**. `EstimateMatcher(db, price_basis, top_k)`:
  `match_line` / `match_estimate(parsed)`. Шаг 1 (позиция→838): по коду —
  КТРУ→`industry_standards.ktru_code`, иначе ОКПД2→`okpd2_code`; если по коду
  пусто — текстовый фоллбэк (гибридный ретрив через `MappingService`). Шаг 2
  (838→товар→цена): товары через `product_standard_mapping` (NOT rejected) +
  `supplier_products`, выбор — самое дешёвое доступное предложение (по
  retail_price; `--price cost` для себестоимости). Итог + НДС из
  `system_settings.vat_rate`. `db_code_availability()` — диагностика (заполнены
  ли коды/маппинги). **Поставщики пока из ВСЕХ** (фильтра нет). Шаг позиция→838:
  код→текстовый ретрив→роутер→**LLM-судья** (опц., `use_llm`/`provider`,
  переиспользует `get_llm_mapping`); без LLM — топ ретрива. Текстовый пул —
  объединение ретрива по имени (точность@1) и по имя+характеристики (полнота).
  **Разложение наборов** (`decompose`, требует `use_llm`): `get_llm_decomposition`
  решает «один товар или набор»; набор → подбираем каждое вложение отдельно
  (1 строка→N позиций), цены суммируем (`_match_bundle`/`_match_single`).
- `services/llm_mapping_service.py` (дополнено) — провайдерная машинерия вынесена
  в `_dispatch(system_prompt,user_prompt,provider)` (судья и декомпозиция делят
  ретраи/прокси). `get_llm_decomposition(line)` → `{is_bundle, items:[{name,
  quantity_per_set}]}` по `DECOMPOSITION_SYSTEM_PROMPT`.
- `api/endpoints/products.py` — `POST /api/products/upload` (фон),
  `GET /api/products/suppliers` (счётчики), `GET /api/products?supplier_id=&status=`
  (товары с маппингом и ценой).
- `api/endpoints/mapping.py` — `GET /api/mapping/providers`,
  `POST /api/mapping/auto-map` (фон, `provider`), `GET /api/mapping/candidates/{id}`.
- `api/endpoints/review.py` — ручная проверка: `/api/review` (HTML), `/stats`,
  `/queue`, `/product/{id}/candidates`, `/mapping/{id}/approve|reassign|reject`.
- `api/endpoints/jobs.py` — `GET /api/jobs/{id}`.

### Frontend (`frontend/`, React 18 + Vite + TS, раздаётся под `/app`)
- `src/api.ts` — типы + клиент: upload/listSuppliers/listProducts/autoMap/
  listProviders/pollJob/getJob/review-экшены. **Все пути относительные.**
- `src/App.tsx` — роуты: `/` (поставщики), `/upload`, `/supplier/:id`.
- `src/pages/SuppliersPage.tsx` — список поставщиков со счётчиками.
- `src/pages/UploadPage.tsx` — форма + CSV (drag&drop), фоновый импорт с прогрессом.
- `src/pages/SupplierDetailPage.tsx` — **селектор LLM-провайдера** + «Классифицировать
  новые»/«Переклассифицировать все» (фон + прогресс + ошибки), таблица товаров со
  статусом/позицией 838/ценой, фильтры, модальная проверка.
- `src/components/JobProgress.tsx` — прогресс-бар + счётчики задачи.
- `src/components/ReviewPanel.tsx` — модалка проверки (кандидаты, approve/reassign/reject).
- `src/styles.css` — стили. `frontend/dist/` — собранный SPA (коммитится!).

### Скрипты (`backend/scripts/`, все берут `--db-url`)
- `restart_server.sh` — перезапуск uvicorn (ждёт готовности HTTP).
- `reset_catalog.py --yes` — полный сброс товаров/поставщиков/маппингов (838
  сохраняется); без `--yes` — dry-run.
- `reset_supplier.py --supplier-id N [--dry-run]` — сброс одного поставщика.
- `parse_estimate.py <файл.xlsx> [...] [--json out]` — **предварительный разбор
  сметы**: печатает распознанную шапку/колонки/позиции (использует
  `app/services/estimate_parser.py`). Инструмент проверки на реальных сметах.
- `match_estimate.py <файл.xlsx> [...] --db-url ... [--price retail|cost] [--top-k N] [--llm [provider]]`
  — **подбор товаров под смету** (разбор + сопоставление, read-only). Печатает
  доступность в БД, по каждой позиции: стандарт 838 (метод ktru/okpd2/rule/
  text+llm/text), причину, выбранный товар/поставщика/цену, альтернативы, итог с
  НДС. `--llm` включает LLM-судью (опц. провайдер: yandex|groq|aitunnel).
  `--decompose` (с `--llm`) раскладывает строки-наборы на вложения и подбирает
  каждое отдельно (печатает набор и его вложения с подбором/ценой).
- `inspect_columns.py <файл.xlsx|csv> [--sheet N] [--rows N]` — **инспектор
  структуры источника**: по каждой колонке печатает заголовок, примеры значений и
  сколько ячеек похожи на КТРУ/ОКПД2 (ничего не меняет). Нужен, чтобы увидеть, где
  в `838.xlsx`/прайсе лежат коды, и написать корректный скрипт их проставления.
- `migrate_drop_sku_unique.py` — снять глобальный UNIQUE с `products.sku` (товары
  стали per-supplier). Идемпотентно, одноразово на боевой БД.
- `import_products.py`, `run_automap.py`, `eval_pipeline.py`, `parse_order_838.py`,
  `import_standards.py`, `generate_embeddings.py`, `regenerate_product_embeddings.py`,
  `export_standards.py`, `diagnose_mapping.py`, `simulate_strategies.py`,
  `recall_experiment.py`, `llm_rerank_eval.py`, `make_review_sheet.py`,
  `score_review.py`, `regenerate_standard_keywords.py` — оффлайн-инструменты.

---

## 8. СХЕМА БД (полная, `database/init.sql`)

- **`industry_standards`** — 1888 позиций 838: id, industry_code, section_code,
  subsection_code, section_name, subsection_name, item_name, equipment_type,
  keywords[], okpd2_code, ktru_code, embedding(768). (+ есть колонка `full_code`,
  добавлена скриптами — иерархический код вида `2.14.137`.)
- **`products`** — id, **sku (НЕ уникален глобально!)**, name, description, unit,
  manufacturer, vat_included, okpd2_code, ktru_code, properties(JSONB), embedding(768).
  Товары ведутся **per-supplier**: один артикул у разных поставщиков = разные товары.
- **`suppliers`** — id, name, short_name, inn(UNIQUE), contact_person, phone, email,
  is_active.
- **`supplier_products`** — связь M:N: id, supplier_id, product_id, supplier_sku,
  cost_price NUMERIC(15,2), retail_price NUMERIC(15,2), delivery_days,
  stock_quantity, is_available. **UNIQUE(supplier_id, product_id).**
- **`product_standard_mapping`** — товар↔стандарт: id, product_id, standard_id,
  match_score, match_reason, is_manual, rejected. **UNIQUE(product_id, standard_id).**
  Статус: авто (NOT is_manual, NOT rejected) / на проверку (is_manual) / отклонён (rejected).
- **`system_settings`** — key-value: `vat_rate=0.22`, `currency=RUB`,
  `company_name=Школьный каталог`.
- **`estimates`** — id, name, description, total_amount NUMERIC(15,2), created_at.
  **ПОКА ПУСТАЯ, кода для смет нет.**
- **`estimate_items`** — id, estimate_id FK→estimates (ON DELETE CASCADE),
  standard_id FK→industry_standards (SET NULL), product_id FK→products (SET NULL),
  supplier_id FK→suppliers (SET NULL), quantity NUMERIC(10,2), unit_price
  NUMERIC(15,2), total_price NUMERIC(15,2), created_at. **ПОКА ПУСТАЯ.**

---

## 9. ЦЕЛЬ СЛЕДУЮЩЕЙ СЕССИИ: ВХОДЯЩИЕ СМЕТЫ

Задача — работа с входящими сметами (школа/заказчик присылает потребность, система
помогает её закрыть товарами из каталога с ценами поставщиков). Таблицы
`estimates`/`estimate_items` в схеме ЕСТЬ, но **кода и UI под сметы пока нет** —
это с нуля.

### Что уже можно опереть (готовая основа)
- Связь «позиция 838 → товары → цены»: по `standard_id` найти товары можно через
  `product_standard_mapping` (WHERE standard_id=X AND NOT rejected), цены/поставщиков
  — через `supplier_products` (cost_price/retail_price), НДС — `system_settings.vat_rate`.
- Тот же гибридный ретрив + LLM-судья, что для товаров, применим к строкам сметы
  (если строки — свободный текст, а не коды 838): сопоставить строку сметы → позицию
  838 (и/или сразу → товары).
- Фоновые задачи (`jobs.py`) и провайдеры LLM — переиспользуемы.

### РЕШЕНИЯ ПОЛЬЗОВАТЕЛЯ (2026-06-29)
- **Критерий подбора:** СНАЧАЛА качество совпадения, ПОТОМ цена. Цена — по
  **себестоимости (cost_price)**, не РРЦ. (Реализовано: в `_offers_for_standards`
  сортировка `is_manual ASC, match_score DESC, cost_price ASC`; `price_basis="cost"`.)
- **Стандарт 838 для позиции:** код приоритетно (КТРУ→ОКПД2), текст — фоллбэк (вар.3).
- **Поставщики:** пока подбираем из ВСЕХ; выбор поставщиков добавим позже.
- **Анализ смет:** сначала БЕЗ LLM (только правила/ретрив), LLM подключаем позже.
- **Формат сметы:** «не определена по колонкам» → нужен предварительный разбор
  (сделан, см. ниже). Реальные сметы — шаблоны 44-ФЗ «Описание объекта закупки».
- **Следующий шаг (приоритет):** заполнить коды КТРУ/ОКПД2 в каталоге (см. ниже).

### СДЕЛАНО: предварительный разбор сметы (этот шаг)
`estimate_parser.py` + `scripts/parse_estimate.py`. Проверено на ДВУХ реальных
сметах (разные шаблоны): обе разобраны — позиция, наименование, код КТРУ/ОКПД2
(в т.ч. зашитый в текст наименования), количество/ед.изм., список характеристик.
Дальше: сопоставление позиции → 838 (по коду напрямую, иначе гибридный ретрив)
→ товары/цены по `product_standard_mapping`+`supplier_products` (мин. цена) →
запись в `estimates`/`estimate_items`. Парсер коды, БД и LLM НЕ трогает.

### СДЕЛАНО: сопоставление сметы с каталогом (этап правил, без LLM)
`estimate_service.py` (`EstimateMatcher`) + `scripts/match_estimate.py`. Стратегия
подтверждена пользователем — **код приоритетно, текст — фоллбэк** (вариант 3).
ВАЖНЫЙ нюанс БД: импорт 838 НЕ проставлял `industry_standards.ktru_code/okpd2_code`,
а импорт товаров — `products.ktru_code`. Значит код-матч, скорее всего, пустой и
сработает текстовый фоллбэк. `match_estimate.py` сразу печатает доступность в БД
(сколько стандартов/товаров с кодами, сколько маппингов) — по ней видно, что
матчится. Чтобы включить точный код-матч, нужен отдельный шаг: проставить КТРУ/ОКПД2
в `industry_standards` (и/или в товары) — НЕ сделано, кандидат на следующий шаг.

Проверено на боевой БД (1888 стандартов, 2401 товар, 2262 маппинга): коды у 838 и
товаров действительно ПУСТЫЕ → весь матч идёт текстом. Текстовый ретрив выбирает
стандарт по НАИМЕНОВАНИЮ позиции (`_line_query`: имя — главный сигнал;
характеристики добавляются только если имя короткое — иначе содержимое набора,
напр. «репродукции картин», уводит ретрив на стандарт вложения, а не самого
набора). Это снимает часть ошибок; остальное — за LLM-судьёй (правильный кандидат,
как правило, уже в пуле, его надо лишь выбрать).

**Проверить на сервере (read-only, ничего не пишет):**
```bash
cd /opt/catalog/backend && source venv/bin/activate
DBURL="$(grep -E '^database_url=' .env | cut -d= -f2- | tr -d '\"' | sed 's#^postgresql://#postgresql+asyncpg://#')"
python scripts/match_estimate.py ../data/input/smeta-1.xlsx ../data/input/smeta-2.xlsx --db-url "$DBURL"
```
### РЕШЕНИЕ ПО КОДАМ (2026-06-29): код-матч НЕ делаем
Кодов КТРУ/ОКПД2 нет ни в `838.xlsx`, ни в прайсах (их первоисточник — федеральный
ЕИС, и они не выводятся из наших данных). ОКПД2-префикс (`32.99.53.130`) одинаков
почти у всего учебного оборудования — для матча бесполезен; присвоить полный КТРУ
каждой позиции 838/товару = отдельная классификация. Поэтому по решению пользователя
**идём текст + LLM-судья** (код-ветка в коде остаётся — сработает, если коды когда-то
появятся). `inspect_columns.py` оставлен как инструмент на будущее.

### СДЕЛАНО: LLM-судья для матча сметы (опционально)
`match_estimate.py --llm [provider]` включает LLM-судью на текстовом пуле
(переиспользует `llm_mapping_service.get_llm_mapping` и его промпт). Должен
исправлять промахи ретрива (напр. смета-2: верный #2054 в пуле, но не первый).
Качество судьи на сметах НЕ измерено — проверить на сервере с рабочим провайдером
(из РФ — AITunnel: `--llm aitunnel`).

### СДЕЛАНО: разложение наборов на вложения (решение пользователя)
Прогон с `--llm aitunnel` показал: смета-1 (цельный товар-прибор) матчится точно;
смета-2 («Набор по закреплению изучаемых тем») — это ЛОТ из разнородных вложений
(портреты+репродукции+таблицы+прибор), и судья ошибочно цеплялся за одно вложение.
Решение пользователя — **разлагать наборы**. Реализовано через LLM-декомпозицию
(`--decompose`): цельный товар остаётся как есть, набор разбивается на вложения,
каждое подбирается отдельно, цены суммируются.

ВАЖНО — ГЕЙТ перед декомпозицией (`BUNDLE_GATE_SIMILARITY=0.85`): без него LLM
разнёс смету-1 (цельный прибор) на 14 «деталей» (соленоид/катушки/нить/паспорт…).
Гейт: если наименование строки уверенно совпадает с позицией 838 (вектор топ-
кандидата по ИМЕНИ >= 0.85) — это цельный товар (характеристики = его части),
НЕ разлагаем. На реальных сметах: смета-1 vec≈1.0 (цельный), смета-2 vec≈0.69
(набор). Порог — эвристика, калибруется. Проверить на сервере:
`python scripts/match_estimate.py ../data/input/smeta-2.xlsx --db-url "$DBURL" --llm aitunnel --decompose`
— ожидаем, что смета-2 разложится на вложения и каждое получит товар/цену.

### ПОТОМ
Запись в `estimates`/`estimate_items` (вложения набора — отдельные `estimate_items`),
эндпоинты `POST /api/estimates/upload` (фон) + `GET /api/estimates/{id}` + выбор
товара/поставщика + экспорт; UI-раздел смет; фильтр поставщиков.

### ОТКРЫТЫЕ ВОПРОСЫ — задать пользователю в начале (не угадывать!)
1. **Формат входящей сметы:** Excel/CSV? Какие колонки? Строки ссылаются на коды
   Приказа 838 (`full_code`) / наименования позиций 838 / свободный текст
   потребности? Есть ли количества?
2. **Что значит «обработать смету»:** подобрать под каждую строку товар(ы) из
   каталога с ценой? Выбрать одного поставщика (самого дешёвого по retail_price?
   по cost_price? по сроку delivery_days/наличию stock_quantity?) или показать все
   варианты для выбора?
3. **Результат:** заполненная смета с позициями/поставщиками/ценами/итогами и НДС?
   Экспорт обратно в Excel/CSV (UTF-8 BOM)? Только просмотр в UI?
4. **UI:** новый раздел в SPA (загрузка сметы → подбор → проверка/правка позиций →
   итог/экспорт)? Нужна ли ручная корректировка подобранных товаров (как в /review)?
5. **Несматченные строки:** что делать со строками сметы, под которые в каталоге нет
   товара (нет маппинга на эту позицию 838)?

### Вероятный план реализации (уточнить после ответов)
- Backend: сервис `estimate_service` (парсинг входящего файла → строки;
  сопоставление строка→standard_id [по коду или ретрив+LLM]; подбор товаров+цен по
  standard_id; запись в `estimates`/`estimate_items`; пересчёт total_amount/НДС).
- Endpoints: `POST /api/estimates/upload` (фон, как товары), `GET /api/estimates`,
  `GET /api/estimates/{id}` (позиции с вариантами товаров/цен),
  `POST /api/estimates/{id}/items/{item_id}/choose` (выбор товара/поставщика),
  `GET /api/estimates/{id}/export` (Excel/CSV).
- Frontend: страницы списка смет, загрузки, детальной сметы с подбором/правкой/итогом.
- Переиспользовать: фоновые задачи, прогресс, LLM-провайдеры, гибридный ретрив.

---

## 10. ИЗВЕСТНЫЕ НЮАНСЫ / ДОЛГИ

- После `git pull` на сервере — ВСЕГДА `bash backend/scripts/restart_server.sh`
  (иначе старый процесс = старый код, и снаружи возможен 502).
- `.env` — только UTF-8, без кириллических комментариев.
- Groq из РФ не работает без `LLM_PROXY` (геоблок 403). Текущий рабочий провайдер
  из РФ — AITunnel; Yandex — когда пользователь починит доступ.
- Точность судьи на Groq/AITunnel(Gemini) не измерена — при желании
  параметризовать `eval_pipeline.py` по `--provider` и сравнить на
  `logs/review_labeled_newids.csv` (99 размеченных товаров).
- Импорт считает эмбеддинг на КАЖДЫЙ новый товар (раньше пустые артикулы
  схлопывались в один «nan» — это был баг, починен). На тысячах товаров векторизация
  занимает минуты — батчится и идёт в потоке, прогресс виден.
- `config.py` поля через `os.getenv` — работает, но «не чисто» (можно отрефакторить
  на чистый pydantic-settings).
- UI на скрипте/`nohup` — не systemd (не переживает ребут сервера). Долг.
- Экспорт результатов маппинга/смет в Excel — пока нет.

---

## 11. ЧАСТЫЕ КОМАНДЫ (сервер)

```bash
# подтянуть + перезапустить (после любого git pull)
cd /opt/catalog && git pull origin claude/handoff-review-7z5w0i
bash backend/scripts/restart_server.sh

# строка БД для скриптов
cd /opt/catalog/backend && source venv/bin/activate
DBURL="$(grep -E '^database_url=' .env | cut -d= -f2- | tr -d '\"' | sed 's#^postgresql://#postgresql+asyncpg://#')"

# полный сброс каталога начисто (838 сохраняется)
python scripts/reset_catalog.py --db-url "$DBURL" --yes

# проверить лог сервера
tail -n 60 /opt/catalog/uvicorn.log
```

UI: `http://31.192.110.121:8001/app/` (сметы будем добавлять туда же).
