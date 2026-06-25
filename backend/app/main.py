from fastapi import FastAPI
from app.api.endpoints import products, mapping

app = FastAPI(title="School Equipment Catalog")

app.include_router(products.router)
app.include_router(mapping.router)

@app.get("/")
async def root():
    return {"status": "ok"}