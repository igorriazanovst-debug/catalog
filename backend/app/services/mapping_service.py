from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from sentence_transformers import SentenceTransformer
import pymorphy2
import re
import json
import logging

# Импортируем наш LLM сервис
from app.services.llm_mapping_service import get_llm_mapping

logger = logging.getLogger(__name__)


class MappingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.embedding_model = SentenceTransformer(
            "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
        )
        self.morph = pymorphy2.MorphAnalyzer()

    def extract_keywords_lemmatized(self, text: str) -> set:
        """Извлекает ключевые слова с лемматизацией"""
        words = re.findall(r'\b[а-яА-Яa-zA-ZёЁ]+\b', text.lower())

        keywords = set()
        for word in words:
            if len(word) >= 3:
                parsed = self.morph.parse(word)
                if parsed:
                    normal_form = parsed[0].normal_form
                    if len(normal_form) >= 3:
                        keywords.add(normal_form)

        return keywords

    async def map_product_to_standards(self, product_id: int, top_k: int = 5) -> list:
        """Находит соответствующие стандарты для товара через гибридный поиск"""

        # Получаем товар
        result = await self.db.execute(
            text("SELECT id, name, description, embedding FROM products WHERE id = :id"),
            {"id": product_id}
        )
        product = result.fetchone()

        if not product:
            return []

        product_id_db, product_name, product_description, product_embedding = product

        # 1. Vector similarity через pgvector
        vector_query = """
            SELECT 
                id,
                item_name,
                keywords,
                embedding <=> CAST(:embedding AS vector) as vector_distance,
                1 - (embedding <=> CAST(:embedding AS vector)) as vector_similarity
            FROM industry_standards
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :top_k
        """

        result = await self.db.execute(
            text(vector_query),
            {"embedding": product_embedding, "top_k": top_k}
        )
        vector_results = result.fetchall()

        # 2. Keyword matching с лемматизацией
        product_text = product_name
        if product_description:
            product_text += " " + product_description

        product_keywords = self.extract_keywords_lemmatized(product_text)

        keyword_results = []
        for std_id, std_name, std_keywords_array, vector_distance, vector_similarity in vector_results:
            std_keywords = set(std_keywords_array) if std_keywords_array else set()

            if product_keywords and std_keywords:
                intersection = len(product_keywords & std_keywords)
                union = len(product_keywords | std_keywords)

                jaccard = intersection / union if union > 0 else 0
                overlap_bonus = min(intersection / 5.0, 0.3)

                keyword_similarity = jaccard + overlap_bonus
            else:
                keyword_similarity = 0

            # Hybrid score: 30% vector + 70% keyword
            hybrid_score = 0.3 * vector_similarity + 0.7 * keyword_similarity

            keyword_results.append({
                "standard_id": std_id,
                "standard_name": std_name,
                "vector_similarity": float(vector_similarity),
                "keyword_similarity": keyword_similarity,
                "match_score": hybrid_score,
                "match_reason": f"Vector: {vector_similarity:.3f}, Keywords: {keyword_similarity:.3f}"
            })

        keyword_results.sort(key=lambda x: x["match_score"], reverse=True)

        return keyword_results

    async def auto_map_all_products(self, threshold: float = 0.6, llm_confidence_threshold: float = 0.7) -> dict:
        """
        Автоматически маппит все товары на стандарты.
        Если гибридный скор ниже threshold, используется YandexGPT.
        """

        # Получаем все товары. Пробуем забрать properties, если колонка есть
        try:
            result = await self.db.execute(
                text("SELECT id, name, description, properties FROM products")
            )
            products = result.fetchall()
            has_properties = True
        except Exception:
            # Если колонки properties нет — берём без неё
            result = await self.db.execute(
                text("SELECT id, name, description FROM products")
            )
            products = [(row[0], row[1], row[2], {}) for row in result.fetchall()]
            has_properties = False
            logger.warning("Колонка 'properties' отсутствует в таблице products. LLM будет работать без характеристик.")

        mapped = 0
        llm_mapped = 0
        needs_review = 0
        errors = []

        for row in products:
            product_id = row[0]
            product_name = row[1]
            product_description = row[2]
            product_properties = row[3] if has_properties else {}

            try:
                candidates = await self.map_product_to_standards(product_id, top_k=5)

                if not candidates:
                    errors.append(f"Товар {product_id} ({product_name}): не найдено кандидатов")
                    continue

                best_match = candidates[0]

                # Переменные для финального решения
                final_standard_id = None
                final_score = 0.0
                final_reason = ""
                is_manual = True
                used_llm = False

                # ЭТАП 1: Проверяем гибридный скор
                if best_match["match_score"] >= threshold:
                    final_standard_id = best_match["standard_id"]
                    final_score = best_match["match_score"]
                    final_reason = f"Авто (гибрид): {best_match['match_reason']}"
                    is_manual = False

                # ЭТАП 2: Fallback на YandexGPT, если скор низкий
                else:
                    product_data = {
                        "name": product_name,
                        "description": product_description or "",
                        "properties": product_properties or {}
                    }

                    llm_candidates = [
                        {"id": c["standard_id"], "standard_name": c["standard_name"]}
                        for c in candidates
                    ]

                    logger.info(
                        f"Товар {product_id} ({product_name}) имеет низкий скор "
                        f"({best_match['match_score']:.2f}). Запрос к LLM..."
                    )
                    llm_decision = await get_llm_mapping(product_data, llm_candidates)

                    llm_confidence = llm_decision.get("confidence", 0.0)
                    llm_reason = llm_decision.get("reason", "Нет ответа от LLM")
                    llm_std_id = llm_decision.get("standard_id")

                    # Если LLM уверен в своем выборе
                    if llm_std_id and llm_confidence >= llm_confidence_threshold:
                        final_standard_id = llm_std_id
                        final_score = llm_confidence
                        final_reason = f"LLM (уверенность {llm_confidence:.2f}): {llm_reason}"
                        is_manual = False
                        used_llm = True
                    else:
                        # LLM сомневается или не нашел подходящего.
                        # Оставляем лучший гибридный вариант для ручной проверки.
                        final_standard_id = best_match["standard_id"]
                        final_score = best_match["match_score"]
                        final_reason = (
                            f"Гибрид: {best_match['match_score']:.2f}. "
                            f"LLM (уверенность {llm_confidence:.2f}): {llm_reason}"
                        )
                        is_manual = True

                # ЭТАП 3: Сохранение в БД (Upsert)
                if final_standard_id is None:
                    # Если ни гибридный поиск, ни LLM не дали ID — пропускаем
                    needs_review += 1
                    continue

                result_check = await self.db.execute(
                    text("""
                        SELECT id FROM product_standard_mapping 
                        WHERE product_id = :product_id AND standard_id = :standard_id
                    """),
                    {"product_id": product_id, "standard_id": final_standard_id}
                )
                existing = result_check.fetchone()

                if existing:
                    await self.db.execute(
                        text("""
                            UPDATE product_standard_mapping
                            SET match_score = :score, match_reason = :reason, is_manual = :is_manual
                            WHERE id = :id
                        """),
                        {
                            "score": final_score,
                            "reason": final_reason,
                            "is_manual": is_manual,
                            "id": existing[0]
                        }
                    )
                else:
                    await self.db.execute(
                        text("""
                            INSERT INTO product_standard_mapping 
                            (product_id, standard_id, match_score, match_reason, is_manual, rejected)
                            VALUES (:product_id, :standard_id, :score, :reason, :is_manual, FALSE)
                        """),
                        {
                            "product_id": product_id,
                            "standard_id": final_standard_id,
                            "score": final_score,
                            "reason": final_reason,
                            "is_manual": is_manual
                        }
                    )

                # Считаем статистику
                if not is_manual:
                    mapped += 1
                    if used_llm:
                        llm_mapped += 1
                else:
                    needs_review += 1

                await self.db.commit()

            except Exception as e:
                logger.exception(f"Ошибка маппинга товара {product_id}")
                errors.append(f"Товар {product_id}: {str(e)}")
                await self.db.rollback()

        return {
            "total_products": len(products),
            "auto_mapped": mapped,
            "llm_mapped": llm_mapped,
            "needs_review": needs_review,
            "errors": errors
        }