import asyncio
import hashlib
import re
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from sentence_transformers import SentenceTransformer

# Префикс внутреннего (авто-сгенерированного) артикула для товаров,
# у которых поставщик не указал «Артикул».
INTERNAL_SKU_PREFIX = "AUTO-"

# Модель эмбеддингов — синглтон на процесс (грузится ~минуту, переиспользуется
# между запросами/импортами).
_EMB_MODEL = None


def get_embedding_model() -> SentenceTransformer:
    global _EMB_MODEL
    if _EMB_MODEL is None:
        _EMB_MODEL = SentenceTransformer(
            "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
        )
    return _EMB_MODEL


class ProductService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @property
    def embedding_model(self) -> SentenceTransformer:
        # Ленивая модель-синглтон: конструктор ProductService остаётся дешёвым
        # (важно, когда сервис нужен лишь для get_or_create_supplier в синхронной
        # части запроса — модель грузить не нужно). Тяжёлую загрузку прогреваем
        # в фоне через asyncio.to_thread(get_embedding_model).
        return get_embedding_model()

    @staticmethod
    def _cell(row, key: str, default: str = "") -> str:
        """NaN-безопасное чтение ячейки. Пустая ячейка CSV читается pandas как
        NaN, а str(NaN) == 'nan' — поэтому без этой нормализации пустые поля
        превращались в строку 'nan'. Возвращает default, если значение
        отсутствует/NaN/пустое после strip."""
        val = row.get(key)
        if val is None or pd.isna(val):
            return default
        s = str(val).strip()
        return s if s else default

    @staticmethod
    def _internal_sku(name: str, manufacturer: str | None) -> str:
        """Детерминированный внутренний артикул для товара без «Артикула».
        Основан на имени+производителе, поэтому повторная загрузка того же
        прайса не плодит дубли (тот же товар → тот же артикул → ветка
        'существующий')."""
        basis = f"{name}|{manufacturer or ''}".lower()
        digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]
        return f"{INTERNAL_SKU_PREFIX}{digest}"


    def parse_price(self, price_str) -> float:
        """Надежный парсер цен: убирает ₽, пробелы, буквы, меняет , на ."""
        if pd.isna(price_str):
            return 0.0
        
        text = str(price_str).strip()
        # Оставляем только цифры, точку и запятую
        text = re.sub(r'[^\d.,]', '', text)
        # Заменяем запятую на точку
        text = text.replace(',', '.')
        
        try:
            val = float(text)
            return val if val > 0 else 0.0
        except ValueError:
            return 0.0
    
    async def get_or_create_supplier(
        self,
        name: str,
        short_name: str = None,
        inn: str = None,
        contact_person: str = None,
        phone: str = None,
        email: str = None
    ) -> int:
        if inn:
            result = await self.db.execute(
                text("SELECT id FROM suppliers WHERE inn = :inn"),
                {"inn": inn}
            )
            existing = result.fetchone()
            if existing:
                return existing[0]
        
        result = await self.db.execute(
            text("""
                INSERT INTO suppliers (name, short_name, inn, contact_person, phone, email)
                VALUES (:name, :short_name, :inn, :contact_person, :phone, :email)
                RETURNING id
            """),
            {
                "name": name, "short_name": short_name, "inn": inn,
                "contact_person": contact_person, "phone": phone, "email": email
            }
        )
        supplier_id = result.scalar()
        await self.db.commit()
        return supplier_id
    
    async def import_products_from_csv(self, df: pd.DataFrame, supplier_id: int,
                                       progress=None) -> dict:
        """progress: callable(processed, total, counters, message) — для
        индикации прогресса в UI (фоновая задача)."""
        imported = 0
        updated = 0
        auto_sku = 0  # сколько товаров получили внутренний артикул
        errors = []
        to_embed = []  # (product_id, name) новых товаров — эмбеддинги считаем
                       # одним батчем после цикла (по одному — на порядок медленнее)

        # Убедимся, что колонки без лишних пробелов
        df.columns = [col.strip() for col in df.columns]
        total = len(df)

        def report(processed, message=""):
            if progress:
                progress(processed, total,
                         {"imported": imported, "updated": updated,
                          "auto_sku": auto_sku, "errors": len(errors)},
                         message)

        for idx, row in df.iterrows():
            try:
                sku = self._cell(row, 'Артикул')
                name = self._cell(row, 'Наименование')
                description = self._cell(row, 'Описание') or None
                unit = self._cell(row, 'Ед. изм.', 'шт')
                manufacturer = self._cell(row, 'Производитель') or None

                cost_price = self.parse_price(row.get('Себестоимость'))
                retail_price = self.parse_price(row.get('РРЦ'))

                if cost_price == 0 or retail_price == 0:
                    errors.append(f"Строка {idx + 1}: некорректные цены (Себестоимость={row.get('Себестоимость')}, РРЦ={row.get('РРЦ')})")
                    continue

                vat_included = False
                if pd.notna(row.get('НДС включен')):
                    vat_value = str(row.get('НДС включен')).strip().lower()
                    vat_included = vat_value in ['да', 'yes', 'true', '1']

                # Наименование обязательно: без него товар бессмысленен и нечем
                # сгенерировать внутренний артикул.
                if not name:
                    errors.append(f"Строка {idx + 1}: отсутствует Наименование")
                    continue

                # Пустой «Артикул» — не ошибка: присваиваем внутренний
                # (детерминированный по имени). supplier_sku при этом NULL,
                # т.к. поставщик артикул не предоставил.
                supplier_sku = sku or None
                if not sku:
                    sku = self._internal_sku(name, manufacturer)
                    auto_sku += 1

                # Матчинг В РАМКАХ ПОСТАВЩИКА: товар считается «уже импортированным»
                # только если ЭТОТ поставщик уже привязан к товару с таким
                # артикулом. Одинаковый артикул у разных поставщиков = разные
                # товары (разные предложения), каждый классифицируется отдельно.
                result = await self.db.execute(
                    text("""
                        SELECT p.id FROM products p
                        JOIN supplier_products sp
                          ON sp.product_id = p.id AND sp.supplier_id = :supplier_id
                        WHERE p.sku = :sku
                        LIMIT 1
                    """),
                    {"sku": sku, "supplier_id": supplier_id}
                )
                existing_product = result.fetchone()

                if existing_product:
                    # Повторная загрузка тем же поставщиком — обновляем цену.
                    product_id = existing_product[0]
                    await self.db.execute(
                        text("""
                            UPDATE supplier_products
                            SET cost_price = :cost_price, retail_price = :retail_price,
                                supplier_sku = :supplier_sku
                            WHERE supplier_id = :supplier_id AND product_id = :product_id
                        """),
                        {"cost_price": cost_price, "retail_price": retail_price,
                         "supplier_sku": supplier_sku, "supplier_id": supplier_id,
                         "product_id": product_id}
                    )
                    updated += 1
                else:
                    result = await self.db.execute(
                        text("""
                            INSERT INTO products (sku, name, description, unit, manufacturer, vat_included)
                            VALUES (:sku, :name, :description, :unit, :manufacturer, :vat_included)
                            RETURNING id
                        """),
                        {
                            "sku": sku, "name": name, "description": description,
                            "unit": unit, "manufacturer": manufacturer, "vat_included": vat_included
                        }
                    )
                    product_id = result.scalar()

                    await self.db.execute(
                        text("""
                            INSERT INTO supplier_products (supplier_id, product_id, supplier_sku, cost_price, retail_price)
                            VALUES (:supplier_id, :product_id, :supplier_sku, :cost_price, :retail_price)
                        """),
                        {"supplier_id": supplier_id, "product_id": product_id, "supplier_sku": supplier_sku, "cost_price": cost_price, "retail_price": retail_price}
                    )

                    # Эмбеддинг считаем позже одним батчем (см. ниже). Пока товар
                    # вставлен с embedding=NULL.
                    to_embed.append((product_id, name))
                    imported += 1

                await self.db.commit()

            except Exception as e:
                errors.append(f"Строка {idx + 1}: {str(e)}")
                await self.db.rollback()

            # Прогресс по строкам (не на каждой — каждые 10 и в конце).
            if (idx + 1) % 10 == 0 or (idx + 1) == total:
                report(idx + 1)

        # Батч-эмбеддинг всех новых товаров одним вызовом модели (на порядок
        # быстрее, чем encode по одному в цикле). encode — CPU-bound и блокирует
        # event loop, поэтому считаем в отдельном потоке (await to_thread), чтобы
        # опрос статуса оставался отзывчивым. Товары уже закоммичены; если процесс
        # упадёт здесь — эмбеддинги дозальёт scripts/regenerate_product_embeddings.py.
        if to_embed:
            report(total, message=f"Векторизация {len(to_embed)} товаров…")
            names = [n for _, n in to_embed]
            vectors = await asyncio.to_thread(
                self.embedding_model.encode, names, batch_size=64
            )
            for (product_id, _), vec in zip(to_embed, vectors):
                emb_str = "[" + ",".join(str(x) for x in vec.tolist()) + "]"
                await self.db.execute(
                    text("UPDATE products SET embedding = CAST(:embedding AS vector) WHERE id = :id"),
                    {"embedding": emb_str, "id": product_id}
                )
            await self.db.commit()
        
        return {"imported": imported, "updated": updated,
                "auto_sku": auto_sku, "errors": errors}