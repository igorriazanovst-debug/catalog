import { Fragment, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  chooseItem,
  classifyEstimate,
  classifyItem,
  exportEstimateUrl,
  getEstimate,
  itemCandidates,
  listProviders,
  pollJob,
  type EstimateDetail,
  type EstimateItem,
  type EstimateOffer,
  type Job,
  type Provider,
} from "../api";
import JobProgress from "../components/JobProgress";

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
  const [rowBusy, setRowBusy] = useState<number | null>(null);

  // Настройки классификации
  const [providers, setProviders] = useState<Provider[]>([]);
  const [provider, setProvider] = useState<string>(""); // "" = без LLM
  const [decompose, setDecompose] = useState(true);
  const [priceBasis, setPriceBasis] = useState<"cost" | "retail">("cost");
  const [job, setJob] = useState<Job | null>(null);
  const [classifying, setClassifying] = useState(false);

  const load = () =>
    getEstimate(estimateId).then(setEst).catch((e) => setError(e.message));
  useEffect(() => {
    load();
    listProviders()
      .then((ps) => {
        setProviders(ps);
        const def = ps.find((p) => p.configured && p.default) || ps.find((p) => p.configured);
        if (def) setProvider(def.id);
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [estimateId]);

  async function autoClassify() {
    setError(null);
    setClassifying(true);
    setJob(null);
    try {
      const useLlm = provider !== "";
      const { job_id } = await classifyEstimate(estimateId, {
        use_llm: useLlm,
        provider: useLlm ? provider : undefined,
        decompose: useLlm && decompose,
        price_basis: priceBasis,
      });
      const finished = await pollJob(job_id, setJob);
      if (finished.status !== "done") setError(finished.error || "Ошибка классификации.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setClassifying(false);
      setJob(null);
      await load();
    }
  }

  async function classifyRow(item: EstimateItem, useLlm: boolean) {
    setError(null);
    setRowBusy(item.id);
    try {
      await classifyItem(estimateId, item.id, useLlm, provider || undefined);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRowBusy(null);
    }
  }

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
    setRowBusy(item.id);
    try {
      await chooseItem(estimateId, item.id, offer.product_id, offer.supplier_id, priceBasis);
      setOpenItem(null);
      setCands(null);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRowBusy(null);
    }
  }

  if (error && !est) return <div className="error">{error}</div>;
  if (!est)
    return (
      <p>
        <span className="spinner" /> Загрузка…
      </p>
    );

  const matched = est.items.filter((i) => i.product_id != null).length;
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

      {/* Панель классификации */}
      <div className="card">
        <div className="grid2">
          <div className="field" style={{ marginBottom: 0 }}>
            <label>Метод подбора стандарта 838</label>
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="">Без LLM (текстовый)</option>
              {providers.map((p) => (
                <option key={p.id} value={p.id} disabled={!p.configured}>
                  С LLM: {p.label}
                  {p.configured ? "" : " (не настроен)"}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>Цена</label>
            <select
              value={priceBasis}
              onChange={(e) => setPriceBasis(e.target.value as "cost" | "retail")}
            >
              <option value="cost">Себестоимость</option>
              <option value="retail">РРЦ</option>
            </select>
          </div>
        </div>
        <label
          style={{ display: "flex", gap: 8, alignItems: "center", margin: "10px 0", fontSize: 14 }}
        >
          <input
            type="checkbox"
            checked={decompose}
            disabled={provider === ""}
            onChange={(e) => setDecompose(e.target.checked)}
          />
          Разлагать наборы на вложения (требует LLM)
        </label>
        <div className="row-actions">
          <button onClick={autoClassify} disabled={classifying}>
            {classifying ? "Классификация…" : "Авто-классификация (все строки)"}
          </button>
          <span className="muted" style={{ fontSize: 13 }}>
            или классифицируйте строки по одной кнопками в таблице →
          </span>
        </div>
        {job && (
          <div style={{ marginTop: 10 }}>
            <JobProgress job={job} />
          </div>
        )}
      </div>

      {error && <div className="error">{error}</div>}

      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Наименование (смета)</th>
              <th>Описание (смета)</th>
              <th style={{ borderLeft: "2px solid var(--line)" }}>Артикул</th>
              <th>Наименование (подбор)</th>
              <th>Описание (подбор)</th>
              <th style={{ textAlign: "right" }}>Кол-во</th>
              <th style={{ textAlign: "right" }}>Цена за ед.</th>
              <th style={{ textAlign: "right" }}>Итого</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {est.items.map((it) => {
              const groupHeader =
                it.group_name && it.group_name !== lastGroup ? it.group_name : null;
              lastGroup = it.group_name;
              const open = openItem === it.id;
              const busy = rowBusy === it.id;
              return (
                <Fragment key={it.id}>
                  {groupHeader && (
                    <tr>
                      <td colSpan={9} style={{ background: "#f0f4fa", fontWeight: 600 }}>
                        Набор: {groupHeader}
                      </td>
                    </tr>
                  )}
                  <tr>
                    <td style={{ paddingLeft: it.group_name ? 22 : undefined, maxWidth: 240 }}>
                      {it.source_name || "—"}
                      {it.match_method && (
                        <div className="muted" style={{ fontSize: 12 }}>
                          {METHOD_LABELS[it.match_method] || it.match_method}
                          {it.standard_id ? (
                            <>
                              {" · "}
                              <span className="code">{it.full_code || ""}</span>{" "}
                              {it.standard_name}
                            </>
                          ) : null}
                        </div>
                      )}
                    </td>
                    <td className="muted" style={{ fontSize: 12, maxWidth: 220 }} title={it.source_description || ""}>
                      {it.source_description
                        ? it.source_description.slice(0, 140) +
                          (it.source_description.length > 140 ? "…" : "")
                        : "—"}
                    </td>
                    <td className="code" style={{ borderLeft: "2px solid var(--line)" }}>
                      {it.sku || "—"}
                    </td>
                    <td style={{ maxWidth: 240 }}>
                      {it.product_id ? (
                        <>
                          {it.product_name}
                          {it.supplier_name && (
                            <div className="muted" style={{ fontSize: 12 }}>
                              {it.supplier_name}
                            </div>
                          )}
                        </>
                      ) : it.standard_id ? (
                        <span className="badge manual">товар не подобран</span>
                      ) : (
                        <span className="badge unmapped">не классиф.</span>
                      )}
                    </td>
                    <td
                      className="muted"
                      style={{ fontSize: 12, maxWidth: 200 }}
                      title={it.product_description || ""}
                    >
                      {it.product_description
                        ? it.product_description.slice(0, 120) +
                          (it.product_description.length > 120 ? "…" : "")
                        : "—"}
                    </td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      {it.quantity ?? "—"} {it.unit || ""}
                    </td>
                    <td style={{ textAlign: "right" }}>{money(it.unit_price)}</td>
                    <td style={{ textAlign: "right" }}>{money(it.total_price)}</td>
                    <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                      {busy ? (
                        <span className="spinner" />
                      ) : (
                        <>
                          <button
                            className="secondary"
                            title="Классифицировать без LLM (текстовый подбор)"
                            onClick={() => classifyRow(it, false)}
                          >
                            без LLM
                          </button>{" "}
                          <button
                            className="secondary"
                            disabled={provider === ""}
                            title={
                              provider === ""
                                ? "Выберите LLM-провайдера в панели выше"
                                : "Классифицировать с LLM"
                            }
                            onClick={() => classifyRow(it, true)}
                          >
                            с LLM
                          </button>{" "}
                          {it.standard_id && (
                            <button onClick={() => toggleCandidates(it)}>
                              {open ? "Скрыть" : "Товар…"}
                            </button>
                          )}
                        </>
                      )}
                    </td>
                  </tr>
                  {open && (
                    <tr>
                      <td colSpan={9} style={{ background: "#fbfcfe" }}>
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
                                      {o.sku ? <span className="muted"> · {o.sku}</span> : null}
                                    </td>
                                    <td>{o.supplier_name}</td>
                                    <td style={{ textAlign: "right" }}>{money(o.cost_price)}</td>
                                    <td style={{ textAlign: "right" }}>{money(o.retail_price)}</td>
                                    <td style={{ textAlign: "right" }}>
                                      <span className={"badge " + (o.is_manual ? "manual" : "auto")}>
                                        {o.is_manual ? "на проверке" : "авто"}
                                        {o.match_score != null ? ` ${o.match_score.toFixed(2)}` : ""}
                                      </span>
                                    </td>
                                    <td style={{ textAlign: "right" }}>
                                      {chosen ? (
                                        <span className="muted">выбран</span>
                                      ) : (
                                        <button onClick={() => choose(it, o)}>Выбрать</button>
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
