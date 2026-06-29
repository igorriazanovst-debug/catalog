import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { uploadEstimate } from "../api";

const XLSX_EXT = [".xlsx", ".xlsm", ".xltx", ".xltm"];

export default function EstimateUploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pct, setPct] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

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
    setBusy(true);
    setPct(0);
    try {
      const res = await uploadEstimate(file, name.trim() || undefined, setPct);
      navigate(`/estimates/${res.estimate_id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
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

        {busy && pct < 100 && (
          <div className="progress">
            <div style={{ width: `${pct}%` }} />
          </div>
        )}
        {error && <div className="error">{error}</div>}

        <div className="row-actions" style={{ marginTop: 12 }}>
          <button type="submit" disabled={busy}>
            {busy ? "Разбор…" : "Загрузить и распознать"}
          </button>
        </div>
      </form>

      <div className="notice">
        При загрузке смета только <b>разбирается</b> — система найдёт шапку,
        наименования, коды, количество и характеристики. Подбор товаров запускается
        отдельно на странице сметы: целиком (авто) или построчно (без LLM / с LLM).
      </div>
    </>
  );
}
