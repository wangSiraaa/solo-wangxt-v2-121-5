import type { CutInput } from "../types";
import { g } from "../format";

interface Props {
  cuts: CutInput[];
  lossPct: number;
  basis: "volume" | "mass";
  planName: string;
  range: [number, number];
  onChange: (cuts: CutInput[]) => void;
  onLossChange: (v: number) => void;
  onBasisChange: (v: "volume" | "mass") => void;
  onPlanNameChange: (v: string) => void;
}

const FLAG_ZH: Record<string, string> = {
  out_of_range: "切点越界（范围内才计算）",
  clipped_to_range: "已按实测范围截断",
  zero_width: "零宽：相邻切点相等",
  inverted: "区间反向",
};

export function cutFlagHint(flag: string) {
  return FLAG_ZH[flag] ?? flag;
}

export default function CutEditor({
  cuts, lossPct, basis, planName, range,
  onChange, onLossChange, onBasisChange, onPlanNameChange,
}: Props) {
  const update = (i: number, patch: Partial<CutInput>) =>
    onChange(cuts.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const remove = (i: number) => onChange(cuts.filter((_, j) => j !== i));
  const add = () =>
    onChange([...cuts, { name: `馏分${cuts.length + 1}`, start_temp_c: range[0], end_temp_c: range[1] }]);

  return (
    <div className="editor">
      <div className="row">
        <label>方案名称
          <input value={planName} onChange={(e) => onPlanNameChange(e.target.value)} />
        </label>
        <label>产率口径
          <select value={basis} onChange={(e) => onBasisChange(e.target.value as "volume" | "mass")}>
            <option value="volume">体积 %（进料体积基准）</option>
            <option value="mass">质量 %（进料质量基准，按密度换算）</option>
          </select>
        </label>
        <label>试验损失 %（已含在未回收部分内，用于展示残渣净额；不得超 100−R_max）
          <input type="number" step="0.1" min="0" max="100"
            value={Number.isFinite(lossPct) ? lossPct : ""}
            onChange={(e) => onLossChange(parseFloat(e.target.value))} />
        </label>
        <button className="btn primary" onClick={add}>＋ 添加馏分</button>
      </div>

      <table className="cut-table">
        <thead>
          <tr>
            <th>#</th><th>馏分名称</th><th>初馏点 ℃</th><th>终馏点 ℃</th><th></th>
          </tr>
        </thead>
        <tbody>
          {cuts.map((c, i) => {
            const outside = c.start_temp_c < range[0] || c.end_temp_c > range[1]
              || c.start_temp_c > range[1] || c.end_temp_c < range[0];
            return (
              <tr key={i} className={outside ? "outside" : ""}>
                <td>{i + 1}</td>
                <td><input value={c.name} onChange={(e) => update(i, { name: e.target.value })} /></td>
                <td><input type="number" value={c.start_temp_c}
                  onChange={(e) => update(i, { start_temp_c: parseFloat(e.target.value) })} /></td>
                <td><input type="number" value={c.end_temp_c}
                  onChange={(e) => update(i, { end_temp_c: parseFloat(e.target.value) })} /></td>
                <td><button className="btn ghost" onClick={() => remove(i)}>删除</button></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="hint">
        实测温度范围 {g(range[0])}~{g(range[1])} ℃。切到范围之外的部分<b>不外推</b>，
        只在结果中提示；相邻馏分端点相等为连续切割（无重叠/缺口）。
      </p>
    </div>
  );
}
