// Тонкий клиент над backend API. Все пути относительные — фронт и бэк на одном
// origin (в dev /api проксируется Vite на uvicorn, см. vite.config.ts).

// Запуск фоновой задачи импорта возвращает идентификатор задачи.
export interface UploadStarted {
  job_id: string;
  supplier_id: number;
  supplier_name: string;
}

// Итог импорта (job.result для kind="import").
export interface ImportResult {
  imported: number;
  updated: number;
  auto_sku: number;
  errors: string[];
}

// Итог классификации (job.result для kind="classify").
export interface ClassifyResult {
  total_products: number;
  auto_mapped: number;
  needs_review: number;
  no_match: number;
  by_rule: number;
  by_llm: number;
  llm_errors: number;
  errors: string[];
}

export interface Provider {
  id: string;
  label: string;
  configured: boolean;
  default: boolean;
}

export type JobStatus = "running" | "done" | "error";

export interface Job {
  id: string;
  kind: "import" | "classify";
  status: JobStatus;
  total: number;
  processed: number;
  counters: Record<string, number>;
  message: string;
  error: string | null;
  result: ImportResult | ClassifyResult | null;
  elapsed: number;
}

export interface Supplier {
  id: number;
  name: string;
  short_name: string | null;
  inn: string | null;
  created_at: string | null;
  products_total: number;
  mapped: number;
  auto: number;
  manual: number;
  unmapped: number;
}

export type MappingStatus = "auto" | "manual" | "rejected" | "unmapped";

export interface Product {
  id: number;
  name: string;
  sku: string | null;
  description: string | null;
  manufacturer: string | null;
  unit: string | null;
  cost_price: number | null;
  retail_price: number | null;
  mapping_id: number | null;
  standard_id: number | null;
  status: MappingStatus;
  match_score: number | null;
  match_reason: string | null;
  standard_name: string | null;
  full_code: string | null;
  subsection_name: string | null;
}

export interface Candidate {
  standard_id: number;
  standard_name: string;
  full_code: string | null;
  subsection_name: string | null;
  sources: string[];
  vector_similarity: number | null;
  keyword_score: number | null;
}

async function jget<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}

async function jpost<T>(url: string): Promise<T> {
  const r = await fetch(url, { method: "POST" });
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}

export interface SupplierFields {
  supplier_name: string;
  supplier_short_name?: string;
  supplier_inn?: string;
  supplier_contact_person?: string;
  supplier_phone?: string;
  supplier_email?: string;
}

export async function uploadPriceList(
  file: File,
  supplier: SupplierFields,
  onProgress?: (pct: number) => void
): Promise<UploadStarted> {
  const form = new FormData();
  form.append("file", file);
  for (const [k, v] of Object.entries(supplier)) {
    if (v != null && v !== "") form.append(k, v);
  }
  // XHR ради прогресса загрузки (fetch не отдаёт upload progress).
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/products/upload");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        let detail = xhr.responseText;
        try {
          detail = JSON.parse(xhr.responseText).detail || detail;
        } catch {
          /* оставляем как есть */
        }
        reject(new Error(detail));
      }
    };
    xhr.onerror = () => reject(new Error("Сетевая ошибка при загрузке"));
    xhr.send(form);
  });
}

export const listSuppliers = () =>
  jget<{ items: Supplier[] }>("/api/products/suppliers").then((d) => d.items);

export function listProducts(params: {
  supplier_id?: number;
  status?: MappingStatus;
  limit?: number;
  offset?: number;
}): Promise<Product[]> {
  const q = new URLSearchParams();
  if (params.supplier_id != null) q.set("supplier_id", String(params.supplier_id));
  if (params.status) q.set("status", params.status);
  q.set("limit", String(params.limit ?? 500));
  q.set("offset", String(params.offset ?? 0));
  return jget<{ items: Product[] }>(`/api/products?${q}`).then((d) => d.items);
}

export const listProviders = () =>
  jget<{ providers: Provider[] }>("/api/mapping/providers").then((d) => d.providers);

export function autoMap(params: {
  supplier_id?: number;
  only_unmapped?: boolean;
  confidence_threshold?: number;
  provider?: string;
}): Promise<{ job_id: string }> {
  const q = new URLSearchParams();
  if (params.supplier_id != null) q.set("supplier_id", String(params.supplier_id));
  if (params.only_unmapped) q.set("only_unmapped", "true");
  if (params.confidence_threshold != null)
    q.set("confidence_threshold", String(params.confidence_threshold));
  if (params.provider) q.set("provider", params.provider);
  return jpost<{ job_id: string }>(`/api/mapping/auto-map?${q}`);
}

export const getJob = (jobId: string) => jget<Job>(`/api/jobs/${jobId}`);

// Опрашивает задачу, вызывая onUpdate на каждом тике, пока статус != running.
export function pollJob(
  jobId: string,
  onUpdate: (job: Job) => void,
  intervalMs = 1200
): Promise<Job> {
  return new Promise((resolve, reject) => {
    const tick = async () => {
      let job: Job;
      try {
        job = await getJob(jobId);
      } catch (e) {
        reject(e);
        return;
      }
      onUpdate(job);
      if (job.status === "running") {
        setTimeout(tick, intervalMs);
      } else {
        resolve(job);
      }
    };
    tick();
  });
}

export const productCandidates = (productId: number) =>
  jget<{ candidates: Candidate[] }>(
    `/api/review/product/${productId}/candidates`
  ).then((d) => d.candidates);

export const approveMapping = (mappingId: number) =>
  jpost(`/api/review/mapping/${mappingId}/approve`);

export const reassignMapping = (mappingId: number, standardId: number) =>
  jpost(`/api/review/mapping/${mappingId}/reassign?standard_id=${standardId}`);

export const rejectMapping = (mappingId: number) =>
  jpost(`/api/review/mapping/${mappingId}/reject`);
