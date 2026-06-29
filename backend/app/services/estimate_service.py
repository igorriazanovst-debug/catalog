"""Сопоставление позиций входящей сметы с каталогом.

Вход — позиции из `estimate_parser` (наименование, код КТРУ/ОКПД2, кол-во,
характеристики). Для каждой позиции:

  ШАГ 1. Позиция → стандарт Приказа 838 (`industry_standards`):
     а) ПО КОДУ (приоритетно): КТРУ → `industry_standards.ktru_code`, иначе
        ОКПД2 → `industry_standards.okpd2_code`;
     б) если по коду не нашли — ТЕКСТОВЫЙ ретрив (вектор ∪ keyword) по
        НАИМЕНОВАНИЮ позиции (переиспользуем `MappingService`) → пул кандидатов;
     в) детерминированный роутер (демо-таблицы, без LLM) на пуле;
     г) LLM-СУДЬЯ (опционально, `use_llm`) выбирает из пула один стандарт —
        характеристики позиции здесь помогают уточнить тип (в отличие от ретрива,
        где они уводят). Без LLM берём топ ретрива.
  ШАГ 2. Стандарт → товары → цена: товары, привязанные к стандарту через
     `product_standard_mapping` (NOT rejected), их предложения поставщиков
     (`supplier_products`). Критерий выбора (по решению пользователя): СНАЧАЛА
     КАЧЕСТВО маппинга (is_manual=FALSE выше очереди на проверку, затем выше
     match_score), ПОТОМ цена — самое дешёвое по cost_price (себестоимость).
     Остальные предложения — как альтернативы.

LLM-судья опционален (`use_llm`, переключаемый провайдер). Выбор поставщика —
пока из ВСЕХ (фильтра нет). Сервис read-only: ничего не пишет в БД (валидация
качества подбора на реальных данных; запись в `estimates`/`estimate_items` —
следующий этап).

ВАЖНО про коды: в текущей БД `industry_standards.ktru_code/okpd2_code` могут быть
не заполнены (импорт 838 их не проставлял) — тогда код-матч ничего не находит и
срабатывает текстовый фоллбэк. `db_code_availability()` показывает, что заполнено.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.mapping_service import MappingService, lemmatize
from app.services.llm_mapping_service import get_llm_mapping

logger = logging.getLogger(__name__)

# Какую цену считаем «ценой позиции» для критерия «дешевле» и для итога сметы.
# retail_price (РРЦ) — цена для школы; cost_price — внутренняя себестоимость.
PRICE_FIELDS = {"retail": "retail_price", "cost": "cost_price"}


class EstimateMatcher:
    def __init__(self, db: AsyncSession, price_basis: str = "cost",
                 top_k: int = 20):
        self.db = db
        self.mapping = MappingService(db)  # переиспользуем каналы ретрива
        if price_basis not in PRICE_FIELDS:
            raise ValueError(f"price_basis must be one of {list(PRICE_FIELDS)}")
        self.price_basis = price_basis
        self.price_col = PRICE_FIELDS[price_basis]
        self.top_k = top_k

    # ------------------------------------------------------------------ #
    # Диагностика БД: что вообще можно сматчить
    # ------------------------------------------------------------------ #
    async def db_code_availability(self) -> dict:
        async def _scalar(sql: str) -> int:
            r = await self.db.execute(text(sql))
            return int(r.scalar() or 0)

        return {
            "standards_total": await _scalar("SELECT count(*) FROM industry_standards"),
            "standards_with_ktru": await _scalar(
                "SELECT count(*) FROM industry_standards WHERE ktru_code IS NOT NULL AND ktru_code <> ''"),
            "standards_with_okpd2": await _scalar(
                "SELECT count(*) FROM industry_standards WHERE okpd2_code IS NOT NULL AND okpd2_code <> ''"),
            "products_total": await _scalar("SELECT count(*) FROM products"),
            "products_with_ktru": await _scalar(
                "SELECT count(*) FROM products WHERE ktru_code IS NOT NULL AND ktru_code <> ''"),
            "mappings_active": await _scalar(
                "SELECT count(*) FROM product_standard_mapping WHERE NOT rejected"),
            "supplier_offers": await _scalar(
                "SELECT count(*) FROM supplier_products WHERE is_available"),
        }

    async def vat_rate(self) -> float:
        r = await self.db.execute(
            text("SELECT value FROM system_settings WHERE key = 'vat_rate'"))
        v = r.scalar()
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    # ------------------------------------------------------------------ #
    # ШАГ 1: позиция -> стандарт 838
    # ------------------------------------------------------------------ #
    async def _by_ktru(self, code_ktru: str | None) -> list[dict]:
        if not code_ktru:
            return []
        r = await self.db.execute(
            text("SELECT id, item_name, full_code FROM industry_standards "
                 "WHERE ktru_code = :c"),
            {"c": code_ktru},
        )
        return [{"standard_id": x[0], "standard_name": x[1], "full_code": x[2]}
                for x in r.fetchall()]

    async def _by_okpd2(self, code_okpd2: str | None) -> list[dict]:
        if not code_okpd2:
            return []
        r = await self.db.execute(
            text("SELECT id, item_name, full_code FROM industry_standards "
                 "WHERE okpd2_code = :c"),
            {"c": code_okpd2},
        )
        return [{"standard_id": x[0], "standard_name": x[1], "full_code": x[2]}
                for x in r.fetchall()]

    async def _by_text(self, query: str) -> list[dict]:
        """Текстовый фоллбэк: гибридный ретрив по 838. Возвращает кандидатов,
        отсортированных по убыванию «силы» совпадения (согласие каналов → выше)."""
        if not query.strip():
            return []
        # Эмбеддинг запроса -> строка pgvector "[...]".
        vec = self.mapping.embedding_model.encode([query])[0]
        emb_str = "[" + ",".join(str(x) for x in vec.tolist()) + "]"

        vcands = await self.mapping._vector_candidates(emb_str, self.top_k)
        kcands = await self.mapping._keyword_candidates(query, self.top_k)

        pool: dict[int, dict] = {}
        for sid, sname, vsim in vcands:
            pool[sid] = {"standard_id": sid, "standard_name": sname,
                         "vector_similarity": vsim, "keyword_score": None,
                         "sources": ["vector"]}
        for sid, sname, score in kcands:
            if sid in pool:
                pool[sid]["keyword_score"] = score
                pool[sid]["sources"].append("keyword")
            else:
                pool[sid] = {"standard_id": sid, "standard_name": sname,
                             "vector_similarity": None, "keyword_score": score,
                             "sources": ["keyword"]}
        cands = list(pool.values())
        # Ранжируем: сперва подтверждённые обоими каналами, затем по вектору.
        cands.sort(key=lambda c: (
            len(c["sources"]),
            c["vector_similarity"] if c["vector_similarity"] is not None else -1.0,
        ), reverse=True)

        # Обогащаем метаданными иерархии и меткой для LLM-судьи ("[область] имя"),
        # как в MappingService.map_product_to_standards.
        ids = [c["standard_id"] for c in cands]
        if ids:
            meta_res = await self.db.execute(
                text("SELECT id, full_code, subsection_name FROM industry_standards "
                     "WHERE id = ANY(:ids)"),
                {"ids": ids},
            )
            meta = {r[0]: (r[1], r[2]) for r in meta_res.fetchall()}
            for c in cands:
                full_code, subsection_name = meta.get(c["standard_id"], (None, None))
                is_generic = bool(full_code) and full_code.count(".") == 1
                area = "По предметной области" if is_generic else (subsection_name or "")
                c["full_code"] = full_code
                c["subsection_name"] = subsection_name
                c["llm_label"] = f"[{area}] {c['standard_name']}" if area else c["standard_name"]
        return cands

    @staticmethod
    def _line_query(line: dict) -> str:
        """Текст для ретрива по 838. Главный сигнал — НАИМЕНОВАНИЕ позиции: в
        44-ФЗ оно обычно совпадает с наименованием позиции 838. Характеристики
        описывают СОДЕРЖИМОЕ набора (репродукции/портреты/таблицы и т.п.) и
        способны увести ретрив в сторону (на стандарт одного из вложений), поэтому
        их добавляем только если имени мало (коротко) для уверенного ретрива.
        (Полный список характеристик остаётся в позиции — пригодится LLM-судье.)"""
        name = (line.get("name") or "").strip()
        if len(lemmatize(name)) >= 4:
            return name[:512]
        extra = " ".join((ch.get("name") or "") for ch in line.get("characteristics", []))
        return (name + " " + extra).strip()[:512]

    async def _resolve_standard(self, line: dict, use_llm: bool = False,
                                provider: str | None = None) -> dict:
        """Вернуть {method, standards:[...], candidates:[...], reason} для позиции.
        method: 'ktru' | 'okpd2' | 'rule' | 'text+llm' | 'text' | 'none'.

        Порядок: код (КТРУ→ОКПД2) → текстовый пул → детерминированный роутер
        (демо-таблицы, без LLM) → LLM-судья выбирает из пула (если use_llm),
        иначе берём топ ретрива."""
        by_ktru = await self._by_ktru(line.get("code_ktru"))
        if by_ktru:
            return {"method": "ktru", "standards": by_ktru, "candidates": by_ktru,
                    "reason": "точное совпадение КТРУ"}
        by_okpd2 = await self._by_okpd2(line.get("code_okpd2"))
        if by_okpd2:
            return {"method": "okpd2", "standards": by_okpd2, "candidates": by_okpd2,
                    "reason": "совпадение ОКПД2"}

        cands = await self._by_text(self._line_query(line))
        if not cands:
            return {"method": "none", "standards": [], "candidates": [], "reason": ""}

        # Детерминированный роутер (демо-таблицы и т.п.) — дёшево, без LLM.
        idx = await self.mapping._ensure_std_index()
        rule = self.mapping._rule_match(line.get("name", ""), "", idx["code2id"])
        if rule:
            sid, reason = rule
            std = {"standard_id": sid, "standard_name": idx["names"].get(sid, ""),
                   "full_code": None}
            return {"method": "rule", "standards": [std], "candidates": cands[:5],
                    "reason": reason}

        if not use_llm:
            return {"method": "text", "standards": cands[:1], "candidates": cands[:5],
                    "reason": "топ текстового ретрива (без LLM)"}

        # LLM-судья выбирает из пула один стандарт (или null). Характеристики
        # позиции здесь ПОМОГАЮТ уточнить тип (в отличие от ретрива).
        properties = {ch.get("name"): ch.get("value")
                      for ch in line.get("characteristics", []) if ch.get("name")}
        llm = await get_llm_mapping(
            {"name": line.get("name", ""), "description": "", "properties": properties},
            [{"id": c["standard_id"], "standard_name": c.get("llm_label", c["standard_name"])}
             for c in cands],
            provider=provider,
        )
        if not llm.get("error"):
            picked = next((c for c in cands if c["standard_id"] == llm.get("standard_id")), None)
            if picked:
                conf = llm.get("confidence", 0.0) or 0.0
                return {"method": "text+llm", "standards": [picked],
                        "candidates": cands[:5],
                        "reason": f"LLM (conf {conf:.2f}): {llm.get('reason', '')}"}
            # LLM сказал «нет подходящего типа» (null) — берём топ ретрива с пометкой.
            return {"method": "text", "standards": cands[:1], "candidates": cands[:5],
                    "reason": f"LLM не выбрал тип ({llm.get('reason', '')}); взят топ ретрива"}
        # Сбой LLM — graceful fallback на топ ретрива.
        return {"method": "text", "standards": cands[:1], "candidates": cands[:5],
                "reason": f"сбой LLM ({llm.get('reason', '')}); взят топ ретрива"}

    # ------------------------------------------------------------------ #
    # ШАГ 2: стандарт -> товары -> самое дешёвое предложение
    # ------------------------------------------------------------------ #
    async def _offers_for_standards(self, standard_ids: list[int]) -> list[dict]:
        if not standard_ids:
            return []
        # Все доступные предложения поставщиков по товарам, привязанным к стандартам.
        # Критерий (по решению пользователя): СНАЧАЛА КАЧЕСТВО маппинга, ПОТОМ цена.
        #   1) is_manual=FALSE (авто-подтверждённые/одобренные вручную) выше, чем
        #      is_manual=TRUE (ещё в очереди на проверку, доверие ниже);
        #   2) внутри — выше match_score;
        #   3) при равном качестве — дешевле (по выбранной цене).
        q = text(f"""
            SELECT sp.product_id, p.name, p.sku, p.manufacturer,
                   sp.supplier_id, s.name AS supplier_name,
                   sp.retail_price, sp.cost_price,
                   sp.delivery_days, sp.stock_quantity,
                   m.standard_id, m.match_score, m.is_manual
            FROM product_standard_mapping m
            JOIN products p          ON p.id = m.product_id
            JOIN supplier_products sp ON sp.product_id = p.id
            JOIN suppliers s          ON s.id = sp.supplier_id
            WHERE m.standard_id = ANY(:ids)
              AND NOT m.rejected
              AND sp.is_available = TRUE
              AND sp.{self.price_col} > 0
            ORDER BY m.is_manual ASC,
                     m.match_score DESC NULLS LAST,
                     sp.{self.price_col} ASC
        """)
        r = await self.db.execute(q, {"ids": standard_ids})
        offers = []
        for x in r.fetchall():
            offers.append({
                "product_id": x[0], "product_name": x[1], "sku": x[2],
                "manufacturer": x[3], "supplier_id": x[4], "supplier_name": x[5],
                "retail_price": float(x[6]) if x[6] is not None else None,
                "cost_price": float(x[7]) if x[7] is not None else None,
                "delivery_days": x[8], "stock_quantity": x[9],
                "standard_id": x[10], "match_score": x[11], "is_manual": x[12],
            })
        return offers

    # ------------------------------------------------------------------ #
    # Полный подбор по одной позиции
    # ------------------------------------------------------------------ #
    async def match_line(self, line: dict, use_llm: bool = False,
                         provider: str | None = None) -> dict:
        resolved = await self._resolve_standard(line, use_llm=use_llm, provider=provider)
        standards = resolved["standards"]
        std_ids = [s["standard_id"] for s in standards]

        offers = await self._offers_for_standards(std_ids)
        chosen = offers[0] if offers else None
        alternatives = offers[1:10] if len(offers) > 1 else []

        qty = _to_float(line.get("quantity"), default=1.0)
        unit_price = chosen[self.price_col] if chosen else None
        total_price = (unit_price * qty) if unit_price is not None else None

        warnings = []
        if resolved["method"] == "none":
            warnings.append("Позиция не сопоставлена со стандартом 838.")
        elif not offers:
            warnings.append(
                "Стандарт найден, но в каталоге нет привязанных товаров с ценой "
                "(пустой/частичный маппинг или нет предложений поставщиков).")

        return {
            "line": {
                "position": line.get("position"),
                "name": line.get("name"),
                "code_ktru": line.get("code_ktru"),
                "code_okpd2": line.get("code_okpd2"),
                "quantity": qty,
                "unit": line.get("unit"),
            },
            "match_method": resolved["method"],
            "match_reason": resolved.get("reason", ""),
            "standard": standards[0] if standards else None,
            "standard_candidates": resolved["candidates"],
            "chosen_offer": chosen,
            "alternatives": alternatives,
            "unit_price": unit_price,
            "total_price": total_price,
            "warnings": warnings,
        }

    async def match_estimate(self, parsed: dict, use_llm: bool = False,
                             provider: str | None = None) -> dict:
        """Подобрать товары под все позиции разобранной сметы + посчитать итоги."""
        items = parsed.get("items", [])
        results = [await self.match_line(it, use_llm=use_llm, provider=provider)
                   for it in items]

        subtotal = sum(r["total_price"] for r in results if r["total_price"])
        vat = await self.vat_rate()
        vat_amount = round(subtotal * vat, 2)

        matched = sum(1 for r in results if r["chosen_offer"])
        by_code = sum(1 for r in results if r["match_method"] in ("ktru", "okpd2"))
        by_llm = sum(1 for r in results if r["match_method"] == "text+llm")
        by_text = sum(1 for r in results if r["match_method"] in ("text", "rule"))
        unmatched = sum(1 for r in results if r["match_method"] == "none")

        return {
            "sheet": parsed.get("sheet"),
            "items": results,
            "summary": {
                "positions": len(results),
                "matched_with_offer": matched,
                "resolved_by_code": by_code,
                "resolved_by_llm": by_llm,
                "resolved_by_text": by_text,
                "unresolved": unmatched,
                "subtotal": round(subtotal, 2),
                "vat_rate": vat,
                "vat_amount": vat_amount,
                "total_with_vat": round(subtotal + vat_amount, 2),
                "price_basis": self.price_basis,
            },
        }


def _to_float(v, default: float = 0.0) -> float:
    if v is None:
        return default
    s = str(v).strip().replace(",", ".")
    # оставляем только число (кол-во может прийти как «5 набор»)
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else default
