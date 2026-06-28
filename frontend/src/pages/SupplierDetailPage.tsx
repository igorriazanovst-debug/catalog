import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  autoMap,
  listProducts,
  listSuppliers,
  pollJob,
  type ClassifyResult,
  type Job,
  type MappingStatus,
  type Product,
  type Supplier,
} from "../api";
import ReviewPanel from "../components/ReviewPanel";
import JobProgress from "../components/JobProgress";

const STATUS_LABEL: Record<MappingStatus, string> = {
  auto: "Авто",
  manual: "На проверке",
  rejected: "Отклонён",
  unmapped: "Без маппинга",
};

type Filter = MappingStatus | "all";

export default function SupplierDetailPage() {
  const { id } = useParams();
  const supplierId = Number(id);

  const [supplier, setSupplier] = useState<Supplier | null>(null);
  const [products, setProducts] = useState<Product[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [reviewing, setReviewing] = useState<Product | null>(null);

  const [classifyJob, setClassifyJob] = useState<Job | null>(null);
  const [classifyError, setClassifyError] = useState<string | null>(null);
  const [classifyDone, setClassifyDone] = useState<ClassifyResult | null>(null);
  const mapping = classifyJob?.status === "running";

  const reload = useCallback(async () => {
    setError(null);
    try {
      const [sups, prods] = await Promise.all([
        listSuppliers(),
        listProducts({ supplier_id: supplierId, limit: 1000 }),
      ]);
      setSupplier(sups.find((s) => s.id === supplierId) ?? null);
      setProducts(prods);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [supplierId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const counts = useMemo(() => {
    const c = { auto: 0, manual: 0, rejected: 0, unmapped: 0 };
    for (const p of products ?? []) c[p.status]++;
    return c;
  }, [products]);

  const shown = useMemo(
    () =>
      (products ?? []).filter((p) => filter === "all" || p.status === filter),
    [products, filter]
  );

  async function runAutoMap(onlyUnmapped: boolean) {
    setClassifyJob(null);
    setClassifyError(null);
    setClassifyDone(null);
    setError(null);
    try {
      const { job_id } = await autoMap({
        supplier_id: supplierId,
        only_unmapped: onlyUnmapped,
      });
      const finished = await pollJob(job_id, setClassifyJob);
      if (finished.status === "done") {
        setClassifyDone(finished.result as ClassifyResult);
      } else {
        // Серия ошибок GPT / иной сбой — показываем причину явно.
        setClassifyError(finished.error || "Классификация завершилась с ошибкой.");
      }
      await reload();
    } catch (e) {
      setClassifyError((e as Error).message);
    }
  }

  return (
    <>
      <p style={{ margin: "0 0 6px" }}>
        <Link to="/">← Поставщики</Link>
      </p>
      <h1>{supplier ? supplier.name : "Поставщик"}</h1>

      {error && <div className="error">{error}</div>}

      <div className="stats">
        <div className="stat">
          <div className="n">{products?.length ?? "—"}</div>
          <div className="l">товаров</div>
        </div>
        <div className="stat">
          <div className="n" style={{ color: "var(--green)" }}>
            {counts.auto}
          </div>
          <div className="l">авто</div>
        </div>
        <div className="stat">
          <div className="n" style={{ color: "var(--amber)" }}>
            {counts.manual}
          </div>
          <div className="l">на проверке</div>
        </div>
        <div className="stat">
          <div className="n">{counts.unmapped}</div>
          <div className="l">без маппинга</div>
        </div>
      </div>

      <div className="row-actions">
        <button disabled={mapping} onClick={() => runAutoMap(true)}>
          {mapping ? (
            <>
              <span className="spinner" /> Классификация…
            </>
          ) : (
            "Классифицировать новые"
          )}
        </button>
        <button
          className="secondary"
          disabled={mapping}
          onClick={() => runAutoMap(false)}
        >
          Переклассифицировать все
        </button>
      </div>

      {mapping && (
        <div className="notice">
          Идёт классификация: гибридный ретрив + LLM-судья (YandexGPT). Прогресс
          обновляется автоматически; можно не закрывать вкладку. Если GPT начнёт
          стабильно сбоить (100 ошибок подряд), процесс остановится с понятной
          ошибкой.
        </div>
      )}

      {classifyJob && classifyJob.status === "running" && (
        <JobProgress job={classifyJob} />
      )}

      {classifyError && (
        <div className="error">
          Классификация прервана: {classifyError}
        </div>
      )}

      {classifyDone && (
        <div className="notice">
          Готово: всего {classifyDone.total_products}, авто{" "}
          {classifyDone.auto_mapped}, на проверку {classifyDone.needs_review},
          без подходящего {classifyDone.no_match} (правило: {classifyDone.by_rule},
          LLM: {classifyDone.by_llm}).
          {classifyDone.llm_errors > 0 && (
            <> Ошибок GPT: {classifyDone.llm_errors}.</>
          )}
        </div>
      )}

      <h2>Товары</h2>
      <div className="toolbar">
        {(["all", "auto", "manual", "unmapped", "rejected"] as Filter[]).map(
          (f) => (
            <button
              key={f}
              className={filter === f ? "" : "secondary"}
              onClick={() => setFilter(f)}
            >
              {f === "all" ? "Все" : STATUS_LABEL[f]}
              {f !== "all" && ` (${counts[f]})`}
            </button>
          )
        )}
      </div>

      {!products && !error && (
        <p>
          <span className="spinner" /> Загрузка…
        </p>
      )}

      {products && (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Товар</th>
                <th>Сопоставление (Приказ 838)</th>
                <th>Статус</th>
                <th style={{ textAlign: "right" }}>РРЦ</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {shown.map((p) => (
                <tr key={p.id}>
                  <td>
                    <b>{p.name}</b>
                    <div className="code">{p.sku}</div>
                  </td>
                  <td>
                    {p.standard_name ? (
                      <>
                        {p.subsection_name && (
                          <span className="muted">[{p.subsection_name}] </span>
                        )}
                        {p.standard_name}{" "}
                        <span className="code">{p.full_code}</span>
                      </>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>
                    <span className={"badge " + p.status}>
                      {STATUS_LABEL[p.status]}
                    </span>
                  </td>
                  <td style={{ textAlign: "right" }}>
                    {p.retail_price != null
                      ? p.retail_price.toLocaleString("ru-RU") + " ₽"
                      : "—"}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <button
                      className="ghost sm"
                      disabled={p.mapping_id == null}
                      title={
                        p.mapping_id == null
                          ? "Сначала классифицируйте товар"
                          : "Проверить и при необходимости исправить"
                      }
                      onClick={() => setReviewing(p)}
                    >
                      Проверить
                    </button>
                  </td>
                </tr>
              ))}
              {shown.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted" style={{ padding: 20 }}>
                    Нет товаров в этой категории.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {reviewing && (
        <ReviewPanel
          product={reviewing}
          onClose={() => setReviewing(null)}
          onResolved={() => {
            setReviewing(null);
            reload();
          }}
        />
      )}
    </>
  );
}
