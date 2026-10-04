import type {
  CurveSample,
  EvalResult,
  Experiment,
  PlanInput,
} from "./types";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const body = await r.json();
      detail = typeof body.detail === "string"
        ? body.detail
        : body.detail?.message ?? JSON.stringify(body.detail ?? body);
    } catch {
      /* ignore */
    }
    throw new Error(`${r.status} ${detail}`);
  }
  return r.json() as Promise<T>;
}

export const api = {
  listExperiments: () => req<Experiment[]>("/api/experiments"),
  getExperiment: (id: number) => req<Experiment>(`/api/experiments/${id}`),
  curveSample: (id: number) =>
    req<CurveSample>(`/api/experiments/${id}/curve/sample?n=240`),
  evaluate: (id: number, plan: PlanInput) =>
    req<EvalResult>(`/api/experiments/${id}/evaluate`, {
      method: "POST",
      body: JSON.stringify(plan),
    }),
  savePlan: (id: number, plan: PlanInput) =>
    req<{ id: number; result: EvalResult }>(
      `/api/experiments/${id}/plans`,
      { method: "POST", body: JSON.stringify(plan) }
    ),
  seed: () =>
    req<{ loaded_experiments: number }>("/api/seed", { method: "POST" }),
};

export function exportUrl(planId: number, format: "markdown" | "json") {
  return `/api/plans/${planId}/export?format=${format}`;
}
