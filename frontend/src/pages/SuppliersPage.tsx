import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listSuppliers, type Supplier } from "../api";

export default function SuppliersPage() {
  const [suppliers, setSuppliers] = useState<Supplier[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listSuppliers().then(setSuppliers).catch((e) => setError(e.message));
  }, []);

  return (
    <>
      <h1>Поставщики</h1>
      <p className="muted">
        Загрузите прайс-лист поставщика, затем классифицируйте его товары по
        позициям Приказа Минпросвещения №838.
      </p>

      {error && <div className="error">{error}</div>}
      {!suppliers && !error && (
        <p>
          <span className="spinner" /> Загрузка…
        </p>
      )}

      {suppliers && suppliers.length === 0 && (
        <div className="card">
          Пока нет поставщиков.{" "}
          <Link to="/upload">Загрузить первый прайс-лист →</Link>
        </div>
      )}

      {suppliers && suppliers.length > 0 && (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Поставщик</th>
                <th>ИНН</th>
                <th style={{ textAlign: "right" }}>Товаров</th>
                <th style={{ textAlign: "right" }}>Авто</th>
                <th style={{ textAlign: "right" }}>На проверке</th>
                <th style={{ textAlign: "right" }}>Без маппинга</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {suppliers.map((s) => (
                <tr key={s.id}>
                  <td>
                    <Link to={`/supplier/${s.id}`}>
                      <b>{s.name}</b>
                    </Link>
                    {s.short_name && (
                      <div className="muted">{s.short_name}</div>
                    )}
                  </td>
                  <td className="code">{s.inn || "—"}</td>
                  <td style={{ textAlign: "right" }}>{s.products_total}</td>
                  <td style={{ textAlign: "right", color: "var(--green)" }}>
                    {s.auto}
                  </td>
                  <td style={{ textAlign: "right", color: "var(--amber)" }}>
                    {s.manual}
                  </td>
                  <td style={{ textAlign: "right" }} className="muted">
                    {s.unmapped}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <Link to={`/supplier/${s.id}`}>Открыть →</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
