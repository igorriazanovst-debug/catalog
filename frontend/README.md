# Frontend (SPA)

React 18 + Vite + TypeScript. UI для загрузки прайс-листов поставщиков и
классификации товаров по Приказу Минпросвещения №838 (поверх существующего
FastAPI-бэкенда).

## Экраны

- **Поставщики** (`/app/`) — список поставщиков со счётчиками (товаров, авто,
  на проверке, без маппинга).
- **Загрузить прайс** (`/app/upload`) — форма поставщика + CSV (разделитель «;»,
  колонки `Артикул;Наименование;Себестоимость;РРЦ`). Показывает прогресс
  загрузки и построчные ошибки. `POST /api/products/upload`.
- **Карточка поставщика** (`/app/supplier/:id`) — запуск классификации
  (`Классифицировать новые` = только без маппинга / `Переклассифицировать все`),
  таблица товаров со статусом и сопоставленной позицией 838, фильтры по статусу,
  модальная проверка с кандидатами (подтвердить / переназначить / отклонить).

## Используемые API

- `GET /api/products/suppliers` — поставщики со счётчиками.
- `GET /api/products?supplier_id=&status=` — товары с текущим маппингом и ценой.
- `POST /api/products/upload` — импорт CSV.
- `POST /api/mapping/auto-map?supplier_id=&only_unmapped=` — классификация.
- `GET /api/review/product/{id}/candidates`, `POST /api/review/mapping/{id}/{approve|reassign|reject}`.

## Разработка

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173/app/  (/api проксируется на :8001)
# другой адрес бэка: VITE_API_TARGET=http://host:8001 npm run dev
```

Бэкенд для dev:
```bash
cd backend && source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

## Сборка и деплой

SPA раздаётся самим FastAPI под путём `/app` (см. `backend/app/main.py`:
монтирование `StaticFiles` из `frontend/dist`, если каталог существует).
Отдельный веб-сервер не нужен — один origin, без CORS.

```bash
cd frontend
npm ci
npm run build        # → frontend/dist
```

Каталог `frontend/dist` **коммитится в репозиторий**, потому что боевой сервер
работает по схеме «git pull + запуск» и не имеет Node для сборки. После любых
изменений фронта: пересобрать `dist`, закоммитить, на сервере `git pull` и
перезапустить uvicorn.

Открывается на сервере: `http://<host>:8001/app/`
(служебная страница ручной проверки остаётся на `/api/review`).
