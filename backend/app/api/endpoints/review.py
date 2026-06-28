"""
API + страница ручной проверки маппинга.

Очередь = записи product_standard_mapping с is_manual=true и rejected=false.
Действия: подтвердить (approve), переназначить на другой стандарт (reassign),
отклонить (reject). Страница /review — самодостаточный HTML+JS.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.mapping_service import MappingService

router = APIRouter(prefix="/api/review", tags=["review"])


@router.get("/stats")
async def review_stats(db: AsyncSession = Depends(get_db)):
    res = await db.execute(text("""
        SELECT
          COUNT(*) FILTER (WHERE NOT rejected) AS total,
          COUNT(*) FILTER (WHERE NOT is_manual AND NOT rejected) AS auto,
          COUNT(*) FILTER (WHERE is_manual AND NOT rejected) AS manual,
          COUNT(*) FILTER (WHERE rejected) AS rejected
        FROM product_standard_mapping
    """))
    r = res.fetchone()
    return {"total": r[0], "auto": r[1], "manual": r[2], "rejected": r[3]}


@router.get("/queue")
async def review_queue(limit: int = 50, offset: int = 0,
                       db: AsyncSession = Depends(get_db)):
    res = await db.execute(text("""
        SELECT m.id, m.standard_id, m.match_score, m.match_reason,
               p.id, p.name, p.description, p.sku,
               s.item_name, s.full_code, s.subsection_name
        FROM product_standard_mapping m
        JOIN products p ON p.id = m.product_id
        JOIN industry_standards s ON s.id = m.standard_id
        WHERE m.is_manual = TRUE AND m.rejected = FALSE
        ORDER BY m.id
        LIMIT :limit OFFSET :offset
    """), {"limit": limit, "offset": offset})
    items = []
    for r in res.fetchall():
        items.append({
            "mapping_id": r[0],
            "standard_id": r[1],
            "match_score": r[2],
            "match_reason": r[3],
            "product_id": r[4],
            "product_name": r[5],
            "description": r[6],
            "sku": r[7],
            "standard_name": r[8],
            "full_code": r[9],
            "subsection_name": r[10],
        })
    return {"items": items}


@router.get("/product/{product_id}/candidates")
async def product_candidates(product_id: int, top_k: int = 20,
                             db: AsyncSession = Depends(get_db)):
    service = MappingService(db)
    cands = await service.map_product_to_standards(product_id, top_k=top_k)
    if not cands:
        raise HTTPException(status_code=404, detail="нет кандидатов")
    return {"product_id": product_id, "candidates": [
        {"standard_id": c["standard_id"], "standard_name": c["standard_name"],
         "full_code": c.get("full_code"), "subsection_name": c.get("subsection_name"),
         "sources": c.get("sources", []),
         "vector_similarity": c.get("vector_similarity"),
         "keyword_score": c.get("keyword_score")}
        for c in cands
    ]}


async def _update_mapping(db, mapping_id, **fields):
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = mapping_id
    res = await db.execute(
        text(f"UPDATE product_standard_mapping SET {sets} WHERE id = :id RETURNING id"),
        fields,
    )
    if not res.fetchone():
        raise HTTPException(status_code=404, detail="маппинг не найден")
    await db.commit()


@router.post("/mapping/{mapping_id}/approve")
async def approve(mapping_id: int, db: AsyncSession = Depends(get_db)):
    await _update_mapping(db, mapping_id, is_manual=False)
    return {"status": "approved"}


@router.post("/mapping/{mapping_id}/reassign")
async def reassign(mapping_id: int, standard_id: int,
                   db: AsyncSession = Depends(get_db)):
    chk = await db.execute(
        text("SELECT id, item_name FROM industry_standards WHERE id = :id"),
        {"id": standard_id})
    row = chk.fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="стандарт не найден")
    await _update_mapping(
        db, mapping_id, standard_id=standard_id, is_manual=False,
        match_reason=f"Ручной выбор: {row[1]}", rejected=False)
    return {"status": "reassigned", "standard_id": standard_id}


@router.post("/mapping/{mapping_id}/reject")
async def reject(mapping_id: int, db: AsyncSession = Depends(get_db)):
    await _update_mapping(db, mapping_id, rejected=True, is_manual=False)
    return {"status": "rejected"}


@router.get("", response_class=HTMLResponse)
async def review_page():
    return REVIEW_HTML


REVIEW_HTML = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Ручная проверка маппинга</title>
<style>
 body{font-family:system-ui,Arial,sans-serif;margin:0;color:#1a1a1a}
 header{background:#0d3b66;color:#fff;padding:10px 16px;display:flex;gap:16px;align-items:center}
 header b{font-size:16px} .stat{font-size:13px;opacity:.9}
 .wrap{display:flex;height:calc(100vh - 48px)}
 .list{width:38%;overflow:auto;border-right:1px solid #ddd}
 .detail{width:62%;overflow:auto;padding:16px}
 .row{padding:10px 14px;border-bottom:1px solid #eee;cursor:pointer}
 .row:hover{background:#f5f8ff} .row.active{background:#e8f0ff}
 .row .nm{font-weight:600;font-size:14px} .row .cur{font-size:12px;color:#555;margin-top:3px}
 .muted{color:#777;font-size:13px} .desc{white-space:pre-wrap;background:#fafafa;border:1px solid #eee;padding:8px;border-radius:6px;max-height:160px;overflow:auto;font-size:13px}
 .cand{padding:8px;border:1px solid #e3e3e3;border-radius:6px;margin:6px 0;display:flex;gap:8px;align-items:flex-start}
 .cand:hover{background:#f7faff} .cand.cur{border-color:#0d3b66;background:#eef4ff}
 .tag{font-size:11px;background:#eef;border-radius:4px;padding:1px 5px;margin-left:6px;color:#335}
 .code{font-family:monospace;color:#888;font-size:12px}
 button{cursor:pointer;border:none;border-radius:6px;padding:8px 12px;font-size:13px;margin-right:8px}
 .approve{background:#1b873f;color:#fff} .reassign{background:#0d3b66;color:#fff} .reject{background:#b00020;color:#fff}
 .bar{position:sticky;top:0;background:#fff;padding:10px 0;border-bottom:1px solid #eee;margin-bottom:10px}
 #empty{padding:40px;text-align:center;color:#777}
</style></head>
<body>
<header><b>Ручная проверка маппинга</b>
 <span class="stat" id="stats">…</span>
 <span class="stat" style="margin-left:auto" id="msg"></span></header>
<div class="wrap">
 <div class="list" id="list"></div>
 <div class="detail" id="detail"><div id="empty">Выберите товар слева</div></div>
</div>
<script>
let queue=[], cur=null, chosen=null;
async function jget(u){const r=await fetch(u);if(!r.ok)throw new Error(await r.text());return r.json();}
async function jpost(u){const r=await fetch(u,{method:'POST'});if(!r.ok)throw new Error(await r.text());return r.json();}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
async function loadStats(){const s=await jget('/api/review/stats');
 document.getElementById('stats').textContent=`всего ${s.total} · авто ${s.auto} · на проверке ${s.manual} · отклонено ${s.rejected}`;}
async function loadQueue(){queue=(await jget('/api/review/queue?limit=200')).items;renderList();}
function renderList(){const el=document.getElementById('list');
 if(!queue.length){el.innerHTML='<div id="empty">Очередь пуста 🎉</div>';return;}
 el.innerHTML=queue.map((it,i)=>`<div class="row ${cur&&cur.mapping_id===it.mapping_id?'active':''}" onclick="openItem(${i})">
   <div class="nm">${esc(it.product_name)}</div>
   <div class="cur">→ [${esc(it.subsection_name||'')}] ${esc(it.standard_name)} <span class="code">${esc(it.full_code||'')}</span></div></div>`).join('');}
async function openItem(i){cur=queue[i];chosen=cur.standard_id;renderList();
 const d=document.getElementById('detail');d.innerHTML='<div class="muted">Загрузка кандидатов…</div>';
 let cands=[];try{cands=(await jget(`/api/review/product/${cur.product_id}/candidates`)).candidates;}catch(e){}
 // текущий стандарт всегда первым, если его нет в пуле
 if(!cands.some(c=>c.standard_id===cur.standard_id))
   cands.unshift({standard_id:cur.standard_id,standard_name:cur.standard_name,full_code:cur.full_code,subsection_name:cur.subsection_name,sources:['текущий']});
 d.innerHTML=`<div class="bar">
   <button class="approve" onclick="act('approve')">✓ Подтвердить текущий</button>
   <button class="reassign" onclick="act('reassign')">↻ Переназначить на выбранный</button>
   <button class="reject" onclick="act('reject')">✗ Отклонить</button></div>
  <h3>${esc(cur.product_name)}</h3>
  <div class="muted">SKU: ${esc(cur.sku||'')} · причина: ${esc(cur.match_reason||'')}</div>
  <p class="desc">${esc(cur.description||'(без описания)')}</p>
  <h4>Кандидаты (выберите для переназначения):</h4>
  <div id="cands">${cands.map(c=>candHtml(c)).join('')}</div>`;}
function candHtml(c){const isCur=c.standard_id===cur.standard_id;
 const src=(c.sources||[]).map(s=>`<span class="tag">${s}</span>`).join('');
 const v=c.vector_similarity!=null?` vec ${c.vector_similarity.toFixed(2)}`:'';
 const k=c.keyword_score!=null?` kw ${c.keyword_score.toFixed(1)}`:'';
 return `<label class="cand ${isCur?'cur':''}" id="c${c.standard_id}">
   <input type="radio" name="cand" ${c.standard_id===chosen?'checked':''} onchange="chosen=${c.standard_id}">
   <div><div>[${esc(c.subsection_name||'')}] <b>${esc(c.standard_name)}</b> <span class="code">${esc(c.full_code||'')}</span>${isCur?' <span class="tag">текущий</span>':''}</div>
   <div class="muted">${src}${v}${k}</div></div></label>`;}
async function act(kind){if(!cur)return;let url;
 if(kind==='approve')url=`/api/review/mapping/${cur.mapping_id}/approve`;
 else if(kind==='reject')url=`/api/review/mapping/${cur.mapping_id}/reject`;
 else{if(!chosen){alert('Выберите кандидата');return;}url=`/api/review/mapping/${cur.mapping_id}/reassign?standard_id=${chosen}`;}
 try{await jpost(url);msg(`✓ ${kind}`);queue=queue.filter(q=>q.mapping_id!==cur.mapping_id);cur=null;
   document.getElementById('detail').innerHTML='<div id="empty">Готово. Выберите следующий товар.</div>';
   renderList();loadStats();}catch(e){msg('Ошибка: '+e.message);}}
function msg(t){document.getElementById('msg').textContent=t;setTimeout(()=>document.getElementById('msg').textContent='',2500);}
loadStats();loadQueue();
</script></body></html>"""
