import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse, Response

from app.api.endpoints import products, mapping, review, jobs, estimates

logger = logging.getLogger(__name__)

app = FastAPI(title="School Equipment Catalog")

app.include_router(products.router)
app.include_router(mapping.router)
app.include_router(review.router)
app.include_router(jobs.router)
app.include_router(estimates.router)


@app.get("/")
async def root():
    return {"status": "ok"}


class SPAStaticFiles(StaticFiles):
    """StaticFiles с SPA-fallback: на отсутствующий путь отдаёт index.html,
    чтобы клиентские маршруты react-router (например /app/supplier/5)
    работали при прямом заходе/обновлении страницы."""

    async def get_response(self, path: str, scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return FileResponse(Path(self.directory) / "index.html")
            raise


# Раздача собранного SPA (frontend/dist). Монтируем только если сборка
# существует — иначе backend поднимается и без фронта (до первого `npm run
# build`). parents[2] = корень репозитория.
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if (_FRONTEND_DIST / "index.html").is_file():
    app.mount(
        "/app",
        SPAStaticFiles(directory=str(_FRONTEND_DIST), html=True),
        name="spa",
    )
    # print, а не logger.info — чтобы строка всегда была видна в uvicorn.log
    # (INFO по умолчанию отфильтрован).
    print(f"[startup] SPA смонтирован на /app из {_FRONTEND_DIST}", flush=True)
else:
    print(f"[startup] SPA НЕ смонтирован: нет {_FRONTEND_DIST / 'index.html'}. "
          f"Соберите фронт (npm ci && npm run build) или git pull и перезапустите.",
          flush=True)
