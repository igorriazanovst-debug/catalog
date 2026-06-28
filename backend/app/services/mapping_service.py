"""
Сервис маппинга товаров на позиции Приказа 838.

Архитектура (подтверждена экспериментально на размеченной выборке):
  1. ГИБРИДНЫЙ РЕТРИВ кандидатов: пул = вектор top-K ∪ keyword-IDF top-K.
     - вектор: pgvector по эмбеддингу товара (name);
     - keyword: IDF-взвешенное совпадение лемм (name+description) с названиями
       стандартов; глушим только функциональные слова, категориальные
       («таблица/демонстрационный/карта») сохраняем.
     На выборке recall@15(union) ≈ 85% против ≈ 39% у чистого вектора.
  2. LLM-СУДЬЯ: пул кандидатов отдаётся YandexGPT, который выбирает один
     стандарт или говорит «подходящего нет» (null). Когда правильный
     кандидат есть в пуле, LLM выбирает его в ~85% случаев.
  3. РЕШЕНИЕ: confidence LLM >= порога → авто-маппинг; иначе → ручная проверка.

Эмбеддинг-модель и IDF-индекс стандартов кэшируются на уровне процесса
(синглтоны), чтобы не перезагружать их на каждый запрос.
"""

import logging
import math
import re

import pymorphy2
from sentence_transformers import SentenceTransformer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.llm_mapping_service import get_llm_mapping

logger = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"

# Только функциональные слова (предлоги/союзы/частицы длиной >=3).
# Категориальные слова НЕ глушим — это сигнал; их вес регулирует IDF.
FUNCTION_WORDS = {
    "для", "как", "так", "где", "или", "что", "при", "после", "себя", "весь",
    "этот", "тот", "который", "также", "можно", "если", "над", "под", "про",
    "без", "два", "три", "шт",
}

# Коды Приказа 838 для детерминированных правил роутера
CODE_TABLES_GENERIC = "2.17"      # Комплект демонстрационных учебных таблиц (по предметной области)
CODE_TABLES_PHYSICS = "2.14.137"  # Комплект демонстрационных учебных таблиц (Кабинет физики)

# Процессные синглтоны (ленивая инициализация)
_embedding_model = None
_morph = None
_std_index = None  # {"lemmas": {id: set}, "idf": {word: float}, "names": {id: str}}


def get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        logger.info("Загрузка модели эмбеддингов %s ...", MODEL_NAME)
        _embedding_model = SentenceTransformer(MODEL_NAME)
    return _embedding_model


def get_morph() -> "pymorphy2.MorphAnalyzer":
    global _morph
    if _morph is None:
        _morph = pymorphy2.MorphAnalyzer()
    return _morph


def lemmatize(s: str) -> set:
    """Леммы слов длиной >=3, без функциональных слов."""
    words = re.findall(r"\b[а-яА-Яa-zA-ZёЁ]+\b", (s or "").lower())
    morph = get_morph()
    out = set()
    for w in words:
        if len(w) >= 3:
            nf = morph.parse(w)[0].normal_form
            if len(nf) >= 3 and nf not in FUNCTION_WORDS:
                out.add(nf)
    return out


class MappingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.embedding_model = get_embedding_model()
        self.morph = get_morph()

    # ------------------------------------------------------------------ #
    # Индекс стандартов для keyword-ретрива (кэшируется на процесс)
    # ------------------------------------------------------------------ #
    async def _ensure_std_index(self) -> dict:
        global _std_index
        if _std_index is not None:
            return _std_index

        res = await self.db.execute(
            text("SELECT id, item_name, full_code FROM industry_standards"))
        lemmas, names, df = {}, {}, {}
        generics = []   # [(id, item_name)] для 2-уровневых «по предметной области»
        code2id = {}    # full_code -> id (для детерминированных правил)
        for sid, name, full_code in res.fetchall():
            lem = lemmatize(name)
            lemmas[sid] = lem
            names[sid] = name
            for w in lem:
                df[w] = df.get(w, 0) + 1
            if full_code:
                code2id[full_code] = sid
                if full_code.count(".") == 1:
                    generics.append((sid, name))

        n_docs = max(len(names), 1)
        idf = {w: math.log(n_docs / (c + 1)) + 1.0 for w, c in df.items()}
        _std_index = {"lemmas": lemmas, "idf": idf, "names": names,
                      "generics": generics, "code2id": code2id}
        logger.info("IDF-индекс стандартов построен: %d позиций, %d генериков",
                    len(names), len(generics))
        return _std_index

    # ------------------------------------------------------------------ #
    # Каналы поиска
    # ------------------------------------------------------------------ #
    async def _vector_candidates(self, embedding, top_k: int) -> list:
        """Топ-K по векторной близости -> [(id, name, vsim)]."""
        if embedding is None:
            return []
        q = text("""
            SELECT id, item_name,
                   1 - (embedding <=> CAST(:e AS vector)) AS vsim
            FROM industry_standards
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:e AS vector)
            LIMIT :k
        """)
        res = await self.db.execute(q, {"e": embedding, "k": top_k})
        return [(r[0], r[1], float(r[2])) for r in res.fetchall()]

    async def _keyword_candidates(self, text_for_kw: str, top_k: int) -> list:
        """Топ-K по IDF-взвешенному совпадению лемм -> [(id, name, score)]."""
        idx = await self._ensure_std_index()
        p_lem = lemmatize(text_for_kw)
        if not p_lem:
            return []
        scored = []
        idf = idx["idf"]
        for sid, lem in idx["lemmas"].items():
            inter = p_lem & lem
            if inter:
                score = sum(idf.get(w, 1.0) for w in inter)
                scored.append((score, sid))
        scored.sort(reverse=True)
        names = idx["names"]
        return [(sid, names[sid], sc) for sc, sid in scored[:top_k]]

    # ------------------------------------------------------------------ #
    # Гибридный ретрив: объединённый пул кандидатов (read-only)
    # ------------------------------------------------------------------ #
    async def map_product_to_standards(self, product_id: int, top_k: int = 20) -> list:
        """Возвращает объединённый пул кандидатов (вектор ∪ keyword) для товара.

        Каждый элемент: standard_id, standard_name, vector_similarity (или None),
        keyword_score (или None), sources (['vector'] / ['keyword'] / оба),
        match_score (для сортировки/совместимости).
        """
        res = await self.db.execute(
            text("SELECT id, name, description, embedding FROM products WHERE id = :id"),
            {"id": product_id},
        )
        product = res.fetchone()
        if not product:
            return []
        _, name, description, embedding = product
        text_for_kw = name + ((" " + description) if description else "")

        vec = await self._vector_candidates(embedding, top_k)
        kw = await self._keyword_candidates(text_for_kw, top_k)
        idx = await self._ensure_std_index()

        # Объединяем по standard_id
        pool = {}
        for sid, sname, vsim in vec:
            pool[sid] = {
                "standard_id": sid,
                "standard_name": sname,
                "vector_similarity": vsim,
                "keyword_score": None,
                "sources": ["vector"],
            }
        for sid, sname, score in kw:
            if sid in pool:
                pool[sid]["keyword_score"] = score
                pool[sid]["sources"].append("keyword")
            else:
                pool[sid] = {
                    "standard_id": sid,
                    "standard_name": sname,
                    "vector_similarity": None,
                    "keyword_score": score,
                    "sources": ["keyword"],
                }

        # Всегда добавляем общие «по предметной области» генерик-позиции:
        # они применимы к любому кабинету (таблицы, словари, ЭОР, мебель и т.п.)
        # и иначе вытесняются предметными кандидатами из top-K.
        for sid, sname in idx.get("generics", []):
            if sid not in pool:
                pool[sid] = {
                    "standard_id": sid,
                    "standard_name": sname,
                    "vector_similarity": None,
                    "keyword_score": None,
                    "sources": ["generic"],
                }

        candidates = list(pool.values())

        # Обогащаем метаданными иерархии (кабинет/область) для метки кандидата.
        ids = list(pool.keys())
        if ids:
            meta_res = await self.db.execute(
                text("SELECT id, full_code, subsection_name FROM industry_standards "
                     "WHERE id = ANY(:ids)"),
                {"ids": ids},
            )
            meta = {r[0]: (r[1], r[2]) for r in meta_res.fetchall()}
            for c in candidates:
                full_code, subsection_name = meta.get(c["standard_id"], (None, None))
                # 2-уровневый код = общая позиция «по предметной области»
                is_generic = bool(full_code) and full_code.count(".") == 1
                label_area = "По предметной области" if is_generic else (subsection_name or "")
                c["full_code"] = full_code
                c["subsection_name"] = subsection_name
                # Метка для LLM: "[Кабинет химии] <название>"
                c["llm_label"] = (f"[{label_area}] {c['standard_name']}"
                                  if label_area else c["standard_name"])

        # Сортировка для показа: сначала по вектору (есть vsim), затем keyword-only.
        candidates.sort(
            key=lambda c: (c["vector_similarity"] if c["vector_similarity"] is not None else -1.0),
            reverse=True,
        )
        for c in candidates:
            # match_score — для обратной совместимости с потребителями (=vsim или 0)
            c["match_score"] = c["vector_similarity"] if c["vector_similarity"] is not None else 0.0
            c.setdefault("llm_label", c["standard_name"])
            vs = "—" if c["vector_similarity"] is None else f"{c['vector_similarity']:.3f}"
            ks = "—" if c["keyword_score"] is None else f"{c['keyword_score']:.2f}"
            c["match_reason"] = f"sources={'+'.join(c['sources'])} vec={vs} kw={ks}"
        return candidates

    # ------------------------------------------------------------------ #
    # Детерминированный роутер очевидных случаев (до LLM)
    # ------------------------------------------------------------------ #
    def _rule_match(self, name: str, description: str, code2id: dict):
        """Возвращает (standard_id, reason) для очевидных типов или None.

        Сейчас закрыт самый частый и регулярный класс — демонстрационные таблицы.
        Правила консервативны: срабатывают только при явном совпадении типа.
        """
        t = (name or "").lower()

        # --- Демонстрационные / учебные ТАБЛИЦЫ ---
        has_tablic = "таблиц" in t
        is_razdat = "раздат" in t                        # раздаточные/«раздат.» — другой тип
        is_electronic = any(w in t for w in
                            ("эор", "электрон", "онлайн", "интерактив", "цифров"))
        is_storage = any(w in t for w in ("тумба", "шкаф", "стеллаж", "хранен"))
        looks_demo_tables = (
            has_tablic and not is_razdat and not is_electronic and not is_storage
            and ("демонстрацион" in t
                 or t.strip().startswith(("комплект таблиц", "таблицы ", "таблица ")))
        )
        if looks_demo_tables:
            is_physics = "физик" in t
            code = CODE_TABLES_PHYSICS if is_physics else CODE_TABLES_GENERIC
            sid = code2id.get(code)
            if sid:
                area = "Кабинет физики" if is_physics else "По предметной области"
                return sid, f"Правило: демонстрационные таблицы → [{area}] (код {code})"
        return None

    async def classify_product(self, product_id: int, top_k: int = 20,
                               llm_confidence_threshold: float = 0.7) -> dict:
        """Единая точка решения по товару: сначала детерминированный роутер,
        иначе гибридный ретрив + LLM-судья.

        Возвращает dict: standard_id, score, reason, method ('rule'|'llm'|'null'),
        is_manual.
        """
        res = await self.db.execute(
            text("SELECT name, description, properties FROM products WHERE id = :id"),
            {"id": product_id},
        )
        row = res.fetchone()
        if not row:
            return {"standard_id": None, "score": 0.0, "reason": "товар не найден",
                    "method": "null", "is_manual": True}
        name, description, properties = row[0], row[1], (row[2] if len(row) > 2 else {})

        idx = await self._ensure_std_index()
        rule = self._rule_match(name, description, idx["code2id"])
        if rule:
            sid, reason = rule
            return {"standard_id": sid, "score": 0.99, "reason": reason,
                    "method": "rule", "is_manual": False}

        # Фоллбэк: LLM-судья по гибридному пулу
        pool = await self.map_product_to_standards(product_id, top_k=top_k)
        if not pool:
            return {"standard_id": None, "score": 0.0, "reason": "нет кандидатов",
                    "method": "null", "is_manual": True}
        llm = await get_llm_mapping(
            {"name": name, "description": description or "", "properties": properties or {}},
            [{"id": c["standard_id"], "standard_name": c.get("llm_label", c["standard_name"])}
             for c in pool],
        )
        llm_id = llm.get("standard_id")
        llm_conf = llm.get("confidence", 0.0) or 0.0
        if llm_id is None:
            return {"standard_id": None, "score": llm_conf,
                    "reason": llm.get("reason", ""), "method": "null", "is_manual": True}

        # Калибровка авто/ручная по СОГЛАСИЮ КАНАЛОВ РЕТРИВА (уверенность LLM
        # практически всегда 0.9–1.0 и неинформативна). Авто — если выбранную
        # позицию подтвердили оба канала (вектор И keyword); иначе — на ручную.
        chosen = next((c for c in pool if c["standard_id"] == llm_id), None)
        agreement = bool(chosen) and {"vector", "keyword"} <= set(chosen.get("sources", []))
        is_manual = not (agreement and llm_conf >= llm_confidence_threshold)
        return {"standard_id": llm_id, "score": llm_conf,
                "reason": f"LLM (conf {llm_conf:.2f}, "
                          f"{'оба канала' if agreement else 'один канал'}): "
                          f"{llm.get('reason', '')}",
                "method": "llm", "is_manual": is_manual, "agreement": agreement}

    # ------------------------------------------------------------------ #
    # Авто-маппинг всех товаров: гибридный ретрив -> LLM-судья -> решение
    # ------------------------------------------------------------------ #
    async def auto_map_all_products(
        self,
        llm_confidence_threshold: float = 0.7,
        top_k: int = 20,
        **_legacy,  # поглощает устаревший threshold= из старых вызовов
    ) -> dict:
        # Берём товары (с properties, если колонка есть)
        try:
            res = await self.db.execute(
                text("SELECT id, name, description, properties FROM products")
            )
            products = res.fetchall()
            has_properties = True
        except Exception:
            res = await self.db.execute(text("SELECT id, name, description FROM products"))
            products = [(r[0], r[1], r[2], {}) for r in res.fetchall()]
            has_properties = False
            logger.warning("Колонка 'properties' отсутствует — LLM без характеристик.")

        auto_mapped = 0
        needs_review = 0
        no_match = 0
        by_rule = 0
        by_llm = 0
        errors = []

        for row in products:
            product_id, product_name = row[0], row[1]

            try:
                decision = await self.classify_product(
                    product_id, top_k=top_k,
                    llm_confidence_threshold=llm_confidence_threshold,
                )

                if decision["standard_id"] is None:
                    no_match += 1
                    continue

                await self._upsert_mapping(
                    product_id, decision["standard_id"], decision["score"],
                    decision["reason"], decision["is_manual"],
                )
                await self.db.commit()

                if decision["method"] == "rule":
                    by_rule += 1
                elif decision["method"] == "llm":
                    by_llm += 1
                if decision["is_manual"]:
                    needs_review += 1
                else:
                    auto_mapped += 1

            except Exception as e:
                logger.exception("Ошибка маппинга товара %s", product_id)
                errors.append(f"Товар {product_id}: {e}")
                await self.db.rollback()

        return {
            "total_products": len(products),
            "auto_mapped": auto_mapped,
            "needs_review": needs_review,
            "no_match": no_match,
            "by_rule": by_rule,
            "by_llm": by_llm,
            "errors": errors,
        }

    async def _upsert_mapping(self, product_id, standard_id, score, reason, is_manual):
        res = await self.db.execute(
            text("""
                SELECT id FROM product_standard_mapping
                WHERE product_id = :p AND standard_id = :s
            """),
            {"p": product_id, "s": standard_id},
        )
        existing = res.fetchone()
        if existing:
            await self.db.execute(
                text("""
                    UPDATE product_standard_mapping
                    SET match_score = :score, match_reason = :reason, is_manual = :m
                    WHERE id = :id
                """),
                {"score": score, "reason": reason, "m": is_manual, "id": existing[0]},
            )
        else:
            await self.db.execute(
                text("""
                    INSERT INTO product_standard_mapping
                    (product_id, standard_id, match_score, match_reason, is_manual, rejected)
                    VALUES (:p, :s, :score, :reason, :m, FALSE)
                """),
                {"p": product_id, "s": standard_id, "score": score,
                 "reason": reason, "m": is_manual},
            )
