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

import base64
import io
import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.estimate_parser import parse_estimate
from app.services.mapping_service import MappingService, lemmatize
from app.services.llm_mapping_service import get_llm_mapping, get_llm_decomposition

logger = logging.getLogger(__name__)

# Какую цену считаем «ценой позиции» для критерия «дешевле» и для итога сметы.
# retail_price (РРЦ) — цена для школы; cost_price — внутренняя себестоимость.
PRICE_FIELDS = {"retail": "retail_price", "cost": "cost_price"}

# Порог «строка — это цельный товар»: если наименование позиции уверенно
# совпадает с позицией 838 (векторная близость топ-кандидата по ИМЕНИ >= порога),
# то характеристики — это части ОДНОГО изделия, а не вложения набора, и разлагать
# строку НЕ нужно. Подобрано по реальным сметам (смета-1: vec≈1.0 — цельный товар;
# смета-2: vec≈0.69 — набор). Эвристика, можно калибровать.
BUNDLE_GATE_SIMILARITY = 0.85


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

    async def _retrieve(self, query: str) -> list[dict]:
        """Гибридный ретрив по 838 для одного запроса: пул = вектор ∪ keyword,
        отсортирован по «силе» (согласие каналов → выше, затем по вектору)."""
        if not query.strip():
            return []
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
        cands.sort(key=lambda c: (
            len(c["sources"]),
            c["vector_similarity"] if c["vector_similarity"] is not None else -1.0,
        ), reverse=True)
        return cands

    async def _enrich(self, cands: list[dict]) -> None:
        """Дописать кандидатам метаданные иерархии и метку для LLM ("[область] имя")."""
        ids = [c["standard_id"] for c in cands]
        if not ids:
            return
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

    @staticmethod
    def _name_query(line: dict) -> str:
        return (line.get("name") or "").strip()[:512]

    @staticmethod
    def _full_query(line: dict) -> str:
        name = (line.get("name") or "").strip()
        extra = " ".join((ch.get("name") or "") for ch in line.get("characteristics", []))
        return (name + " " + extra).strip()[:512]

    async def _text_pool(self, line: dict) -> tuple[list[dict], dict | None]:
        """Пул кандидатов 838 + лучший кандидат по ИМЕНИ.

        Возвращает (pool, name_top1):
          * pool — ОБЪЕДИНЕНИЕ ретрива по наименованию и по наименование+
            характеристики. Имя даёт точность (наименование позиции 44-ФЗ обычно
            совпадает с наименованием 838), характеристики — полноту (поднимают в
            пул правильный стандарт, который чистое имя могло потерять). Большой
            recall важен для LLM-судьи: он выбирает из пула, и нужный стандарт
            должен там быть.
          * name_top1 — топ ретрива ПО ИМЕНИ: его берём как ответ, когда LLM
            выключен (точность@1 у имени выше, чем у имя+характеристики).
        """
        name_cands = await self._retrieve(self._name_query(line))
        full_q = self._full_query(line)
        full_cands = (await self._retrieve(full_q)
                      if full_q != self._name_query(line) else [])

        # Объединяем: сначала кандидаты по имени (порядок сохраняем), затем
        # уникальные добавки из ретрива по характеристикам.
        pool: list[dict] = list(name_cands)
        seen = {c["standard_id"] for c in pool}
        for c in full_cands:
            if c["standard_id"] not in seen:
                pool.append(c)
                seen.add(c["standard_id"])
        pool = pool[:25]  # держим промпт LLM-судьи компактным
        await self._enrich(pool)
        name_top1 = name_cands[0] if name_cands else None
        return pool, name_top1

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

        pool, name_top1 = await self._text_pool(line)
        if not pool:
            return {"method": "none", "standards": [], "candidates": [], "reason": ""}

        # Детерминированный роутер (демо-таблицы и т.п.) — дёшево, без LLM.
        idx = await self.mapping._ensure_std_index()
        rule = self.mapping._rule_match(line.get("name", ""), "", idx["code2id"])
        if rule:
            sid, reason = rule
            std = {"standard_id": sid, "standard_name": idx["names"].get(sid, ""),
                   "full_code": None}
            return {"method": "rule", "standards": [std], "candidates": pool[:5],
                    "reason": reason}

        if not use_llm:
            return {"method": "text", "standards": [name_top1] if name_top1 else [],
                    "candidates": pool[:5], "reason": "топ текстового ретрива (без LLM)"}

        # LLM-судья выбирает из ПУЛА один стандарт (или null). Характеристики
        # позиции здесь ПОМОГАЮТ уточнить тип (в отличие от ретрива).
        properties = {ch.get("name"): ch.get("value")
                      for ch in line.get("characteristics", []) if ch.get("name")}
        llm = await get_llm_mapping(
            {"name": line.get("name", ""), "description": "", "properties": properties},
            [{"id": c["standard_id"], "standard_name": c.get("llm_label", c["standard_name"])}
             for c in pool],
            provider=provider,
        )
        if not llm.get("error"):
            picked = next((c for c in pool if c["standard_id"] == llm.get("standard_id")), None)
            if picked:
                conf = llm.get("confidence", 0.0) or 0.0
                return {"method": "text+llm", "standards": [picked],
                        "candidates": pool[:5],
                        "reason": f"LLM (conf {conf:.2f}): {llm.get('reason', '')}"}
            # LLM сказал «нет подходящего типа» (null) — берём топ ретрива по имени.
            return {"method": "text",
                    "standards": [name_top1] if name_top1 else [],
                    "candidates": pool[:5],
                    "reason": f"LLM не выбрал тип ({llm.get('reason', '')}); взят топ ретрива"}
        # Сбой LLM — graceful fallback на топ ретрива по имени.
        return {"method": "text", "standards": [name_top1] if name_top1 else [],
                "candidates": pool[:5],
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
    # Подбор по одной позиции (с опц. разложением набора на вложения)
    # ------------------------------------------------------------------ #
    async def match_line(self, line: dict, use_llm: bool = False,
                         provider: str | None = None,
                         decompose: bool = False) -> dict:
        """Подбор под одну строку сметы. Если decompose и use_llm — сначала
        LLM-декомпозиция: цельный товар матчим как есть, набор разложим на
        вложения и подберём каждое (1 строка → N позиций), цену суммируем.

        ГЕЙТ перед декомпозицией: если наименование строки само уверенно совпадает
        с позицией 838 (это цельный товар, характеристики = его части) — НЕ
        разлагаем, иначе разнесли бы один прибор на детали (соленоид/катушки/...)."""
        if decompose and use_llm and line.get("characteristics"):
            _, name_top1 = await self._text_pool(line)
            sim = (name_top1 or {}).get("vector_similarity") or 0.0
            if sim < BUNDLE_GATE_SIMILARITY:
                decomp = await get_llm_decomposition(
                    {"name": line.get("name", ""),
                     "characteristics": line.get("characteristics", [])},
                    provider=provider,
                )
                if not decomp.get("error") and decomp.get("is_bundle"):
                    return await self._match_bundle(line, decomp["items"], provider)
        return await self._match_single(line, use_llm=use_llm, provider=provider)

    async def _match_bundle(self, line: dict, items: list[dict],
                            provider: str | None) -> dict:
        """Набор: подобрать каждое вложение отдельно, суммировать."""
        line_qty = _to_float(line.get("quantity"), default=1.0)
        subs = []
        for it in items:
            sub_line = {
                "position": None,
                "name": it["name"],
                "code_ktru": None, "code_okpd2": None,
                "quantity": line_qty * it.get("quantity_per_set", 1.0),
                "unit": "шт",
                "characteristics": [],
                # вложение наследует строку исходного файла родителя (для экспорта)
                "source_rows": line.get("source_rows"),
            }
            subs.append(await self._match_single(sub_line, use_llm=True,
                                                 provider=provider))
        total = sum(s["total_price"] for s in subs if s["total_price"])
        warnings = []
        missing = [s for s in subs if not s["chosen_offer"]]
        if missing:
            warnings.append(f"Без подбора {len(missing)} из {len(subs)} вложений.")
        return {
            "line": {
                "position": line.get("position"), "name": line.get("name"),
                "code_ktru": line.get("code_ktru"), "code_okpd2": line.get("code_okpd2"),
                "quantity": line_qty, "unit": line.get("unit"),
                "source_rows": line.get("source_rows"),
                "source_description": _chars_text(line.get("characteristics")),
            },
            "match_method": "bundle",
            "match_reason": f"набор разложен на {len(subs)} вложений (LLM)",
            "is_bundle": True,
            "sub_items": subs,
            "standard": None,
            "standard_candidates": [],
            "chosen_offer": None,
            "alternatives": [],
            "unit_price": None,
            "total_price": total if total else None,
            "warnings": warnings,
        }

    async def _match_single(self, line: dict, use_llm: bool = False,
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
                "source_rows": line.get("source_rows"),
                "source_description": line.get("source_description")
                or _chars_text(line.get("characteristics")),
            },
            "match_method": resolved["method"],
            "match_reason": resolved.get("reason", ""),
            "is_bundle": False,
            "standard": standards[0] if standards else None,
            "standard_candidates": resolved["candidates"],
            "chosen_offer": chosen,
            "alternatives": alternatives,
            "unit_price": unit_price,
            "total_price": total_price,
            "warnings": warnings,
        }

    async def match_estimate(self, parsed: dict, use_llm: bool = False,
                             provider: str | None = None,
                             decompose: bool = False, progress=None) -> dict:
        """Подобрать товары под все позиции разобранной сметы + посчитать итоги.
        progress: callable(processed, total) — для прогресса фоновой задачи."""
        items = parsed.get("items", [])
        results = []
        for i, it in enumerate(items, 1):
            results.append(await self.match_line(it, use_llm=use_llm,
                                                 provider=provider,
                                                 decompose=decompose))
            if progress:
                progress(i, len(items))

        subtotal = sum(r["total_price"] for r in results if r["total_price"])
        vat = await self.vat_rate()
        vat_amount = round(subtotal * vat, 2)

        # Счётчики считаем по «листьям»: вложения набора — каждое отдельно.
        leaves = []
        for r in results:
            if r.get("is_bundle"):
                leaves.extend(r["sub_items"])
            else:
                leaves.append(r)
        matched = sum(1 for r in leaves if r["chosen_offer"])
        by_code = sum(1 for r in leaves if r["match_method"] in ("ktru", "okpd2"))
        by_llm = sum(1 for r in leaves if r["match_method"] == "text+llm")
        by_text = sum(1 for r in leaves if r["match_method"] in ("text", "rule"))
        unmatched = sum(1 for r in leaves if r["match_method"] == "none")
        bundles = sum(1 for r in results if r.get("is_bundle"))

        return {
            "sheet": parsed.get("sheet"),
            "items": results,
            "summary": {
                "positions": len(results),
                "bundles": bundles,
                "subitems_total": len(leaves),
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


    # ------------------------------------------------------------------ #
    # Сохранение результата подбора в БД (estimates / estimate_items)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _leaves(match_result: dict):
        """Плоский список «листьев» сметы: обычная позиция — сама строка;
        набор — его вложения (group_name = наименование набора)."""
        for r in match_result.get("items", []):
            if r.get("is_bundle"):
                for sub in r["sub_items"]:
                    yield sub, r["line"].get("name")
            else:
                yield r, None

    @staticmethod
    def _src_row(line: dict):
        rows = line.get("source_rows") or []
        return rows[0] if rows else None

    async def _insert_item(self, estimate_id: int, leaf: dict, group_name):
        """Вставить одну позицию (лист) сметы."""
        line = leaf["line"]
        std = leaf.get("standard")
        offer = leaf.get("chosen_offer")
        await self.db.execute(
            text("""
                INSERT INTO estimate_items
                  (estimate_id, standard_id, product_id, supplier_id,
                   source_name, source_description, source_row, group_name, unit,
                   match_method, match_reason, quantity, unit_price, total_price)
                VALUES
                  (:eid, :sid, :pid, :supid, :sname, :sdesc, :srow, :gname, :unit,
                   :method, :reason, :qty, :uprice, :tprice)
            """),
            {
                "eid": estimate_id,
                "sid": std["standard_id"] if std else None,
                "pid": offer["product_id"] if offer else None,
                "supid": offer["supplier_id"] if offer else None,
                "sname": line.get("name"),
                "sdesc": line.get("source_description"),
                "srow": self._src_row(line),
                "gname": group_name,
                "unit": line.get("unit"),
                "method": leaf.get("match_method"),
                "reason": leaf.get("match_reason"),
                "qty": line.get("quantity") or 1.0,
                "uprice": leaf.get("unit_price") or 0.0,
                "tprice": leaf.get("total_price") or 0.0,
            },
        )

    async def save_estimate(self, name: str, match_result: dict,
                            description: str | None = None) -> dict:
        """Создать смету и записать её позиции (вложения набора — отдельно)."""
        leaves = list(self._leaves(match_result))
        total_amount = sum((leaf.get("total_price") or 0.0) for leaf, _ in leaves)
        res = await self.db.execute(
            text("""INSERT INTO estimates (name, description, total_amount)
                    VALUES (:name, :descr, :total) RETURNING id"""),
            {"name": name, "descr": description, "total": total_amount},
        )
        estimate_id = res.scalar()
        for leaf, group_name in leaves:
            await self._insert_item(estimate_id, leaf, group_name)
        await self.db.commit()
        return {"estimate_id": estimate_id, "items": len(leaves),
                "total_amount": round(total_amount, 2)}

    # ------------------------------------------------------------------ #
    # Разбор-и-запись (без подбора) + классификация уже сохранённой сметы
    # ------------------------------------------------------------------ #
    async def create_estimate_from_parsed(self, name: str, parsed: dict,
                                          source_filename: str | None,
                                          source_file_b64: str | None) -> dict:
        """Сохранить распознанные строки сметы БЕЗ подбора (для предпросмотра и
        последующей классификации). Хранит исходный файл для аннот. экспорта."""
        res = await self.db.execute(
            text("""INSERT INTO estimates
                      (name, total_amount, source_filename, source_file_b64,
                       sheet_name, header_row)
                    VALUES (:name, 0, :fn, :b64, :sheet, :hrow) RETURNING id"""),
            {"name": name, "fn": source_filename, "b64": source_file_b64,
             "sheet": parsed.get("sheet"), "hrow": parsed.get("header_row")},
        )
        estimate_id = res.scalar()
        for it in parsed.get("items", []):
            rows = it.get("source_rows") or []
            await self.db.execute(
                text("""
                    INSERT INTO estimate_items
                      (estimate_id, source_name, source_description, source_row,
                       unit, quantity, unit_price, total_price)
                    VALUES (:eid, :sname, :sdesc, :srow, :unit, :qty, 0, 0)
                """),
                {"eid": estimate_id, "sname": it.get("name"),
                 "sdesc": _chars_text(it.get("characteristics")),
                 "srow": rows[0] if rows else None,
                 "unit": it.get("unit"),
                 "qty": _to_float(it.get("quantity"), 1.0)},
            )
        await self.db.commit()
        return {"estimate_id": estimate_id, "items": len(parsed.get("items", []))}

    async def _load_parsed(self, estimate_id: int) -> tuple[dict, str]:
        row = await self.db.execute(
            text("SELECT name, source_file_b64, source_filename "
                 "FROM estimates WHERE id = :id"), {"id": estimate_id})
        r = row.fetchone()
        if not r or not r[1]:
            raise ValueError("у сметы нет сохранённого исходного файла")
        content = base64.b64decode(r[1])
        parsed = parse_estimate(io.BytesIO(content), display_name=r[2] or r[0])
        return parsed, r[0]

    async def classify_estimate(self, estimate_id: int, use_llm: bool,
                                provider: str | None, decompose: bool,
                                progress=None) -> dict:
        """Авто-классификация всей сметы: пере-разобрать исходный файл, подобрать,
        ПЕРЕЗАПИСАТЬ позиции."""
        parsed, _ = await self._load_parsed(estimate_id)
        result = await self.match_estimate(parsed, use_llm=use_llm,
                                            provider=provider, decompose=decompose,
                                            progress=progress)
        await self._replace_items(estimate_id, result)
        return result["summary"]

    async def _replace_items(self, estimate_id: int, match_result: dict):
        await self.db.execute(
            text("DELETE FROM estimate_items WHERE estimate_id = :id"),
            {"id": estimate_id})
        total = 0.0
        for leaf, group_name in self._leaves(match_result):
            await self._insert_item(estimate_id, leaf, group_name)
            total += leaf.get("total_price") or 0.0
        await self.db.execute(
            text("UPDATE estimates SET total_amount = :t WHERE id = :id"),
            {"t": round(total, 2), "id": estimate_id})
        await self.db.commit()

    async def classify_item(self, estimate_id: int, item_id: int,
                            use_llm: bool, provider: str | None) -> dict:
        """Классифицировать ОДНУ строку (ручной режим, без/с LLM). Декомпозиция
        здесь не применяется — это одна строка. Возвращает подбор для ответа."""
        row = await self.db.execute(
            text("""SELECT source_name, source_description, quantity, unit, source_row
                    FROM estimate_items WHERE id = :iid AND estimate_id = :eid"""),
            {"iid": item_id, "eid": estimate_id})
        r = row.fetchone()
        if not r:
            raise ValueError("позиция не найдена")
        chars = [{"name": r[1], "value": ""}] if r[1] else []
        line = {
            "name": r[0], "characteristics": chars,
            "quantity": float(r[2]) if r[2] is not None else 1.0,
            "unit": r[3], "code_ktru": None, "code_okpd2": None,
            "source_rows": [r[4]] if r[4] else None,
            "source_description": r[1],
        }
        res = await self._match_single(line, use_llm=use_llm, provider=provider)
        std = res.get("standard")
        offer = res.get("chosen_offer")
        await self.db.execute(
            text("""UPDATE estimate_items
                    SET standard_id = :sid, product_id = :pid, supplier_id = :supid,
                        group_name = NULL, match_method = :method,
                        match_reason = :reason, unit_price = :uprice,
                        total_price = :tprice
                    WHERE id = :iid"""),
            {"sid": std["standard_id"] if std else None,
             "pid": offer["product_id"] if offer else None,
             "supid": offer["supplier_id"] if offer else None,
             "method": res.get("match_method"),
             "reason": res.get("match_reason"),
             "uprice": res.get("unit_price") or 0.0,
             "tprice": res.get("total_price") or 0.0,
             "iid": item_id})
        await self._recompute_total(estimate_id)
        await self.db.commit()
        return res

    async def _recompute_total(self, estimate_id: int):
        await self.db.execute(
            text("""UPDATE estimates SET total_amount =
                      (SELECT COALESCE(SUM(total_price),0) FROM estimate_items
                       WHERE estimate_id = :id)
                    WHERE id = :id"""), {"id": estimate_id})

    async def offers_for_standard(self, standard_id: int) -> list[dict]:
        """Предложения (товар+цена) под один стандарт — для ручного выбора в UI."""
        return await self._offers_for_standards([standard_id])


def _to_float(v, default: float = 0.0) -> float:
    if v is None:
        return default
    s = str(v).strip().replace(",", ".")
    # оставляем только число (кол-во может прийти как «5 набор»)
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else default


def _chars_text(characteristics) -> str:
    """Характеристики строки сметы → читаемое «описание по смете» (для показа и
    хранения). Каждая характеристика: «Имя: значение ед.»."""
    if not characteristics:
        return ""
    parts = []
    for c in characteristics:
        name = (c.get("name") or "").strip()
        val = (c.get("value") or "").strip()
        unit = (c.get("unit") or "").strip()
        line = name
        if val:
            line = f"{name}: {val}" if name else val
        if unit:
            line = f"{line} {unit}"
        if line:
            parts.append(line)
    return "; ".join(parts)
