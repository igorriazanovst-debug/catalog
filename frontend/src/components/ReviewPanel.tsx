import { useEffect, useState } from "react";
import {
  approveMapping,
  productCandidates,
  reassignMapping,
  rejectMapping,
  type Candidate,
  type Product,
} from "../api";

export default function ReviewPanel({
  product,
  onClose,
  onResolved,
}: {
  product: Product;
  onClose: () => void;
  onResolved: () => void;
}) {
  const [cands, setCands] = useState<Candidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<number | null>(product.standard_id);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setError(null);
    productCandidates(product.id)
      .then((list) => {
        // Текущий стандарт всегда в списке, даже если выпал из пула кандидатов.
        if (
          product.standard_id != null &&
          !list.some((c) => c.standard_id === product.standard_id)
        ) {
          list.unshift({
            standard_id: product.standard_id,
            standard_name: product.standard_name || "(текущий)",
            full_code: product.full_code,
            subsection_name: product.subsection_name,
            sources: ["текущий"],
            vector_similarity: null,
            keyword_score: null,
          });
        }
        setCands(list);
      })
      .catch((e) => setError(e.message));
  }, [product]);

  async function run(action: () => Promise<unknown>) {
    if (product.mapping_id == null) {
      setError("У товара нет записи маппинга — сначала классифицируйте его.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await action();
      onResolved();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="toolbar">
          <h2 style={{ margin: 0 }}>Проверка сопоставления</h2>
          <div className="spacer" />
          <button className="ghost" onClick={onClose}>
            ✕
          </button>
        </div>

        <p style={{ margin: "2px 0" }}>
          <b>{product.name}</b>
        </p>
        <div className="muted" style={{ fontSize: 13 }}>
          SKU: {product.sku || "—"}
          {product.match_reason ? ` · ${product.match_reason}` : ""}
        </div>
        {product.description && (
          <p className="muted" style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>
            {product.description}
          </p>
        )}

        {error && <div className="error">{error}</div>}

        <h2>Кандидаты</h2>
        {!cands && !error && (
          <p>
            <span className="spinner" /> Подбор кандидатов (первый запрос грузит
            модель эмбеддингов, ~минуту)…
          </p>
        )}
        {cands &&
          cands.map((c) => {
            const isCurrent = c.standard_id === product.standard_id;
            return (
              <label
                key={c.standard_id}
                className={"cand" + (isCurrent ? " current" : "")}
              >
                <input
                  type="radio"
                  name="cand"
                  checked={chosen === c.standard_id}
                  onChange={() => setChosen(c.standard_id)}
                />
                <div>
                  <div>
                    {c.subsection_name && (
                      <span className="muted">[{c.subsection_name}] </span>
                    )}
                    <b>{c.standard_name}</b>{" "}
                    <span className="code">{c.full_code || ""}</span>
                    {isCurrent && <span className="tag">текущий</span>}
                  </div>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {c.sources.map((s) => (
                      <span key={s} className="tag">
                        {s}
                      </span>
                    ))}
                    {c.vector_similarity != null &&
                      ` vec ${c.vector_similarity.toFixed(2)}`}
                    {c.keyword_score != null &&
                      ` kw ${c.keyword_score.toFixed(1)}`}
                  </div>
                </div>
              </label>
            );
          })}

        <div className="row-actions" style={{ marginTop: 16 }}>
          <button
            className="approve"
            disabled={busy}
            onClick={() => run(() => approveMapping(product.mapping_id!))}
          >
            ✓ Подтвердить текущий
          </button>
          <button
            disabled={busy || chosen == null || chosen === product.standard_id}
            onClick={() =>
              run(() => reassignMapping(product.mapping_id!, chosen!))
            }
          >
            ↻ Назначить выбранный
          </button>
          <button
            className="reject"
            disabled={busy}
            onClick={() => run(() => rejectMapping(product.mapping_id!))}
          >
            ✗ Отклонить
          </button>
          {busy && <span className="spinner" />}
        </div>
      </div>
    </div>
  );
}
