import re
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from sentence_transformers import SentenceTransformer

class ProductService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.embedding_model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-mpnet-base-v2")
    
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
    
    async def import_products_from_csv(self, df: pd.DataFrame, supplier_id: int) -> dict:
        imported = 0
        updated = 0
        errors = []
        
        # Убедимся, что колонки без лишних пробелов
        df.columns = [col.strip() for col in df.columns]
        
        for idx, row in df.iterrows():
            try:
                sku = str(row.get('Артикул', '')).strip()
                name = str(row.get('Наименование', '')).strip()
                description = str(row.get('Описание', '')).strip() if pd.notna(row.get('Описание')) else None
                unit = str(row.get('Ед. изм.', 'шт')).strip()
                
                cost_price = self.parse_price(row.get('Себестоимость'))
                retail_price = self.parse_price(row.get('РРЦ'))
                
                if cost_price == 0 or retail_price == 0:
                    errors.append(f"Строка {idx + 1}: некорректные цены (Себестоимость={row.get('Себестоимость')}, РРЦ={row.get('РРЦ')})")
                    continue
                
                vat_included = False
                if pd.notna(row.get('НДС включен')):
                    vat_value = str(row.get('НДС включен')).strip().lower()
                    vat_included = vat_value in ['да', 'yes', 'true', '1']
                
                manufacturer = str(row.get('Производитель', '')).strip() if pd.notna(row.get('Производитель')) else None
                
                if not sku or not name:
                    errors.append(f"Строка {idx + 1}: отсутствуют Артикул или Наименование")
                    continue
                
                result = await self.db.execute(
                    text("SELECT id FROM products WHERE sku = :sku"),
                    {"sku": sku}
                )
                existing_product = result.fetchone()
                
                if existing_product:
                    product_id = existing_product[0]
                    result = await self.db.execute(
                        text("SELECT id FROM supplier_products WHERE supplier_id = :supplier_id AND product_id = :product_id"),
                        {"supplier_id": supplier_id, "product_id": product_id}
                    )
                    existing_link = result.fetchone()
                    
                    if existing_link:
                        await self.db.execute(
                            text("UPDATE supplier_products SET cost_price = :cost_price, retail_price = :retail_price WHERE id = :id"),
                            {"cost_price": cost_price, "retail_price": retail_price, "id": existing_link[0]}
                        )
                    else:
                        await self.db.execute(
                            text("""
                                INSERT INTO supplier_products (supplier_id, product_id, supplier_sku, cost_price, retail_price)
                                VALUES (:supplier_id, :product_id, :supplier_sku, :cost_price, :retail_price)
                            """),
                            {"supplier_id": supplier_id, "product_id": product_id, "supplier_sku": sku, "cost_price": cost_price, "retail_price": retail_price}
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
                        {"supplier_id": supplier_id, "product_id": product_id, "supplier_sku": sku, "cost_price": cost_price, "retail_price": retail_price}
                    )
                    
                    embedding = self.embedding_model.encode(name)
                    emb_str = "[" + ",".join(str(x) for x in embedding.tolist()) + "]"
                    
                    await self.db.execute(
                        text("UPDATE products SET embedding = CAST(:embedding AS vector) WHERE id = :id"),
                        {"embedding": emb_str, "id": product_id}
                    )
                    imported += 1
                
                await self.db.commit()
                
            except Exception as e:
                errors.append(f"Строка {idx + 1}: {str(e)}")
                await self.db.rollback()
        
        return {"imported": imported, "updated": updated, "errors": errors}