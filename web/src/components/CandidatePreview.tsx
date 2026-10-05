import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type {
  CandidatePreviewItem,
  CandidatePreviewResponse,
  PlanInput,
} from "../types";
import { cutFlagHint } from "./CutEditor";
import { g } from "../format";

interface Props {
  expId: number;
  /** 采用某个候选：把其 name/basis/loss_pct/cuts 带入现有方案编辑与保存流程。 */
  onAdopt: (plan: PlanInput) => void;
}

type SortKey = "none" | "yield" | "issues";

const SAMPLE_CSV = `候选,馏分,初馏点,终馏点,损失
方案甲,轻馏分,55,150,1.0
方案甲,中馏分,150,250,1.0
方案甲,重馏分,250,340,1.0
方案乙,轻馏分,55,185,0.5
方案乙,重馏分,185,370,0.5`;

function fmt(v: number | null | undefined, d = 2) {
  return v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(d);
}

/** 候选方案批量预览：粘贴/上传 CSV 或 JSON，逐项试算比较，可选一项带入编辑器。 */
export default function CandidatePreview({ expId, onAdopt }: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resp, setResp] = useState<CandidatePreviewResponse | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("none");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [adopted, setAdopted] = useState<number | null>(null);

  const format: "csv" | "json" = useMemo(() => {
    const t = text.trimStart();
    return t.startsWith("{") || t.startsWith("[") ? "json" : "csv";
  }, [text]);

  // 预览结果只对当前试验有效：切换试验后清空，避免误带到别的数据上
  useEffect(() => {
    setResp(null);
    setError(null);
    setAdopted(null);
    setExpanded(new Set());
  }, [expId]);

  const sorted = useMemo(() => {
    if (!resp) return [];
    const items = [...resp.candidates];
    const rank = (c: CandidatePreviewItem) => (c.valid && c.summary ? 0 : 1);
    items.sort((a, b) => {
      const ra = rank(a), rb = rank(b);
      if (ra !== rb) return ra - rb; // 无效候选永远沉底
      if (ra === 1) return a.index - b.index;
      const sa = a.summary!, sb = b.summary!;
      if (sortKey === "yield") return sb.union_yield_pct - sa.union_yield_pct;
      if (sortKey === "issues") {
        if (sa.n_errors !== sb.n_errors) return sa.n_errors - sb.n_errors;
        if (sa.n_issues !== sb.n_issues) return sa.n_issues - sb.n_issues;
        return sb.union_yield_pct - sa.union_yield_pct;
      }
      return a.index - b.index;
    });
    return items;
  }, [resp, sortKey]);

  const runPreview = async () => {
    if (!text.trim()) {
      setError("请先粘贴候选内容或选择文件（CSV 或 JSON）");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await api.previewCandidates(expId, text, format);
      setResp(r);
      setSortKey("none");
      setExpanded(new Set());
      setAdopted(null);
    } catch (e) {
      setResp(null);
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const loadFile = async (f: File | undefined) => {
    if (!f) return;
    setText(await f.text());
    setError(null);
  };

  const toggle = (i: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });

  const adopt = (c: CandidatePreviewItem) => {
    if (!c.plan) return;
    onAdopt(c.plan);
    setAdopted(c.index);
  };

  return (
    <div className="candidates">
      <div className="candidates-head">
        <button className="btn" onClick={() => setOpen(!open)}>
          {open ? "▾ 收起候选批量预览" : "▸ 候选方案批量预览（CSV / JSON 导入）"}
        </button>
        <span className="hint-inline">
          多套候选切点先在当前试验上试算比较，再选一套带入下方编辑器保存；导入不修改试验数据。
        </span>
      </div>

      {open && (
        <div className="candidates-body">
          <div className="row">
            <label className="file-label">
              选择文件
              <input
                type="file"
                accept=".csv,.json,text/csv,application/json"
                onChange={(e) => loadFile(e.target.files?.[0])}
              />
            </label>
            <button className="btn ghost-blue" onClick={() => setText(SAMPLE_CSV)}>
              填入 CSV 示例
            </button>
            <span className="hint-inline">
              识别为：<b>{format === "csv" ? "CSV" : "JSON"}</b>
              （CSV 表头：候选,馏分,初馏点,终馏点[,损失]；JSON：候选数组或 {"{\"candidates\":[...]}"}）
            </span>
          </div>
          <textarea
            rows={6}
            placeholder={"粘贴 CSV（首行表头）或 JSON 候选数组……\n每项候选 = 名称 + 切点列表，可带 loss_pct / basis"}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="row">
            <button className="btn primary" onClick={runPreview} disabled={busy}>
              {busy ? "试算中…" : "批量预览（仅试算，不落库）"}
            </button>
            {resp && (
              <button
                className="btn"
                onClick={() => { setResp(null); setAdopted(null); }}
              >
                清除结果
              </button>
            )}
            {resp && (
              <span className="hint-inline">
                共 {resp.count} 项候选，{resp.valid_count} 项有效
                {resp.valid_count < resp.count && `，${resp.count - resp.valid_count} 项无效（不影响其他候选）`}
              </span>
            )}
          </div>

          {error && <div className="banner error">{error} <button onClick={() => setError(null)}>×</button></div>}

          {resp && resp.row_errors.length > 0 && (
            <div className="issues">
              {resp.row_errors.map((m, i) => (
                <div key={i} className="issue warning"><span>⚠️</span><span className="tag">行级提示</span><span>{m}</span></div>
              ))}
            </div>
          )}

          {resp && (
            <>
              <div className="row sort-row">
                排序：
                <button
                  className={`btn ${sortKey === "yield" ? "primary" : ""}`}
                  onClick={() => setSortKey("yield")}
                >
                  按产率（并集 % 从高到低）
                </button>
                <button
                  className={`btn ${sortKey === "issues" ? "primary" : ""}`}
                  onClick={() => setSortKey("issues")}
                >
                  按问题数（从少到多）
                </button>
                <button
                  className={`btn ${sortKey === "none" ? "primary" : ""}`}
                  onClick={() => setSortKey("none")}
                >
                  原始顺序
                </button>
              </div>

              <table className="candidate-table">
                <thead>
                  <tr>
                    <th>#</th><th>候选名称</th><th>状态</th>
                    <th>并集产率 %</th><th>重叠 %</th><th>缺口 %</th><th>平衡</th>
                    <th>问题</th><th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((c) => (
                    <CandidateRow
                      key={c.index}
                      c={c}
                      expanded={expanded.has(c.index)}
                      adopted={adopted === c.index}
                      onToggle={() => toggle(c.index)}
                      onAdopt={() => adopt(c)}
                    />
                  ))}
                </tbody>
              </table>
              <p className="hint">
                预览与下方“实时计算”使用同一套评估规则；点击“采用”后候选切点带入编辑器，
                可继续调整，确认后再“保存方案”。
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function CandidateRow({
  c, expanded, adopted, onToggle, onAdopt,
}: {
  c: CandidatePreviewItem;
  expanded: boolean;
  adopted: boolean;
  onToggle: () => void;
  onAdopt: () => void;
}) {
  const s = c.summary;
  return (
    <>
      <tr className={c.valid ? "" : "invalid"}>
        <td>{c.index + 1}</td>
        <td>{c.name}</td>
        <td>
          {c.valid ? (
            s!.n_errors > 0 ? (
              <span className="pill zero_width">有错误</span>
            ) : s!.n_warnings > 0 ? (
              <span className="pill out_of_range">有警告</span>
            ) : (
              <span className="pill ok">有效</span>
            )
          ) : (
            <span className="pill inverted">无效</span>
          )}
        </td>
        <td>{c.valid ? fmt(s!.union_yield_pct) : "—"}</td>
        <td className={c.valid && s!.overlap_pct > 0 ? "bad-num" : ""}>
          {c.valid ? fmt(s!.overlap_pct) : "—"}
        </td>
        <td>{c.valid ? fmt(s!.gap_total_pct) : "—"}</td>
        <td>{c.valid ? (s!.identity_ok ? "✅ 闭合" : "❌ 不闭合") : "—"}</td>
        <td>
          {c.valid
            ? [s!.n_errors > 0 && `⛔${s!.n_errors}`,
               s!.n_warnings > 0 && `⚠️${s!.n_warnings}`,
               s!.n_infos > 0 && `ℹ️${s!.n_infos}`]
                .filter(Boolean).join(" ") || "无"
            : "—"}
        </td>
        <td className="ops">
          <button className="btn ghost-blue" onClick={onToggle}>
            {expanded ? "收起" : "说明"}
          </button>
          {c.valid && (
            <button className="btn primary" onClick={onAdopt} disabled={adopted}>
              {adopted ? "已带入编辑器" : "采用此候选"}
            </button>
          )}
        </td>
      </tr>
      {expanded && (
        <tr className="detail">
          <td colSpan={9}>
            {!c.valid && (
              <div className="issues">
                <div className="issue error">
                  <span>⛔</span><span className="tag">无效</span>
                  <span>{c.error}</span>
                </div>
              </div>
            )}
            {c.valid && c.result && (
              <div className="candidate-detail">
                <table className="yield-table">
                  <thead>
                    <tr><th>馏分</th><th>区间 ℃</th><th>体积产率 %</th><th>质量产率 %</th><th>标记</th></tr>
                  </thead>
                  <tbody>
                    {c.result.cuts.map((cut) => (
                      <tr key={cut.index}>
                        <td>{cut.name}</td>
                        <td>{g(cut.start_temp_c)} ~ {g(cut.end_temp_c)}</td>
                        <td>{fmt(cut.volume_yield_pct)}</td>
                        <td>{fmt(cut.mass_yield_pct)}</td>
                        <td className="flags">
                          {cut.flags.map((f) => (
                            <span key={f} className={`pill ${f}`}>{cutFlagHint(f)}</span>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {c.result.issues.length > 0 && (
                  <div className="issues">
                    {c.result.issues.map((it, i) => (
                      <div key={i} className={`issue ${it.severity}`}>
                        <span>{it.severity === "error" ? "⛔" : it.severity === "warning" ? "⚠️" : "ℹ️"}</span>
                        <span>{it.message}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
