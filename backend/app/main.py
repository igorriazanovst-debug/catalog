import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse, Response

from app.api.endpoints import products, mapping, review

logger = logging.getLogger(__name__)

app = FastAPI(title="School Equipment Catalog")

app.include_router(products.router)
app.include_router(mapping.router)
app.include_router(review.router)


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
    logger.info("SPA смонтирован на /app из %s", _FRONTEND_DIST)
else:
    logger.warning(
        "SPA НЕ смонтирован: нет %s. Соберите фронт (cd frontend && npm ci && "
        "npm run build) или сделайте git pull, затем перезапустите uvicorn.",
        _FRONTEND_DIST / "index.html",
    )
