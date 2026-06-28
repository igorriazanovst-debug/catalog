import type { Job } from "../api";

const COUNTER_LABELS: Record<string, string> = {
  imported: "новых",
  updated: "обновлено",
  auto_sku: "внутр. артикул",
  auto_mapped: "авто",
  needs_review: "на проверку",
  no_match: "без совпадения",
  by_rule: "правилом",
  by_llm: "LLM",
  llm_errors: "ошибки GPT",
  errors: "ошибок",
};

export default function JobProgress({ job }: { job: Job }) {
  const pct = job.total ? Math.round((job.processed / job.total) * 100) : 0;
  const entries = Object.entries(job.counters).filter(([, v]) => v != null);

  return (
    <div className="card">
      <div className="progress">
        <div style={{ width: `${pct}%` }} />
      </div>
      <div className="muted" style={{ fontSize: 13 }}>
        {job.processed}
        {job.total ? ` / ${job.total}` : ""} ({pct}%)
        {job.message ? ` · ${job.message}` : ""} · {job.elapsed}s
      </div>
      {entries.length > 0 && (
        <div className="stats" style={{ marginTop: 10 }}>
          {entries.map(([k, v]) => (
            <div className="stat" key={k}>
              <div
                className="n"
                style={{
                  color:
                    k === "errors" || k === "llm_errors"
                      ? v
                        ? "var(--red)"
                        : undefined
                      : k === "auto_mapped" || k === "imported"
                      ? "var(--green)"
                      : undefined,
                }}
              >
                {v}
              </div>
              <div className="l">{COUNTER_LABELS[k] || k}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
