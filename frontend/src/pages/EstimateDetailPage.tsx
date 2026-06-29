import { Fragment, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  chooseItem,
  exportEstimateUrl,
  getEstimate,
  itemCandidates,
  type EstimateDetail,
  type EstimateItem,
  type EstimateOffer,
} from "../api";

function money(x: number | null): string {
  if (x == null) return "—";
  return x.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

const METHOD_LABELS: Record<string, string> = {
  ktru: "по КТРУ",
  okpd2: "по ОКПД2",
  rule: "правило",
  "text+llm": "текст+LLM",
  text: "текст",
  manual: "вручную",
  none: "не сопоставлено",
};

export default function EstimateDetailPage() {
  const { id } = useParams();
  const estimateId = Number(id);
  const [est, setEst] = useState<EstimateDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openItem, setOpenItem] = useState<number | null>(null);
  const [cands, setCands] = useState<EstimateOffer[] | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () =>
    getEstimate(estimateId).then(setEst).catch((e) => setError(e.message));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [estimateId]);

  async function toggleCandidates(item: EstimateItem) {
    if (openItem === item.id) {
      setOpenItem(null);
      setCands(null);
      return;
    }
    setOpenItem(item.id);
    setCands(null);
    try {
      setCands(await itemCandidates(estimateId, item.id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function choose(item: EstimateItem, offer: EstimateOffer) {
    setBusy(true);
    try {
      await chooseItem(estimateId, item.id, offer.product_id, offer.supplier_id);
      setOpenItem(null);
      setCands(null);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error) return <div className="error">{error}</div>;
  if (!est)
    return (
      <p>
        <span className="spinner" /> Загрузка…
      </p>
    );

  const matched = est.items.filter((i) => i.product_id != null).length;

  // Группировка: вложения набора идут подряд с одинаковым group_name.
  let lastGroup: string | null = null;

  return (
    <>
      <div style={{ marginBottom: 8 }}>
        <Link to="/estimates" className="muted">
          ← Сметы
        </Link>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0 }}>{est.name}</h1>
        <a href={exportEstimateUrl(est.id)}>
          <button className="secondary">Экспорт в xlsx</button>
        </a>
      </div>
      <p className="muted">
        Позиций: {est.items.length} · с подбором: {matched} · итог:{" "}
        <b>{money(est.total_amount)} ₽</b> (по выбранной цене, без НДС)
      </p>

      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>Наименование (смета)</th>
              <th>Позиция 838</th>
              <th>Товар / поставщик</th>
              <th style={{ textAlign: "right" }}>Кол-во</th>
              <th style={{ textAlign: "right" }}>Цена</th>
              <th style={{ textAlign: "right" }}>Стоимость</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {est.items.map((it) => {
              const groupHeader =
                it.group_name && it.group_name !== lastGroup ? it.group_name : null;
              lastGroup = it.group_name;
              const open = openItem === it.id;
              return (
                <Fragment key={it.id}>
                  {groupHeader && (
                    <tr>
                      <td colSpan={7} style={{ background: "#f0f4fa", fontWeight: 600 }}>
                        Набор: {groupHeader}
                      </td>
                    </tr>
                  )}
                  <tr>
                    <td style={{ paddingLeft: it.group_name ? 22 : undefined }}>
                      {it.source_name || "—"}
                      {it.match_method && (
                        <div className="muted" style={{ fontSize: 12 }}>
                          {METHOD_LABELS[it.match_method] || it.match_method}
                        </div>
                      )}
                    </td>
                    <td>
                      {it.standard_id ? (
                        <>
                          <span className="code">{it.full_code || ""}</span>{" "}
                          {it.standard_name}
                        </>
                      ) : (
                        <span className="badge unmapped">нет</span>
                      )}
                    </td>
                    <td>
                      {it.product_id ? (
                        <>
                          {it.product_name}
                          <div className="muted" style={{ fontSize: 12 }}>
                            {it.supplier_name}
                            {it.sku ? ` · ${it.sku}` : ""}
                          </div>
                        </>
                      ) : (
                        <span className="badge manual">не подобран</span>
                      )}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {it.quantity ?? "—"} {it.unit || ""}
                    </td>
                    <td style={{ textAlign: "right" }}>{money(it.unit_price)}</td>
                    <td style={{ textAlign: "right" }}>{money(it.total_price)}</td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      {it.standard_id ? (
                        <button className="secondary" onClick={() => toggleCandidates(it)}>
                          {open ? "Скрыть" : "Изменить"}
                        </button>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                  </tr>
                  {open && (
                    <tr>
                      <td colSpan={7} style={{ background: "#fbfcfe" }}>
                        {!cands && (
                          <span className="muted">
                            <span className="spinner" /> Загрузка вариантов…
                          </span>
                        )}
                        {cands && cands.length === 0 && (
                          <span className="muted">
                            Нет товаров, привязанных к этой позиции 838.
                          </span>
                        )}
                        {cands && cands.length > 0 && (
                          <table>
                            <thead>
                              <tr>
                                <th>Товар</th>
                                <th>Поставщик</th>
                                <th style={{ textAlign: "right" }}>Себест.</th>
                                <th style={{ textAlign: "right" }}>РРЦ</th>
                                <th style={{ textAlign: "right" }}>Маппинг</th>
                                <th></th>
                              </tr>
                            </thead>
                            <tbody>
                              {cands.map((o) => {
                                const chosen = o.product_id === it.product_id;
                                return (
                                  <tr key={`${o.product_id}-${o.supplier_id}`}>
                                    <td>
                                      {o.product_name}
                                      {o.sku ? (
                                        <span className="muted"> · {o.sku}</span>
                                      ) : null}
                                    </td>
                                    <td>{o.supplier_name}</td>
                                    <td style={{ textAlign: "right" }}>
                                      {money(o.cost_price)}
                                    </td>
                                    <td style={{ textAlign: "right" }}>
                                      {money(o.retail_price)}
                                    </td>
                                    <td style={{ textAlign: "right" }}>
                                      <span
                                        className={"badge " + (o.is_manual ? "manual" : "auto")}
                                      >
                                        {o.is_manual ? "на проверке" : "авто"}
                                        {o.match_score != null
                                          ? ` ${o.match_score.toFixed(2)}`
                                          : ""}
                                      </span>
                                    </td>
                                    <td style={{ textAlign: "right" }}>
                                      {chosen ? (
                                        <span className="muted">выбран</span>
                                      ) : (
                                        <button disabled={busy} onClick={() => choose(it, o)}>
                                          Выбрать
                                        </button>
                                      )}
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
