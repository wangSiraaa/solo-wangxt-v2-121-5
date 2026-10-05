import { Fragment, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type {
  CandidatesPreview, CandidatePreviewItem, CutInput,
} from "../types";
import IssuesPanel from "./IssuesPanel";
import { g } from "../format";

interface Props {
  expId: number;
  defaultBasis: "volume" | "mass";
  defaultLossPct: number;
  appliedIndex: number | null;
  onApply: (p: {
    name: string; basis: "volume" | "mass"; loss_pct: number; cuts: CutInput[];
  }, index: number) => void;
}

type SortKey = "input" | "yield_desc" | "issues_asc" | "issues_desc";

const CSV_TEMPLATE =
  "candidate,cut,start_temp_c,end_temp_c,basis,loss_pct\n" +
  "方案甲,轻馏分,30,185,volume,0\n" +
  "方案甲,重馏分,185,450,,\n" +
  "方案乙,宽馏分,50,650,volume,0\n";

const JSON_TEMPLATE = JSON.stringify(
  [
    {
      name: "方案甲",
      basis: "volume",
      loss_pct: 0,
      cuts: [
        { name: "轻馏分", start_temp_c: 30, end_temp_c: 185 },
        { name: "重馏分", start_temp_c: 185, end_temp_c: 450 },
      ],
    },
    {
      name: "方案乙",
      cuts: [{ name: "宽馏分", start_temp_c: 50, end_temp_c: 650 }],
    },
  ],
  null,
  2
);

function fmt(v: number | null | undefined, d = 2) {
  return v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(d);
}

export default function CandidatePreview({
  expId, defaultBasis, defaultLossPct, appliedIndex, onApply,
}: Props) {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState("");
  const [format, setFormat] = useState<"auto" | "csv" | "json">("auto");
  const [preview, setPreview] = useState<CandidatesPreview | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("input");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const runPreview = async () => {
    if (!content.trim()) {
      setFileError("候选内容为空，请粘贴 CSV/JSON 或选择文件");
      return;
    }
    setLoading(true);
    setFileError(null);
    try {
      const r = await api.previewCandidates(expId, {
        content,
        format,
        basis: defaultBasis,
        loss_pct: Number.isFinite(defaultLossPct) ? defaultLossPct : 0,
      });
      setPreview(r);
      setExpanded(null);
    } catch (e) {
      setPreview(null);
      setFileError(`批量预览被拒绝：${(e as Error).message}（未创建任何方案）`);
    } finally {
      setLoading(false);
    }
  };

  const onPickFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      setContent(String(reader.result ?? ""));
      setFileError(null);
      const name = file.name.toLowerCase();
      setFormat(name.endsWith(".json") ? "json" : name.endsWith(".csv") ? "csv" : "auto");
    };
    reader.onerror = () => setFileError(`读取文件失败：${file.name}`);
    reader.readAsText(file, "utf-8");
  };

  const rows = useMemo(() => {
    if (!preview) return [];
    const valid = preview.candidates.filter((c) => c.valid);
    const invalid = preview.candidates.filter((c) => !c.valid);
    if (sortKey === "yield_desc") {
      valid.sort((a, b) =>
        (b.summary?.volume_yield_pct ?? -1) - (a.summary?.volume_yield_pct ?? -1));
    } else if (sortKey === "issues_asc") {
      valid.sort((a, b) => (a.summary?.issue_count ?? 0) - (b.summary?.issue_count ?? 0));
    } else if (sortKey === "issues_desc") {
      valid.sort((a, b) => (b.summary?.issue_count ?? 0) - (a.summary?.issue_count ?? 0));
    }
    // 无效项不参与产率排序，统一沉底（保持各自原始顺序）
    return sortKey === "input" ? preview.candidates : [...valid, ...invalid];
  }, [preview, sortKey]);

  const apply = (c: CandidatePreviewItem) => {
    if (!c.valid || !c.result) return;
    const cuts: CutInput[] = c.result.cuts.map((x) => ({
      name: x.name,
      start_temp_c: x.start_temp_c,
      end_temp_c: x.end_temp_c,
    }));
    onApply({ name: c.name ?? "未命名方案", basis: c.basis, loss_pct: c.loss_pct, cuts }, c.index);
  };

  return (
    <div className="candidate-box">
      <button className="btn" onClick={() => setOpen((v) => !v)}>
        {open ? "收起候选批量预览" : "📋 候选切点批量预览（CSV/JSON，不落库）"}
      </button>

      {open && (
        <div className="candidate-panel">
          <div className="cand-controls">
            <label>格式
              <select value={format} onChange={(e) => setFormat(e.target.value as typeof format)}>
                <option value="auto">自动识别</option>
                <option value="csv">CSV</option>
                <option value="json">JSON</option>
              </select>
            </label>
            <button className="btn" onClick={() => { setContent(CSV_TEMPLATE); setFormat("csv"); setFileError(null); }}>
              填入 CSV 示例
            </button>
            <button className="btn" onClick={() => { setContent(JSON_TEMPLATE); setFormat("json"); setFileError(null); }}>
              填入 JSON 示例
            </button>
            <button className="btn" onClick={() => fileRef.current?.click()}>选择文件…</button>
            <input
              ref={fileRef} type="file" accept=".csv,.json,.txt" hidden
              onChange={(e) => { const f = e.target.files?.[0]; if (f) onPickFile(f); e.target.value = ""; }}
            />
            <span className="meta">
              未在文本中指定口径/损失时，取当前编辑器：
              {defaultBasis === "volume" ? "体积" : "质量"} · 损失 {g(defaultLossPct)}%
            </span>
          </div>

          <textarea
            className="cand-text"
            rows={7}
            placeholder={
              "CSV 首行：candidate,cut,start_temp_c,end_temp_c[,basis,loss_pct]\n" +
              "或 JSON：[{\"name\": \"方案甲\", \"cuts\": [{\"name\": \"轻\", \"start_temp_c\": 30, \"end_temp_c\": 185}]}]"
            }
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />

          <div className="cand-actions">
            <button className="btn primary" onClick={runPreview} disabled={loading}>
              {loading ? "计算中…" : "批量预览（复用当前试验的同一套评估规则）"}
            </button>
            {preview && (
              <span className="meta">
                共 {preview.total} 项：有效 {preview.valid_count} · 单项无效 {preview.invalid_count}
              </span>
            )}
          </div>

          {fileError && <div className="banner error cand-banner">{fileError}</div>}

          {preview && preview.total > 0 && (
            <>
              <div className="cand-sort">
                排序：
                <label><input type="radio" name="candsort" checked={sortKey === "input"}
                  onChange={() => setSortKey("input")} /> 输入顺序</label>
                <label><input type="radio" name="candsort" checked={sortKey === "yield_desc"}
                  onChange={() => setSortKey("yield_desc")} /> 产率高→低</label>
                <label><input type="radio" name="candsort" checked={sortKey === "issues_asc"}
                  onChange={() => setSortKey("issues_asc")} /> 问题少→多</label>
                <label><input type="radio" name="candsort" checked={sortKey === "issues_desc"}
                  onChange={() => setSortKey("issues_desc")} /> 问题多→少</label>
                <span className="meta">（无效项始终沉底）</span>
              </div>

              <table className="cand-table">
                <thead>
                  <tr>
                    <th>#</th><th>候选名称</th><th>口径</th><th>馏分</th>
                    <th>并集产率 体积/质量 %</th>
                    <th>重叠 处(%)</th><th>缺口 处(%)</th>
                    <th>残余 %</th><th>问题 错/警/示</th><th>平衡</th><th>状态 / 操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((c) => {
                    const s = c.summary;
                    const isOpen = expanded === c.index;
                    const applied = appliedIndex === c.index;
                    return (
                      <Fragment key={c.index}>
                        <tr
                          className={!c.valid ? "invalid" : s?.has_blocking_errors ? "blocked" : applied ? "applied" : ""}>
                          <td>{c.index + 1}</td>
                          <td className="cand-name">
                            <button className="link-btn" onClick={() => setExpanded(isOpen ? null : c.index)}>
                              {isOpen ? "▾" : "▸"} {c.name ?? "（无名称）"}
                            </button>
                          </td>
                          <td>{c.basis === "mass" ? "质量" : "体积"}{c.loss_pct ? ` · 损${g(c.loss_pct)}%` : ""}</td>
                          <td>{c.cut_count}</td>
                          <td className="num">
                            {c.valid
                              ? `${fmt(s?.volume_yield_pct)} / ${fmt(s?.mass_yield_pct)}`
                              : "—"}
                          </td>
                          <td className="num">{c.valid ? `${s!.overlap_count} (${fmt(s!.overlap_pct)})` : "—"}</td>
                          <td className="num">{c.valid ? `${s!.gap_count} (${fmt(s!.gap_pct)})` : "—"}</td>
                          <td className="num">{c.valid ? fmt(s!.residue_pct) : "—"}</td>
                          <td className="num">
                            {c.valid
                              ? `${s!.error_count}/${s!.warning_count}/${s!.info_count}`
                              : "—"}
                          </td>
                          <td>{c.valid ? (s!.identity_ok ? "✅" : "❌") : "—"}</td>
                          <td>
                            {!c.valid ? (
                              <span className="tag-invalid">单项无效</span>
                            ) : (
                              <>
                                {s!.has_blocking_errors && <span className="tag-blocked">含硬错误</span>}
                                <button className="btn primary small" onClick={() => apply(c)}>
                                  {applied ? "重新带入" : "带入编辑"}
                                </button>
                              </>
                            )}
                          </td>
                        </tr>
                        {isOpen && (
                          <tr className="detail-row">
                            <td colSpan={11}>
                              {!c.valid ? (
                                <div className="cand-errors">
                                  <b>该候选无法评估（仅本项标记，不影响其他候选）：</b>
                                  <ul>{c.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
                                </div>
                              ) : c.result && (
                                <>
                                  <div className="cand-cutlist">
                                    {c.result.cuts.map((x) => (
                                      <span key={x.index} className="cut-chip">
                                        {x.name}：{g(x.start_temp_c)}~{g(x.end_temp_c)} ℃
                                        {x.flags.length > 0 &&
                                          <em className="flags-inline">（{x.flags.join("、")}）</em>}
                                      </span>
                                    ))}
                                  </div>
                                  <IssuesPanel issues={c.result.issues} />
                                </>
                              )}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
              <p className="hint">
                预览为只读计算：不保存方案、不修改原始试验数据。选择「带入编辑」后仍可在编辑器调整，
                再走现有保存流程；保存结果与单独评估完全一致。
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
