import { useMemo } from "react";
import type { CurveSample, CutInput, Overlap } from "../types";
import { g as fmtG } from "../format";

interface Props {
  sample: CurveSample;
  cuts: CutInput[];
  overlaps: Overlap[];
}

const W = 820;
const H = 440;
const M = { top: 24, right: 24, bottom: 52, left: 64 };
const PALETTE = [
  "#2563eb", "#0891b2", "#16a34a", "#ca8a04",
  "#dc2626", "#9333ea", "#0d9488", "#ea580c",
];

export default function CurveChart({ sample, cuts, overlaps }: Props) {
  const [dmin, dmax] = sample.range.temp_c;

  const { xMin, xMax } = useMemo(() => {
    const ts = [...sample.temps_c, ...cuts.flatMap((c) => [c.start_temp_c, c.end_temp_c])];
    let lo = Math.min(...ts);
    let hi = Math.max(...ts);
    const pad = Math.max((hi - lo) * 0.06, 4);
    return { xMin: lo - pad, xMax: hi + pad };
  }, [sample, cuts]);

  const x = (t: number) => M.left + ((t - xMin) / (xMax - xMin)) * (W - M.left - M.right);
  const y = (r: number) => H - M.bottom - (r / 100) * (H - M.top - M.bottom);

  const path = useMemo(() => {
    const pts = sample.temps_c.map((t, i) => `${x(t)},${y(sample.recovered_pct[i])}`);
    return "M" + pts.join(" L");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sample, xMin, xMax]);

  const gridVals = [0, 20, 40, 60, 80, 100];
  const xTicks = useMemo(() => {
    const n = 8;
    const step = (xMax - xMin) / n;
    const nice = Math.ceil(step / 10) * 10 || 10;
    const out: number[] = [];
    for (let t = Math.ceil(xMin / nice) * nice; t <= xMax; t += nice) out.push(t);
    return out;
  }, [xMin, xMax]);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="累积蒸馏曲线与切点">
      <defs>
        <pattern id="hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="8" height="8" fill="#f3f4f6" />
          <line x1="0" y1="0" x2="0" y2="8" stroke="#d1d5db" strokeWidth="2" />
        </pattern>
        <pattern id="ovhatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" stroke="#dc2626" strokeWidth="2.5" />
        </pattern>
      </defs>

      {/* 实测范围外：无数据·不外推 */}
      {xMin < dmin && (
        <rect x={x(xMin)} y={M.top} width={x(dmin) - x(xMin)} height={H - M.top - M.bottom} fill="url(#hatch)" />
      )}
      {xMax > dmax && (
        <rect x={x(dmax)} y={M.top} width={x(xMax) - x(dmax)} height={H - M.top - M.bottom} fill="url(#hatch)" />
      )}

      {/* 网格与坐标 */}
      {gridVals.map((gv) => (
        <g key={gv}>
          <line x1={M.left} x2={W - M.right} y1={y(gv)} y2={y(gv)} stroke="#e5e7eb" />
          <text x={M.left - 8} y={y(gv) + 4} textAnchor="end" fontSize="11" fill="#6b7280">{gv}%</text>
        </g>
      ))}
      {xTicks.map((t) => (
        <g key={t}>
          <line x1={x(t)} x2={x(t)} y1={M.top} y2={H - M.bottom} stroke="#f1f5f9" />
          <text x={x(t)} y={H - M.bottom + 18} textAnchor="middle" fontSize="11" fill="#6b7280">{t}</text>
        </g>
      ))}
      <line x1={M.left} x2={W - M.right} y1={y(0)} y2={y(0)} stroke="#374151" />
      <line x1={M.left} x2={M.left} y1={M.top} y2={H - M.bottom} stroke="#374151" />
      <text x={(M.left + W - M.right) / 2} y={H - 10} textAnchor="middle" fontSize="12" fill="#374151">
        温度 ℃（仅 {fmtG(dmin)}~{fmtG(dmax)} ℃ 有离线试验数据）
      </text>
      <text x={16} y={(M.top + H - M.bottom) / 2} textAnchor="middle" fontSize="12"
        transform={`rotate(-90 16 ${(M.top + H - M.bottom) / 2})`} fill="#374151">
        累积回收 体积%（进料基准）
      </text>

      {/* 馏分色带 */}
      {cuts.map((c, i) => {
        const lo = Math.max(Math.min(c.start_temp_c, c.end_temp_c), dmin);
        const hi = Math.min(Math.max(c.start_temp_c, c.end_temp_c), dmax);
        if (hi <= lo) return null;
        const color = PALETTE[i % PALETTE.length];
        return (
          <g key={i}>
            <rect x={x(lo)} y={M.top} width={Math.max(x(hi) - x(lo), 1)}
              height={H - M.top - M.bottom} fill={color} opacity={0.08} />
            <line x1={x(c.start_temp_c)} x2={x(c.start_temp_c)} y1={M.top} y2={H - M.bottom}
              stroke={color} strokeWidth={1.5} strokeDasharray="5 3" />
            <line x1={x(c.end_temp_c)} x2={x(c.end_temp_c)} y1={M.top} y2={H - M.bottom}
              stroke={color} strokeWidth={1.5} strokeDasharray="5 3" />
            <text x={(x(lo) + x(hi)) / 2} y={M.top + 14} textAnchor="middle"
              fontSize="11" fill={color} fontWeight="600">
              {c.name}
            </text>
          </g>
        );
      })}

      {/* 重叠区红色斜线 */}
      {overlaps.filter((o) => !o.fully_outside_range).map((o, i) => {
        const lo = Math.max(o.from_temp_c, dmin);
        const hi = Math.min(o.to_temp_c, dmax);
        return (
          <rect key={i} x={x(lo)} y={M.top} width={Math.max(x(hi) - x(lo), 1)}
            height={H - M.top - M.bottom} fill="url(#ovhatch)" opacity={0.5} />
        );
      })}

      {/* PCHIP 曲线（仅实测范围内画）*/}
      <path d={path} fill="none" stroke="#1d4ed8" strokeWidth={2.5} />
      {sample.raw_points.map((p, i) => (
        <circle key={i} cx={x(p.temp_c)} cy={y(p.recovered_pct)} r={3.5}
          fill="#fff" stroke="#1d4ed8" strokeWidth={1.5} />
      ))}

      {/* 适用范围边界 */}
      <line x1={x(dmin)} x2={x(dmin)} y1={M.top} y2={H - M.bottom} stroke="#9ca3af" strokeWidth={1} />
      <line x1={x(dmax)} x2={x(dmax)} y1={M.top} y2={H - M.bottom} stroke="#9ca3af" strokeWidth={1} />
      {xMin < dmin && (
        <text x={(x(xMin) + x(dmin)) / 2} y={(M.top + H - M.bottom) / 2} textAnchor="middle"
          fontSize="12" fill="#9ca3af">无数据 · 不外推</text>
      )}
      {xMax > dmax && (
        <text x={(x(dmax) + x(xMax)) / 2} y={(M.top + H - M.bottom) / 2} textAnchor="middle"
          fontSize="12" fill="#9ca3af">无高温数据 · 不外推</text>
      )}
    </svg>
  );
}
