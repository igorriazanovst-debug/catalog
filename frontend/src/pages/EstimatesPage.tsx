import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listEstimates, deleteEstimate, type EstimateListItem } from "../api";

function money(x: number): string {
  return x.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export default function EstimatesPage() {
  const [items, setItems] = useState<EstimateListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    listEstimates().then(setItems).catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  async function onDelete(id: number, name: string) {
    if (!confirm(`Удалить смету «${name}»?`)) return;
    try {
      await deleteEstimate(id);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <>
      <h1>Сметы</h1>
      <p className="muted">
        Загрузите входящую смету (xlsx) — система разберёт позиции, сопоставит их с
        Приказом 838 и подберёт товары поставщиков с ценами.
      </p>

      <div className="row-actions" style={{ marginBottom: 14 }}>
        <Link to="/estimates/upload">
          <button>+ Загрузить смету</button>
        </Link>
      </div>

      {error && <div className="error">{error}</div>}
      {!items && !error && (
        <p>
          <span className="spinner" /> Загрузка…
        </p>
      )}

      {items && items.length === 0 && (
        <div className="card">
          Пока нет смет. <Link to="/estimates/upload">Загрузить первую →</Link>
        </div>
      )}

      {items && items.length > 0 && (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Смета</th>
                <th style={{ textAlign: "right" }}>Позиций</th>
                <th style={{ textAlign: "right" }}>С подбором</th>
                <th style={{ textAlign: "right" }}>Сумма</th>
                <th>Создана</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <tr key={e.id}>
                  <td>
                    <Link to={`/estimates/${e.id}`}>
                      <b>{e.name}</b>
                    </Link>
                  </td>
                  <td style={{ textAlign: "right" }}>{e.items}</td>
                  <td
                    style={{
                      textAlign: "right",
                      color: e.matched < e.items ? "var(--amber)" : "var(--green)",
                    }}
                  >
                    {e.matched} / {e.items}
                  </td>
                  <td style={{ textAlign: "right" }}>{money(e.total_amount)} ₽</td>
                  <td className="muted">
                    {e.created_at ? new Date(e.created_at).toLocaleString("ru-RU") : "—"}
                  </td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <Link to={`/estimates/${e.id}`}>Открыть →</Link>
                    <button
                      className="secondary"
                      style={{ marginLeft: 10 }}
                      onClick={() => onDelete(e.id, e.name)}
                    >
                      Удалить
                    </button>
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
