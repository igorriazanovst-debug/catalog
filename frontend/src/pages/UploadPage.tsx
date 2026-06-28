import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  pollJob,
  uploadPriceList,
  type ImportResult,
  type Job,
  type SupplierFields,
} from "../api";
import JobProgress from "../components/JobProgress";

const EMPTY: SupplierFields = {
  supplier_name: "",
  supplier_short_name: "",
  supplier_inn: "",
  supplier_contact_person: "",
  supplier_phone: "",
  supplier_email: "",
};

type Phase = "form" | "running" | "done" | "failed";

export default function UploadPage() {
  const [fields, setFields] = useState<SupplierFields>(EMPTY);
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const [phase, setPhase] = useState<Phase>("form");
  const [uploadPct, setUploadPct] = useState(0);
  const [job, setJob] = useState<Job | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [supplierId, setSupplierId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const set = (k: keyof SupplierFields, v: string) =>
    setFields((f) => ({ ...f, [k]: v }));

  const pickFile = (f: File | null) => {
    setError(null);
    if (f && !f.name.toLowerCase().endsWith(".csv")) {
      setError("Нужен файл в формате CSV (разделитель «;»).");
      return;
    }
    setFile(f);
  };

  function reset() {
    setPhase("form");
    setFile(null);
    setFields(EMPTY);
    setJob(null);
    setResult(null);
    setUploadPct(0);
    setError(null);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!fields.supplier_name.trim()) {
      setError("Укажите название поставщика.");
      return;
    }
    if (!file) {
      setError("Прикрепите CSV-файл прайс-листа.");
      return;
    }
    setPhase("running");
    setUploadPct(0);
    setJob(null);
    try {
      const started = await uploadPriceList(file, fields, setUploadPct);
      setSupplierId(started.supplier_id);
      const finished = await pollJob(started.job_id, setJob);
      if (finished.status === "done") {
        setResult(finished.result as ImportResult);
        setPhase("done");
      } else {
        setError(finished.error || "Импорт завершился с ошибкой.");
        setPhase("failed");
      }
    } catch (err) {
      setError((err as Error).message);
      setPhase("failed");
    }
  }

  if (phase === "done" && result) {
    return (
      <>
        <h1>Прайс-лист загружен</h1>
        <div className="card">
          <div className="stats">
            <div className="stat">
              <div className="n">{result.imported}</div>
              <div className="l">новых товаров</div>
            </div>
            <div className="stat">
              <div className="n">{result.updated}</div>
              <div className="l">обновлено</div>
            </div>
            <div className="stat">
              <div className="n" style={{ color: result.errors.length ? "var(--red)" : undefined }}>
                {result.errors.length}
              </div>
              <div className="l">строк с ошибками</div>
            </div>
          </div>

          {result.auto_sku > 0 && (
            <div className="notice">
              {result.auto_sku}{" "}
              {result.auto_sku === 1
                ? "товару присвоен внутренний артикул"
                : "товарам присвоены внутренние артикулы"}{" "}
              (в прайсе не был указан «Артикул»). Формат:{" "}
              <span className="code">AUTO-xxxxxxxxxx</span>.
            </div>
          )}

          {result.errors.length > 0 && (
            <details>
              <summary className="muted" style={{ cursor: "pointer" }}>
                Показать ошибки строк ({result.errors.length})
              </summary>
              <ul className="muted" style={{ fontSize: 13 }}>
                {result.errors.slice(0, 200).map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </details>
          )}

          <div className="row-actions" style={{ marginTop: 16 }}>
            {supplierId != null && (
              <button onClick={() => navigate(`/supplier/${supplierId}`)}>
                Перейти к классификации →
              </button>
            )}
            <button className="secondary" onClick={reset}>
              Загрузить ещё один
            </button>
          </div>
        </div>
      </>
    );
  }

  if (phase === "running") {
    return (
      <>
        <h1>Загрузка прайс-листа</h1>
        {uploadPct < 100 && !job && (
          <div className="card">
            <div className="muted" style={{ fontSize: 13 }}>
              Отправка файла на сервер… {uploadPct}%
            </div>
            <div className="progress">
              <div style={{ width: `${uploadPct}%` }} />
            </div>
          </div>
        )}
        {(uploadPct >= 100 || job) && !job && (
          <div className="card">
            <span className="spinner" /> Файл принят, запуск импорта…
          </div>
        )}
        {job && <JobProgress job={job} />}
        <p className="muted">
          Импорт идёт в фоне (вставка + векторизация товаров). Можно не закрывать
          вкладку — прогресс обновляется автоматически.
        </p>
      </>
    );
  }

  return (
    <>
      <h1>Загрузка прайс-листа</h1>
      <form className="card" onSubmit={submit}>
        <div className="grid2">
          <div className="field">
            <label>
              Название поставщика <span className="req">*</span>
            </label>
            <input
              type="text"
              value={fields.supplier_name}
              onChange={(e) => set("supplier_name", e.target.value)}
              placeholder="ООО «Учебное оборудование»"
            />
          </div>
          <div className="field">
            <label>Короткое название</label>
            <input
              type="text"
              value={fields.supplier_short_name}
              onChange={(e) => set("supplier_short_name", e.target.value)}
            />
          </div>
          <div className="field">
            <label>ИНН</label>
            <input
              type="text"
              value={fields.supplier_inn}
              onChange={(e) => set("supplier_inn", e.target.value)}
              placeholder="по ИНН поставщик не дублируется"
            />
          </div>
          <div className="field">
            <label>Контактное лицо</label>
            <input
              type="text"
              value={fields.supplier_contact_person}
              onChange={(e) => set("supplier_contact_person", e.target.value)}
            />
          </div>
          <div className="field">
            <label>Телефон</label>
            <input
              type="text"
              value={fields.supplier_phone}
              onChange={(e) => set("supplier_phone", e.target.value)}
            />
          </div>
          <div className="field">
            <label>Email</label>
            <input
              type="email"
              value={fields.supplier_email}
              onChange={(e) => set("supplier_email", e.target.value)}
            />
          </div>
        </div>

        <div className="field">
          <label>Файл прайс-листа (CSV) <span className="req">*</span></label>
          <div
            className={
              "dropzone" + (drag ? " drag" : "") + (file ? " has-file" : "")
            }
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              pickFile(e.dataTransfer.files[0] ?? null);
            }}
          >
            {file ? (
              <>
                <b>{file.name}</b>{" "}
                <span className="muted">({Math.round(file.size / 1024)} КБ)</span>
                <div className="muted" style={{ marginTop: 4, fontSize: 12 }}>
                  Нажмите, чтобы выбрать другой файл
                </div>
              </>
            ) : (
              <>
                Перетащите CSV сюда или нажмите для выбора
                <div className="muted" style={{ marginTop: 4, fontSize: 12 }}>
                  Колонки: Артикул; Наименование; Себестоимость; РРЦ (разделитель «;»)
                </div>
              </>
            )}
          </div>
          <input
            ref={inputRef}
            type="file"
            accept=".csv,text/csv"
            style={{ display: "none" }}
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
          />
        </div>

        {error && <div className="error">{error}</div>}

        <div className="row-actions" style={{ marginTop: 12 }}>
          <button type="submit">Загрузить прайс</button>
        </div>
      </form>

      <div className="notice">
        Ожидаемый формат: CSV с разделителем «;», обязательные колонки —{" "}
        <span className="code">Артикул</span>,{" "}
        <span className="code">Наименование</span>,{" "}
        <span className="code">Себестоимость</span>,{" "}
        <span className="code">РРЦ</span>. Необязательные:{" "}
        <span className="code">Описание</span>,{" "}
        <span className="code">Ед. изм.</span>,{" "}
        <span className="code">Производитель</span>,{" "}
        <span className="code">НДС включен</span>.
      </div>
    </>
  );
}
