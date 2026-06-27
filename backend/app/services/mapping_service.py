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

        res = await self.db.execute(text("SELECT id, item_name FROM industry_standards"))
        lemmas, names, df = {}, {}, {}
        for sid, name in res.fetchall():
            lem = lemmatize(name)
            lemmas[sid] = lem
            names[sid] = name
            for w in lem:
                df[w] = df.get(w, 0) + 1

        n_docs = max(len(names), 1)
        idf = {w: math.log(n_docs / (c + 1)) + 1.0 for w, c in df.items()}
        _std_index = {"lemmas": lemmas, "idf": idf, "names": names}
        logger.info("IDF-индекс стандартов построен: %d позиций", len(names))
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
    async def map_product_to_standards(self, product_id: int, top_k: int = 15) -> list:
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

        candidates = list(pool.values())
        # Сортировка для показа: сначала по вектору (есть vsim), затем keyword-only.
        candidates.sort(
            key=lambda c: (c["vector_similarity"] if c["vector_similarity"] is not None else -1.0),
            reverse=True,
        )
        for c in candidates:
            # match_score — для обратной совместимости с потребителями (=vsim или 0)
            c["match_score"] = c["vector_similarity"] if c["vector_similarity"] is not None else 0.0
            vs = "—" if c["vector_similarity"] is None else f"{c['vector_similarity']:.3f}"
            ks = "—" if c["keyword_score"] is None else f"{c['keyword_score']:.2f}"
            c["match_reason"] = f"sources={'+'.join(c['sources'])} vec={vs} kw={ks}"
        return candidates

    # ------------------------------------------------------------------ #
    # Авто-маппинг всех товаров: гибридный ретрив -> LLM-судья -> решение
    # ------------------------------------------------------------------ #
    async def auto_map_all_products(
        self,
        llm_confidence_threshold: float = 0.7,
        top_k: int = 15,
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
        errors = []

        for row in products:
            product_id, product_name, product_description = row[0], row[1], row[2]
            product_properties = row[3] if has_properties else {}

            try:
                pool = await self.map_product_to_standards(product_id, top_k=top_k)
                if not pool:
                    errors.append(f"Товар {product_id} ({product_name}): нет кандидатов")
                    continue

                product_data = {
                    "name": product_name,
                    "description": product_description or "",
                    "properties": product_properties or {},
                }
                llm_candidates = [
                    {"id": c["standard_id"], "standard_name": c["standard_name"]}
                    for c in pool
                ]

                llm = await get_llm_mapping(product_data, llm_candidates)
                llm_id = llm.get("standard_id")
                llm_conf = llm.get("confidence", 0.0) or 0.0
                llm_reason = llm.get("reason", "")

                # LLM не нашёл подходящего — на ручную, маппинг не пишем
                if llm_id is None:
                    no_match += 1
                    continue

                is_manual = llm_conf < llm_confidence_threshold
                final_reason = f"LLM (conf {llm_conf:.2f}): {llm_reason}"

                await self._upsert_mapping(
                    product_id, llm_id, llm_conf, final_reason, is_manual
                )
                await self.db.commit()

                if is_manual:
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
