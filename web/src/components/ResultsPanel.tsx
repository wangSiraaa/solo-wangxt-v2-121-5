import type { EvalResult } from "../types";
import { cutFlagHint } from "./CutEditor";
import { g } from "../format";

const KIND = { front: "前缺口", inter: "中间缺口", tail: "尾缺口" } as const;

function fmt(v: number | null | undefined, d = 2) {
  return v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(d);
}

export default function ResultsPanel({ result, basis }: { result: EvalResult; basis: "volume" | "mass" }) {
  const t = result.totals;
  const m = t.mass;
  const useMass = basis === "mass" && m !== null;
  const T = useMass && m ? m : t;

  return (
    <div className="results">
      <section>
        <h3>① 各馏分产率</h3>
        <table className="yield-table">
          <thead>
            <tr>
              <th>#</th><th>馏分</th><th>区间 ℃</th>
              <th>体积产率 %</th><th>质量产率 %</th><th>标记</th>
            </tr>
          </thead>
          <tbody>
            {result.cuts.map((c) => (
              <tr key={c.index}>
                <td>{c.index + 1}</td>
                <td>{c.name}</td>
                <td>{g(c.start_temp_c)} ~ {g(c.end_temp_c)}</td>
                <td className={basis === "volume" ? "sel" : ""}>{fmt(c.volume_yield_pct)}</td>
                <td className={basis === "mass" ? "sel" : ""}>{fmt(c.mass_yield_pct)}</td>
                <td className="flags">
                  {c.flags.map((f) => (
                    <span key={f} className={`pill ${f}`}>{cutFlagHint(f)}</span>
                  ))}
                </td>
              </tr>
            ))}
            <tr className="sum">
              <td colSpan={3}>名义合计（重叠段被重复计入）</td>
              <td>{fmt(t.nominal_yield_pct)}</td>
              <td>{fmt(m?.nominal_yield_pct)}</td>
              <td />
            </tr>
            <tr className="sum">
              <td colSpan={3}>并集合计（去掉重叠）</td>
              <td>{fmt(t.union_yield_pct)}</td>
              <td>{fmt(m?.union_yield_pct)}</td>
              <td />
            </tr>
          </tbody>
        </table>
      </section>

      <section>
        <h3>② 重叠</h3>
        {result.overlaps.length === 0 ? (
          <p className="ok-line">无重叠。</p>
        ) : (
          <table className="issue-table">
            <thead><tr><th>馏分对</th><th>温度区间 ℃</th><th>宽度 ℃</th><th>体积 %</th><th>质量 %</th><th>范围</th></tr></thead>
            <tbody>
              {result.overlaps.map((o, i) => (
                <tr key={i} className="bad">
                  <td>{o.cut_a_name} × {o.cut_b_name}</td>
                  <td>{g(o.from_temp_c)} ~ {g(o.to_temp_c)}</td>
                  <td>{fmt(o.width_c, 1)}</td>
                  <td>{fmt(o.volume_pct)}</td>
                  <td>{fmt(o.mass_pct)}</td>
                  <td>{o.fully_outside_range ? "范围外（不计）" : o.partial_outside_range ? "部分越界" : "范围内"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h3>③ 缺口</h3>
        {result.gaps.filter((gp) => gp.width_c > 0).length === 0 ? (
          <p className="ok-line">无缺口（首尾覆盖实测范围、馏分连续）。</p>
        ) : (
          <table className="issue-table">
            <thead><tr><th>类型</th><th>位置</th><th>温度区间 ℃</th><th>宽度 ℃</th><th>体积 %</th><th>质量 %</th><th>范围</th></tr></thead>
            <tbody>
              {result.gaps.filter((gp) => gp.width_c > 0).map((gp, i) => (
                <tr key={i} className={gp.fully_outside_range ? "muted" : "warn"}>
                  <td>{KIND[gp.kind]}</td>
                  <td>{[gp.after_cut, gp.before_cut].filter(Boolean).join(" → ") || "—"}</td>
                  <td>{g(gp.from_temp_c)} ~ {g(gp.to_temp_c)}</td>
                  <td>{fmt(gp.width_c, 1)}</td>
                  <td>{fmt(gp.volume_pct)}</td>
                  <td>{fmt(gp.mass_pct)}</td>
                  <td>{gp.fully_outside_range ? "范围外（无数据）" : gp.partial_outside_range ? "部分越界" : "范围内"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h3>④ 残余量与总量核对</h3>
        <table className="balance-table">
          <thead><tr><th>项目</th><th>体积 %</th><th>质量 %</th></tr></thead>
          <tbody>
            <tr><td>馏分并集产率</td><td>{fmt(t.union_yield_pct)}</td><td>{fmt(m?.union_yield_pct)}</td></tr>
            <tr><td>范围内缺口合计（前+中+尾）</td>
              <td>{fmt(t.front_gap_pct + t.inter_gap_pct + t.tail_gap_pct)}</td>
              <td>{m ? fmt(m.front_gap_pct + m.inter_gap_pct + m.tail_gap_pct) : "—"}</td></tr>
            <tr><td>轻端/起点前回收（低于实测起点 {g(result.applicable_range.temp_c[0])} ℃）</td>
              <td>{fmt(t.light_unassigned_pct)}</td><td>{fmt(m?.light_unassigned_pct)}</td></tr>
            <tr><td>范围内未切出馏出液</td>
              <td>{fmt(t.uncut_distillate_pct)}</td><td>{fmt(m?.uncut_distillate_pct)}</td></tr>
            <tr><td>未回收残渣毛额（100−R_max，含损失/不凝气）</td>
              <td>{fmt(t.residue_bottoms_gross_pct)}</td><td>{fmt(m?.residue_bottoms_gross_pct)}</td></tr>
            <tr><td>　扣除声明损失后的残渣净额</td>
              <td>{fmt(t.residue_bottoms_pct)}</td><td>{fmt(m?.residue_bottoms_pct)}</td></tr>
            <tr className="strong"><td>残余合计（未切出+起点前+未回收毛额）</td>
              <td>{fmt(t.residual_total_pct)}</td><td>{fmt(m?.residual_total_pct)}</td></tr>
            <tr><td>其中声明：试验损失（已含在未回收毛额内，不另加）</td>
              <td>{fmt(t.loss_pct)}</td><td>{basis === "mass" ? fmt(m?.loss_pct) : "—"}</td></tr>
            <tr className={`identity ${t.identity_ok ? "ok" : "bad"}`}>
              <td>闭合合计（并集 + 未切出 + 起点前 + 未回收毛额，应 = 100%）</td>
              <td colSpan={2}>
                <b>{fmt(T.identity_sum_pct, 4)}%</b>
                {t.identity_ok ? " ✅ 体积基准闭合" : ` ❌ 残差 ${fmt(t.identity_residual_pct, 4)}%`}
                {useMass && m && !m.identity_ok && m.identity_note && (
                  <span className="mass-note">（质量基准：{m.identity_note}）</span>
                )}
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section className="method">
        <h3>⑤ 计算方法与适用范围（导出同此声明）</h3>
        <ul>
          <li><b>插值：</b>{result.method.interpolation}</li>
          <li><b>曲线坐标：</b>{result.method.recovery_axis}</li>
          <li><b>密度换算：</b>{result.method.mass_conversion}</li>
          <li><b>外推策略：</b>{result.method.extrapolation}</li>
          <li>
            <b>适用温度范围：</b>
            {g(result.applicable_range.temp_c[0])} ~ {g(result.applicable_range.temp_c[1])} ℃
            （回收 {g(result.applicable_range.recovered_pct[0])}% ~ {g(result.applicable_range.recovered_pct[1])}%），
            之外不存在的数据不做任何假设。
          </li>
        </ul>
      </section>
    </div>
  );
}
