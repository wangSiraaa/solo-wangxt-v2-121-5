import type { Issue } from "../types";

const STYLE: Record<Issue["severity"], { icon: string; cls: string; label: string }> = {
  error: { icon: "⛔", cls: "issue error", label: "错误" },
  warning: { icon: "⚠️", cls: "issue warning", label: "需核实" },
  info: { icon: "ℹ️", cls: "issue info", label: "提示" },
};

export default function IssuesPanel({ issues }: { issues: Issue[] }) {
  if (issues.length === 0) {
    return (
      <div className="issues">
        <div className="issue ok">✅ 未发现数据与切点问题（结果仍仅在实测温度范围内成立）</div>
      </div>
    );
  }
  // 错误优先排序
  const order = { error: 0, warning: 1, info: 2 };
  const sorted = [...issues].sort((a, b) => order[a.severity] - order[b.severity]);
  return (
    <div className="issues">
      {sorted.map((it, i) => (
        <div key={i} className={STYLE[it.severity].cls}>
          <span>{STYLE[it.severity].icon}</span>
          <span className="tag">{STYLE[it.severity].label}</span>
          <span>{it.message}</span>
        </div>
      ))}
    </div>
  );
}
