# HANDOFF — резюме для следующей сессии

Документ максимально подробный: чтобы новая сессия стартовала без вопросов.
Дата: 2026-06-28.

---

## 0. КАК МЫ РАБОТАЕМ (важно!)

Среда ассистента — **эфемерный контейнер**, отдельный от боевого сервера.
Прямого доступа к серверу/БД у ассистента НЕТ. Поэтому цикл такой:

1. Ассистент пишет/правит код локально → **коммитит** → **пушит** в ветку
   `claude/charming-maxwell-11bdtk` (origin = GitHub `igorriazanovst-debug/catalog`).
2. Пользователь на сервере делает `git pull`, **запускает** скрипт/команду,
   результат (лог/CSV) при необходимости **коммитит и пушит** обратно.
3. Ассистент делает `git fetch` + `git reset --hard origin/...`, **читает** лог,
   делает выводы, правит код. И так по кругу.

Подробности git:
- Рабочая ветка: **`claude/charming-maxwell-11bdtk`** (НЕ main).
- Коммиты автор: `Claude <noreply@anthropic.com>` (хук это проверяет).
- Пуш из контейнера: обычный `git push -u origin claude/charming-maxwell-11bdtk`.
  В начале сессии пуш не работал (403), пока пользователь не выдал
  GitHub-интеграции право **Contents: Read and write** — теперь работает.
- На сервере git пушит по HTTPS с **personal access token** в remote URL
  (fine-grained, repo catalog, Contents RW). Если на сервере снова 403 —
  проверить срок токена.
- На сервере у некоторых файлов бывают **локальные правки** (хардкод порта).
  Если `git pull` ругается — `git checkout -- <file>` (наши версии берут
  `--db-url`, локальные хаки не нужны) и снова pull.

Универсальный сниппет для всех серверных команд (достаёт строку БД из `.env`,
чинит схему на asyncpg):
```bash
cd /opt/catalog && git pull origin claude/charming-maxwell-11bdtk && \
cd backend && source venv/bin/activate && \
DBURL="$(grep -E '^database_url=' .env | cut -d= -f2- | tr -d '\"' | sed 's#^postgresql://#postgresql+asyncpg://#')"
# далее: python scripts/<script>.py --db-url "$DBURL"
```

---

## 1. ЧТО ЗА ПРОЕКТ

SaaS-каталог: поставщик грузит товары (CSV прайс-лист) → система автоматически
сопоставляет (маппит) их с позициями **Приказа Минпросвещения РФ №838** →
помогает формировать сметы для школ. Репозиторий: только **backend** (FastAPI),
фронтенда пока почти нет (есть одна служебная HTML-страница ручной проверки).

---

## 2. БОЕВОЙ СЕРВЕР (важные факты)

- Путь проекта: **`/opt/catalog`**, backend в `/opt/catalog/backend`, venv там же.
- Python **3.10**, Ubuntu. `pymorphy2` + `pymorphy2-dicts-ru` установлены.
- **PostgreSQL 16 + pgvector в Docker на порту 5433** (НЕ 5432!). Креды в
  `/opt/catalog/backend/.env` (`database_url=postgresql://...@localhost:5433/catalog_db`).
- **YandexGPT:** ключи в `.env` (`YANDEX_GPT_API_KEY`, `YANDEX_GPT_FOLDER_ID`,
  `YANDEX_GPT_MODEL_URI`). **ОБЯЗАТЕЛЬНО полная модель** `gpt://<folder>/yandexgpt/latest`
  (НЕ `yandexgpt-lite` — на lite точность судьи падает с ~84% до ~58%).
- **Нет systemd-сервиса** `catalog-api` (в старом резюме упоминался, но не создан).
- **nginx** на сервере обслуживает ЧУЖОЙ проект (editor-web → :3002), к нам не относится.
- **Фаервол (ufw) активен.** Открыты порты: 80, 8080, 3001, OpenSSH и теперь **8001**.
- Данные на сервере (НЕ в git): `/opt/catalog/data/input/838.xlsx`,
  `/opt/catalog/data/input/шаблон товары.csv`, `/opt/catalog/data/input/app.1000psc.csv`.

### Как сейчас запущен UI ручной проверки
Поднят вручную в фоне (НЕ systemd):
```bash
cd /opt/catalog/backend && source venv/bin/activate && \
nohup uvicorn app.main:app --host 0.0.0.0 --port 8001 > /opt/catalog/uvicorn.log 2>&1 &
```
Открывается: **http://31.192.110.121:8001/api/review**
(первый клик по товару ~минуту — грузится модель эмбеддингов, потом мгновенно).
TODO: оформить как systemd-сервис (чтобы переживал перезагрузку).

---

## 3. ТЕКУЩЕЕ СОСТОЯНИЕ БД

- `industry_standards`: **1888 позиций** Приказа 838 (+ колонка `full_code`,
  embedding(768), keywords[]). Перепарсено корректно (старый парсер терял 511).
- `products`: **934 товара** (10 из шаблона + ~924 из app.1000psc.csv),
  эмбеддинги по **name only** (так точнее — name+desc пробовали, хуже).
- `product_standard_mapping`: заполнен авто-маппингом — **630 авто / 301 на ручную /
  3 null** (всего 931 запись; маппинги пишутся upsert'ом, есть UNIQUE(product_id,standard_id)).

---

## 4. АРХИТЕКТУРА МАППИНГА (готова и измерена)

Точка входа: `MappingService.classify_product(product_id)` в
`backend/app/services/mapping_service.py`. Пайплайн:

1. **Детерминированный роутер** (`_rule_match`, без LLM). Закрывает самый частый
   класс — демонстрационные таблицы:
   - «Таблицы демонстрационные…»/«Комплект таблиц…» (не раздаточные, не
     электронные, не мебель) → **код 2.17** «Комплект демонстрационных учебных
     таблиц (по предметной области)»; если предмет **физика** → **код 2.14.137**.
   - На выборке: срабатывает на ~60% товаров, точность ~98%.
   - Коды резолвятся через `_std_index["code2id"]`. Константы:
     `CODE_TABLES_GENERIC="2.17"`, `CODE_TABLES_PHYSICS="2.14.137"`.
2. **Гибридный ретрив** (`map_product_to_standards`, top_k=20): пул кандидатов =
   вектор top-K (pgvector по эмбеддингу name) **∪** keyword-IDF top-K (леммы
   name+description, глушим только функц. слова) **∪** все 22 «по предметной
   области» генерик-позиции (иначе вытесняются предметными). recall@20 ≈ 91%.
3. **LLM-судья** (`llm_mapping_service.get_llm_mapping`, полная yandexgpt):
   кандидаты помечены областью/кабинетом («[По предметной области] …»,
   «[Кабинет химии] …»). Промпт матчит по ТИПУ изделия (таблицы≠карты≠модели≠
   пособия), знает конвенцию «по предметной области», русский≠иностранный,
   блок «частые ошибки — не путай», есть retry/backoff (2/4/8с на таймаут/5xx/429).
4. **Калибровка авто/ручная** по СОГЛАСИЮ КАНАЛОВ (уверенность LLM бесполезна —
   всегда 0.9–1.0): если выбранную позицию подтвердили И вектор, И keyword → авто;
   иначе → ручная. Роутер → всегда авто.

**Измеренная точность (выборка 99 размеченных товаров):**
- accuracy **82%**, recall **91%**, precision@pool **90%**.
- роутер: 60 шт, 98%. LLM: 39 шт, 56% (трудный хвост).
- **АВТО: 67 шт, точность 99%.** РУЧНАЯ: 32 шт (туда уходят ошибки) — калибровка работает.
- Часть оставшихся «ошибок» — спорные/ошибочные метки эксперта, не сбои системы.

---

## 5. КАРТА ФАЙЛОВ

### Боевой код (`backend/app/`)
- `services/mapping_service.py` — **ядро**: роутер + гибридный ретрив + classify_product
  + auto_map_all_products. Синглтоны: модель эмбеддингов, IDF-индекс стандартов.
- `services/llm_mapping_service.py` — промпт судьи + вызов YandexGPT + retry.
- `services/product_service.py` — импорт товаров из CSV (парсинг цен, эмбеддинг по name).
- `api/endpoints/mapping.py` — `POST /api/mapping/auto-map`, `GET /api/mapping/candidates/{id}`.
- `api/endpoints/products.py` — `POST /api/products/upload` (CSV + поставщик).
- `api/endpoints/review.py` — **UI ручной проверки** + API (`/api/review`, /queue,
  /stats, /product/{id}/candidates, /mapping/{id}/approve|reassign|reject).
- `core/config.py` — Settings (читает `.env`). `core/database.py` — движок БД
  (читает `settings.database_url`, чинит схему на asyncpg; фоллбэк 5432).
- `main.py` — регистрация роутеров (products, mapping, review).

### Скрипты (`backend/scripts/`) — все принимают `--db-url`
- `parse_order_838.py` — xlsx → `data/output/order_838_tree.json` (1888 позиций, иерархия из кода).
- `import_standards.py` — JSON → industry_standards (+ full_code, DELETE+INSERT, леммы keywords).
- `generate_embeddings.py` — эмбеддинги стандартов (~10–15 мин на 1888).
- `regenerate_product_embeddings.py` — эмбеддинги товаров (`--mode name|name_desc`, дефолт name).
- `import_products.py` — импорт товаров из CSV (`--csv --supplier-name --db-url`).
- `run_automap.py` — **полный авто-маппинг** всех товаров (пишет в БД, печатает распределение).
- `diagnose_mapping.py` — read-only диагностика гибрида (распределение скоров).
- `simulate_strategies.py` — read-only сравнение стратегий скоринга.
- `llm_rerank_eval.py` — read-only оценка LLM-реранжирования (есть `--sample`, `--sleep`).
- `eval_pipeline.py` — **сквозная оценка** на размеченной выборке (роутер+LLM,
  пишет `logs/eval_dump_*.csv`, метрики accuracy/recall/precision/АВТО/РУЧНАЯ).
- `recall_experiment.py` — read-only recall vector/keyword/union по размеченным.
- `make_review_sheet.py` / `score_review.py` — генерация листа проверки из llm_rerank JSON / подсчёт.
- `export_standards.py` — выгрузка справочника 838 в `logs/standards_838.csv` (+ full_code).

### Данные/эталон (`logs/`, в git)
- `logs/standards_838.csv` — справочник всех 1888 позиций (id, full_code, кабинет, название).
- `logs/review_labeled_newids.csv` — **ЭКСПЕРТНЫЙ ЭТАЛОН**: 99 товаров с correct_std_id
  (новые id). Метки уже почищены (37→1923, 884→2108, 920→1910, физика-таблицы→2288).
- `logs/eval_dump_*.csv`, `logs/diagnose_*`, `logs/simulate_*`, `logs/llm_rerank_*` — прогоны.

### Доки
- `PROJECT_CONTEXT.md` — обновлён (актуальная архитектура, 1888 позиций, full model).
- `HANDOFF.md` — этот файл.

---

## 6. ЧАСТЫЕ КОМАНДЫ (на сервере)

Полный авто-маппинг (пишет в БД, ~20–40 мин):
```bash
python scripts/run_automap.py --db-url "$DBURL"
```
Сквозная оценка качества на эталоне (~5 мин, ~40 вызовов LLM):
```bash
python scripts/eval_pipeline.py --csv ../logs/review_labeled_newids.csv --db-url "$DBURL"
# затем при желании: cd /opt/catalog && git add logs/eval_dump_*.csv && git commit -m "eval" && git push
```
Пересборка классификатора (если меняли парсер): parse → import → generate_embeddings → export.

---

## 7. ИЗВЕСТНЫЕ НЮАНСЫ / ДОЛГИ

- Уверенность LLM неинформативна (всё 0.9–1.0) — для авто/ручная используем
  согласие каналов ретрива (не трогать без замера).
- `config.py` поля дефолтятся через `os.getenv(...)` — работает, но «не чисто»;
  при желании отрефакторить на чистый pydantic-settings.
- Роутер расширять на карты/раздаточные НЕ стали — второго регулярного класса нет
  (таблицы = 65 из 99, остальное предметно-зависимый хвост, его тянет LLM).
- UI на `nohup` — оформить systemd-юнит.
- Батчинг/конкурентность вызовов LLM для ускорения больших загрузок — не сделано.
- Экспорт результатов маппинга в Excel/CSV — не сделано.

---

## 8. ЦЕЛЬ СЛЕДУЮЩЕЙ СЕССИИ: ФРОНТЕНД

Делаем фронтенд (полноценный UI, не только служебная страница):
1. **Инструмент загрузки новых прайс-листов** (CSV/Excel поставщиков):
   форма загрузки → бэкенд уже умеет (`POST /api/products/upload`, парсинг цен в
   `product_service`), но: проверить форматы, показать прогресс/ошибки строк,
   привязку к поставщику.
2. **Классификация загруженного** прайса: запустить маппинг по новым товарам
   (есть `auto_map_all_products` / `classify_product`) и показать результат —
   что авто, что на ручную, с возможностью проверки (уже есть `/api/review`).

Нюансы под фронт:
- Бэкенд на FastAPI; решить, делать SPA (React/Vue) или server-rendered/vanilla
  (как текущая `/api/review`). Текущий UI — самодостаточный HTML+JS внутри FastAPI.
- `auto_map_all_products` сейчас маппит ВСЕ товары; для «классифицировать только
  что загруженное» понадобится фильтр по поставщику/новизне (доработать).
- Импорт генерирует эмбеддинг по name (в ProductService); если решим иначе —
  согласовать с ретривом.
- Модель эмбеддингов грузится ~минуту на первом запросе — учесть в UX.
