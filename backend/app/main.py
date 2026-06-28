from fastapi import FastAPI
from app.api.endpoints import products, mapping, review

app = FastAPI(title="School Equipment Catalog")

app.include_router(products.router)
app.include_router(mapping.router)
app.include_router(review.router)

@app.get("/")
async def root():
    return {"status": "ok"}