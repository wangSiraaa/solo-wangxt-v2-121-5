import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, exportUrl } from "./api";
import type {
  CurveSample, EvalResult, Experiment, PlanInput,
} from "./types";
import CurveChart from "./components/CurveChart";
import CutEditor from "./components/CutEditor";
import ResultsPanel from "./components/ResultsPanel";
import IssuesPanel from "./components/IssuesPanel";

function defaultCuts(sample: CurveSample) {
  const [lo, hi] = sample.range.temp_c;
  const q1 = lo + (hi - lo) / 3;
  const q2 = lo + 2 * (hi - lo) / 3;
  return [
    { name: "轻馏分", start_temp_c: lo, end_temp_c: q1 },
    { name: "中间馏分", start_temp_c: q1, end_temp_c: q2 },
    { name: "重馏分", start_temp_c: q2, end_temp_c: hi },
  ];
}

export default function App() {
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [expId, setExpId] = useState<number | null>(null);
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [sample, setSample] = useState<CurveSample | null>(null);

  const [planName, setPlanName] = useState("未命名方案");
  const [basis, setBasis] = useState<"volume" | "mass">("volume");
  const [lossPct, setLossPct] = useState(0);
  const [cuts, setCuts] = useState<PlanInput["cuts"]>([]);

  const [result, setResult] = useState<EvalResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<number | null>(null);
  const [seedMsg, setSeedMsg] = useState<string | null>(null);

  useEffect(() => {
    api.listExperiments()
      .then((list) => {
        setExperiments(list);
        if (list.length) setExpId(list[0].id);
      })
      .catch((e) => setError(`读取试验列表失败：${e.message}`));
  }, []);

  useEffect(() => {
    if (expId === null) return;
    setError(null);
    setResult(null);
    Promise.all([api.getExperiment(expId), api.curveSample(expId)])
      .then(([exp, s]) => {
        setExperiment(exp);
        setSample(s);
        if (exp.plans && exp.plans.length) {
          const p = exp.plans[0];
          setPlanName(p.name);
          setBasis(p.basis);
          setLossPct(p.loss_pct);
          setCuts(p.cuts);
        } else {
          setPlanName(`${exp.name.split("｜")[0]}-方案`);
          setBasis("volume");
          setLossPct(0);
          setCuts(defaultCuts(s));
        }
        setSavedId(exp.plans && exp.plans.length ? exp.plans[0].id : null);
      })
      .catch((e) => setError(`加载试验失败：${e.message}`));
  }, [expId]);

  const payload: PlanInput = useMemo(
    () => ({ name: planName, basis, loss_pct: Number.isFinite(lossPct) ? lossPct : 0, cuts }),
    [planName, basis, lossPct, cuts]
  );

  const timer = useRef<number | null>(null);
  const evaluate = useCallback(() => {
    if (expId === null) return;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      setLoading(true);
      try {
        const res = await api.evaluate(expId, payload);
        setResult(res);
        setSavedId(null);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    }, 250);
  }, [expId, payload]);

  useEffect(() => {
    evaluate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload]);

  const save = async () => {
    if (expId === null) return;
    setLoading(true);
    try {
      const r = await api.savePlan(expId, payload);
      setResult(r.result);
      setSavedId(r.id);
      setError(null);
    } catch (e) {
      setError(`保存失败：${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const loadSeed = async () => {
    try {
      const r = await api.seed();
      setSeedMsg(`已导入 ${r.loaded_experiments} 条种子试验`);
      const list = await api.listExperiments();
      setExperiments(list);
      if (!expId && list.length) setExpId(list[0].id);
    } catch (e) {
      setSeedMsg(`导入失败（库非空时需清空）：${(e as Error).message}`);
    }
  };

  return (
    <div className="app">
      <header>
        <h1>蒸馏曲线切点选择与产率核对 <span className="sub">培训系统 · 仅离线试验数据 · 不控制真实装置</span></h1>
        <div className="header-actions">
          {experiments.length === 0 && (
            <button className="btn" onClick={loadSeed}>载入内置算例（3 组）</button>
          )}
          {seedMsg && <span className="seed-msg">{seedMsg}</span>}
        </div>
      </header>

      {error && <div className="banner error">{error} <button onClick={() => setError(null)}>×</button></div>}

      <div className="toolbar">
        <label>离线试验：
          <select value={expId ?? ""} onChange={(e) => setExpId(parseInt(e.target.value))}>
            {experiments.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
          </select>
        </label>
        {experiment && (
          <span className="meta">
            进料密度 {experiment.feed_density_g_cm3.toFixed(3)} g/cm³
            {experiment.residue_density_g_cm3
              ? ` · 残渣密度 ${experiment.residue_density_g_cm3.toFixed(3)} g/cm³`
              : " · 未提供残渣密度（质量基准残渣项留空）"}
            {" · "}密度表 {experiment.density_rows.length} 行
          </span>
        )}
        {loading && <span className="loading">计算中…</span>}
      </div>

      {experiment?.conditions && Object.keys(experiment.conditions).length > 0 && (
        <div className="conditions">
          试验条件：
          {Object.entries(experiment.conditions).map(([k, v]) => (
            <span key={k} className="cond-item">{k}={String(v)}</span>
          ))}
        </div>
      )}

      {sample && result && (
        <>
          <CurveChart sample={sample} cuts={cuts} overlaps={result.overlaps} />
          <IssuesPanel issues={result.issues} />

          <CutEditor
            cuts={cuts}
            lossPct={lossPct}
            basis={basis}
            planName={planName}
            range={sample.range.temp_c}
            onChange={setCuts}
            onLossChange={setLossPct}
            onBasisChange={setBasis}
            onPlanNameChange={setPlanName}
          />

          <ResultsPanel result={result} basis={basis} />

          <div className="save-bar">
            <button className="btn primary" onClick={save} disabled={result.has_blocking_errors}>
              保存方案
            </button>
            {result.has_blocking_errors && (
              <span className="save-note">存在曲线硬错误，请先核实修正试验数据再保存。</span>
            )}
            {savedId && (
              <span className="exports">
                已保存（方案 #{savedId}）：
                <a href={exportUrl(savedId, "markdown")} target="_blank" rel="noreferrer">导出 Markdown</a>
                <span className="sep">|</span>
                <a href={exportUrl(savedId, "json")} target="_blank" rel="noreferrer">导出 JSON</a>
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
