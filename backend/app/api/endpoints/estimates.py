"""API входящих смет: загрузка (фон) → подбор → просмотр/правка → экспорт.

Пайплайн загрузки (как у импорта товаров — длинная операция с LLM, поэтому в фоне):
  POST /api/estimates/upload  — принимает xlsx, сразу отдаёт {job_id}; разбор+
    подбор+запись идут фоном, прогресс/итог — через GET /api/jobs/{id}.
  GET  /api/estimates              — список смет.
  GET  /api/estimates/{id}         — смета с позициями (товар/поставщик/цена).
  GET  /api/estimates/{id}/items/{item_id}/candidates — варианты товаров для строки.
  POST /api/estimates/{id}/items/{item_id}/choose      — ручной выбор товара/поставщика.
  GET  /api/estimates/{id}/export  — выгрузка сметы в xlsx.
  DELETE /api/estimates/{id}       — удалить смету.
"""

import asyncio
import io

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, async_session
from app.services.estimate_parser import parse_estimate
from app.services.estimate_service import EstimateMatcher
from app.services.mapping_service import get_embedding_model
from app.services.jobs import jobs, run_job

router = APIRouter(prefix="/api/estimates", tags=["estimates"])

_XLSX_EXT = (".xlsx", ".xlsm", ".xltx", ".xltm")


@router.post("/upload")
async def upload_estimate(
    file: UploadFile = File(...),
    name: str = Form(None),
    use_llm: bool = Form(True),
    provider: str = Form(None),
    decompose: bool = Form(True),
    price_basis: str = Form("cost"),
):
    """Загрузить входящую смету (xlsx). Разбор+подбор+запись — в фоне."""
    if not file.filename or not file.filename.lower().endswith(_XLSX_EXT):
        raise HTTPException(status_code=400, detail="Файл должен быть в формате xlsx")
    if price_basis not in ("cost", "retail"):
        raise HTTPException(status_code=400, detail="price_basis: cost|retail")

    content = await file.read()
    # Быстрый разбор сразу — чтобы отдать понятную ошибку, если файл не смета.
    try:
        parsed = parse_estimate(io.BytesIO(content), display_name=file.filename)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Не удалось разобрать xlsx: {e}")
    if not parsed.get("items"):
        raise HTTPException(
            status_code=400,
            detail="В файле не найдено позиций сметы: " + "; ".join(parsed.get("warnings", [])))

    est_name = (name or "").strip() or file.filename
    prov = None if (provider or "").strip() in ("", "default") else provider.strip()

    job = jobs.create("estimate")
    job.total = len(parsed["items"])

    async def body(job):
        # Прогрев модели эмбеддингов в потоке (как при импорте товаров).
        await asyncio.to_thread(get_embedding_model)
        async with async_session() as bg_db:
            matcher = EstimateMatcher(bg_db, price_basis=price_basis)
            result = await matcher.match_estimate(
                parsed, use_llm=use_llm, provider=prov, decompose=decompose,
                progress=lambda p, t: job.set_progress(p, t),
            )
            saved = await matcher.save_estimate(est_name, result)
        return {"estimate_id": saved["estimate_id"], "items": saved["items"],
                "total_amount": saved["total_amount"], "summary": result["summary"]}

    run_job(job, body)
    return {"job_id": job.id, "name": est_name, "positions": len(parsed["items"])}


@router.get("")
async def list_estimates(db: AsyncSession = Depends(get_db)):
    res = await db.execute(text("""
        SELECT e.id, e.name, e.description, e.total_amount, e.created_at,
               COUNT(i.id) AS items,
               COUNT(i.id) FILTER (WHERE i.product_id IS NOT NULL) AS matched
        FROM estimates e
        LEFT JOIN estimate_items i ON i.estimate_id = e.id
        GROUP BY e.id
        ORDER BY e.created_at DESC, e.id DESC
    """))
    items = [{
        "id": r[0], "name": r[1], "description": r[2],
        "total_amount": float(r[3]) if r[3] is not None else 0.0,
        "created_at": r[4].isoformat() if r[4] else None,
        "items": r[5] or 0, "matched": r[6] or 0,
    } for r in res.fetchall()]
    return {"items": items}


async def _estimate_or_404(db, estimate_id: int):
    res = await db.execute(
        text("SELECT id, name, description, total_amount, created_at "
             "FROM estimates WHERE id = :id"), {"id": estimate_id})
    row = res.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="смета не найдена")
    return row


@router.get("/{estimate_id}")
async def get_estimate(estimate_id: int, db: AsyncSession = Depends(get_db)):
    e = await _estimate_or_404(db, estimate_id)
    res = await db.execute(text("""
        SELECT i.id, i.source_name, i.group_name, i.unit, i.match_method,
               i.match_reason, i.quantity, i.unit_price, i.total_price,
               i.standard_id, st.item_name, st.full_code,
               i.product_id, p.name, p.sku,
               i.supplier_id, s.name
        FROM estimate_items i
        LEFT JOIN industry_standards st ON st.id = i.standard_id
        LEFT JOIN products p ON p.id = i.product_id
        LEFT JOIN suppliers s ON s.id = i.supplier_id
        WHERE i.estimate_id = :id
        ORDER BY i.id
    """), {"id": estimate_id})
    items = [{
        "id": r[0], "source_name": r[1], "group_name": r[2], "unit": r[3],
        "match_method": r[4], "match_reason": r[5],
        "quantity": float(r[6]) if r[6] is not None else None,
        "unit_price": float(r[7]) if r[7] is not None else None,
        "total_price": float(r[8]) if r[8] is not None else None,
        "standard_id": r[9], "standard_name": r[10], "full_code": r[11],
        "product_id": r[12], "product_name": r[13], "sku": r[14],
        "supplier_id": r[15], "supplier_name": r[16],
    } for r in res.fetchall()]
    return {
        "id": e[0], "name": e[1], "description": e[2],
        "total_amount": float(e[3]) if e[3] is not None else 0.0,
        "created_at": e[4].isoformat() if e[4] else None,
        "items": items,
    }


@router.get("/{estimate_id}/items/{item_id}/candidates")
async def item_candidates(estimate_id: int, item_id: int,
                          db: AsyncSession = Depends(get_db)):
    """Варианты товаров для строки (по её стандарту 838) — для ручного выбора."""
    res = await db.execute(
        text("SELECT standard_id FROM estimate_items "
             "WHERE id = :iid AND estimate_id = :eid"),
        {"iid": item_id, "eid": estimate_id})
    row = res.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="позиция не найдена")
    if row[0] is None:
        return {"candidates": []}
    matcher = EstimateMatcher(db)
    offers = await matcher.offers_for_standard(row[0])
    return {"candidates": offers}


@router.post("/{estimate_id}/items/{item_id}/choose")
async def choose_item(estimate_id: int, item_id: int,
                      product_id: int, supplier_id: int,
                      price_basis: str = "cost",
                      db: AsyncSession = Depends(get_db)):
    """Ручной выбор товара/поставщика для строки: пересчёт цены и итога сметы."""
    if price_basis not in ("cost", "retail"):
        raise HTTPException(status_code=400, detail="price_basis: cost|retail")
    res = await db.execute(
        text("SELECT quantity FROM estimate_items "
             "WHERE id = :iid AND estimate_id = :eid"),
        {"iid": item_id, "eid": estimate_id})
    row = res.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="позиция не найдена")
    qty = float(row[0]) if row[0] is not None else 1.0

    price_col = "cost_price" if price_basis == "cost" else "retail_price"
    pres = await db.execute(
        text(f"SELECT {price_col} FROM supplier_products "
             "WHERE product_id = :pid AND supplier_id = :supid"),
        {"pid": product_id, "supid": supplier_id})
    prow = pres.fetchone()
    if not prow:
        raise HTTPException(status_code=400, detail="нет такого предложения поставщика")
    unit_price = float(prow[0]) if prow[0] is not None else 0.0
    total_price = unit_price * qty

    await db.execute(
        text("""UPDATE estimate_items
                SET product_id = :pid, supplier_id = :supid,
                    unit_price = :uprice, total_price = :tprice,
                    match_method = 'manual',
                    match_reason = 'ручной выбор товара/поставщика'
                WHERE id = :iid"""),
        {"pid": product_id, "supid": supplier_id, "uprice": unit_price,
         "tprice": total_price, "iid": item_id})
    # Пересчёт итога сметы.
    await db.execute(
        text("""UPDATE estimates SET total_amount =
                 (SELECT COALESCE(SUM(total_price), 0) FROM estimate_items
                  WHERE estimate_id = :eid)
                WHERE id = :eid"""),
        {"eid": estimate_id})
    await db.commit()
    return {"status": "ok", "unit_price": unit_price, "total_price": total_price}


@router.delete("/{estimate_id}")
async def delete_estimate(estimate_id: int, db: AsyncSession = Depends(get_db)):
    await _estimate_or_404(db, estimate_id)
    await db.execute(text("DELETE FROM estimates WHERE id = :id"), {"id": estimate_id})
    await db.commit()
    return {"status": "deleted"}


@router.get("/{estimate_id}/export")
async def export_estimate(estimate_id: int, db: AsyncSession = Depends(get_db)):
    """Выгрузка сметы в xlsx (наименование, набор, стандарт, товар, поставщик,
    кол-во, цена, стоимость) + итог с НДС."""
    import openpyxl
    from openpyxl.styles import Font

    detail = await get_estimate(estimate_id, db)
    vat_res = await db.execute(
        text("SELECT value FROM system_settings WHERE key = 'vat_rate'"))
    try:
        vat = float(vat_res.scalar() or 0.0)
    except (TypeError, ValueError):
        vat = 0.0

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Смета"
    headers = ["№", "Наименование (смета)", "Набор", "Позиция 838", "Код 838",
               "Подобранный товар", "Артикул", "Поставщик", "Кол-во", "Ед.",
               "Цена за ед.", "Стоимость"]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)

    for n, it in enumerate(detail["items"], 1):
        ws.append([
            n, it["source_name"], it["group_name"] or "",
            it["standard_name"] or "", it["full_code"] or "",
            it["product_name"] or "", it["sku"] or "",
            it["supplier_name"] or "", it["quantity"], it["unit"] or "",
            it["unit_price"], it["total_price"],
        ])

    subtotal = detail["total_amount"]
    vat_amount = round(subtotal * vat, 2)
    ws.append([])
    ws.append(["", "", "", "", "", "", "", "", "", "", "Итого:", subtotal])
    ws.append(["", "", "", "", "", "", "", "", "", "", f"НДС {round(vat*100)}%:", vat_amount])
    ws.append(["", "", "", "", "", "", "", "", "", "", "Всего с НДС:",
               round(subtotal + vat_amount, 2)])
    for row_off in (0, 1, 2):
        ws[ws.max_row - row_off][10].font = Font(bold=True)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"estimate_{estimate_id}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
