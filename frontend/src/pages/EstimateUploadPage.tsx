import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  listProviders,
  pollJob,
  uploadEstimate,
  type EstimateJobResult,
  type Job,
  type Provider,
} from "../api";
import JobProgress from "../components/JobProgress";

const XLSX_EXT = [".xlsx", ".xlsm", ".xltx", ".xltm"];
type Phase = "form" | "running" | "failed";

export default function EstimateUploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [drag, setDrag] = useState(false);
  const [providers, setProviders] = useState<Provider[]>([]);
  // "" = без LLM; иначе id провайдера
  const [provider, setProvider] = useState<string>("");
  const [decompose, setDecompose] = useState(true);
  const [priceBasis, setPriceBasis] = useState<"cost" | "retail">("cost");
  const [phase, setPhase] = useState<Phase>("form");
  const [uploadPct, setUploadPct] = useState(0);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    listProviders()
      .then((ps) => {
        setProviders(ps);
        const def = ps.find((p) => p.configured && p.default) || ps.find((p) => p.configured);
        if (def) setProvider(def.id);
      })
      .catch(() => {});
  }, []);

  const pickFile = (f: File | null) => {
    setError(null);
    if (f && !XLSX_EXT.some((x) => f.name.toLowerCase().endsWith(x))) {
      setError("Нужен файл сметы в формате xlsx.");
      return;
    }
    setFile(f);
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!file) {
      setError("Прикрепите файл сметы (xlsx).");
      return;
    }
    setPhase("running");
    setUploadPct(0);
    setJob(null);
    const useLlm = provider !== "";
    try {
      const started = await uploadEstimate(
        file,
        {
          name: name.trim() || undefined,
          use_llm: useLlm,
          provider: useLlm ? provider : undefined,
          decompose: useLlm && decompose,
          price_basis: priceBasis,
        },
        setUploadPct
      );
      const finished = await pollJob(started.job_id, setJob);
      if (finished.status === "done") {
        const res = finished.result as EstimateJobResult;
        navigate(`/estimates/${res.estimate_id}`);
      } else {
        setError(finished.error || "Подбор завершился с ошибкой.");
        setPhase("failed");
      }
    } catch (err) {
      setError((err as Error).message);
      setPhase("failed");
    }
  }

  if (phase === "running") {
    return (
      <>
        <h1>Обработка сметы</h1>
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
            <span className="spinner" /> Файл принят, идёт разбор и подбор…
          </div>
        )}
        {job && <JobProgress job={job} />}
        <p className="muted">
          Разбор сметы, сопоставление с 838 и подбор товаров идут в фоне
          {provider !== "" ? " (с LLM — может занять время)" : ""}. Прогресс по
          позициям обновляется автоматически.
        </p>
      </>
    );
  }

  return (
    <>
      <h1>Загрузка сметы</h1>
      <form className="card" onSubmit={submit}>
        <div className="field">
          <label>Название сметы</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="по умолчанию — имя файла"
          />
        </div>

        <div className="grid2">
          <div className="field">
            <label>Подбор стандарта 838</label>
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="">Без LLM (только текстовый подбор)</option>
              {providers.map((p) => (
                <option key={p.id} value={p.id} disabled={!p.configured}>
                  С LLM: {p.label}
                  {p.configured ? "" : " (не настроен)"}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>Цена для подбора и итога</label>
            <select
              value={priceBasis}
              onChange={(e) => setPriceBasis(e.target.value as "cost" | "retail")}
            >
              <option value="cost">Себестоимость</option>
              <option value="retail">РРЦ</option>
            </select>
          </div>
        </div>

        <div className="field">
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontWeight: 400 }}>
            <input
              type="checkbox"
              checked={decompose}
              disabled={provider === ""}
              onChange={(e) => setDecompose(e.target.checked)}
            />
            Разлагать строки-наборы на вложения (требует LLM)
          </label>
        </div>

        <div className="field">
          <label>
            Файл сметы (xlsx) <span className="req">*</span>
          </label>
          <div
            className={"dropzone" + (drag ? " drag" : "") + (file ? " has-file" : "")}
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
                Перетащите xlsx сюда или нажмите для выбора
                <div className="muted" style={{ marginTop: 4, fontSize: 12 }}>
                  Поддерживаются разные шаблоны 44-ФЗ «Описание объекта закупки»
                </div>
              </>
            )}
          </div>
          <input
            ref={inputRef}
            type="file"
            accept=".xlsx,.xlsm"
            style={{ display: "none" }}
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
          />
        </div>

        {error && <div className="error">{error}</div>}

        <div className="row-actions" style={{ marginTop: 12 }}>
          <button type="submit">Загрузить и подобрать</button>
        </div>
      </form>

      <div className="notice">
        Колонки сметы не фиксированы — система сама находит шапку, наименования,
        коды КТРУ/ОКПД2, количество и характеристики. Подбор: позиция → Приказ 838
        (текст {provider !== "" ? "+ LLM-судья" : ""}) → товар поставщика (сначала
        качество маппинга, потом дешевле).
      </div>
    </>
  );
}
