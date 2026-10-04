"""质量百分比 ↔ 体积百分比的有依据换算。

原则
----
* 累积蒸馏曲线的原生横轴是 **温度**，纵轴 R(T) 是 **进料体积基准** 的
  累积回收百分比。质量百分比 *不能* 直接当成同一根轴。
* 换算以 100 个体积单位进料为基准：
  进料质量 = 100 · ρ_feed
  某温度段 [a,b] 馏出液质量 = Σ ΔV_i · ρ_i
      （ΔV_i 为第 i 个曲线结点段的体积回收增量，ρ_i 为该段馏出液密度）
  该段质量百分数 = Σ ΔV_i · ρ_i / ρ_feed
* ρ_i 由“馏出温度—密度表”按 **结点段中点温度做分段线性插值** 得到。
  馏分越重密度越高，因此必须分段取值；密度表覆盖不到的温度段一律不猜，
  返回缺数提示。
* 蒸馏残渣（塔底）密度与馏出液不同，单独给 ``residue_density_g_cm3``；
  缺失时只在体积基准出结果，质量基准的残渣项留空并提示。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .curve import Issue, PreparedCurve, Severity

_DENS_TOL = 1e-9


@dataclass
class DensityModel:
    temps: np.ndarray
    densities: np.ndarray
    issues: list[Issue] = field(default_factory=list)

    @property
    def t_min(self) -> float:
        return float(self.temps[0])

    @property
    def t_max(self) -> float:
        return float(self.temps[-1])

    def at(self, t: float) -> tuple[float | None, Issue | None]:
        """段中点温度 -> 密度 g/cm³（线性插值，严格不外推）。"""
        if t < self.temps[0] - _DENS_TOL or t > self.temps[-1] + _DENS_TOL:
            return None, Issue(
                "density_out_of_range",
                Severity.WARNING,
                f"馏出段中点 {t:g} ℃ 超出密度表范围 "
                f"[{self.temps[0]:g}, {self.temps[-1]:g}] ℃，"
                "该段密度缺失，质量基准结果无法计算（不得借用单一密度凑数）",
            )
        return float(np.interp(t, self.temps, self.densities)), None


def prepare_density(rows: list[dict]) -> DensityModel | None:
    """构造密度模型；无密度行时返回 None（质量基准不可用）。"""
    if not rows:
        return None
    data = sorted((float(r["temp_c"]), float(r["density_g_cm3"])) for r in rows)
    temps = np.array([d[0] for d in data], dtype=float)
    dens = np.array([d[1] for d in data], dtype=float)

    issues: list[Issue] = []
    if np.any(np.diff(temps) <= 0):
        issues.append(
            Issue(
                "density_temp_duplicate",
                Severity.ERROR,
                "密度表存在重复温度，请每个代表温度只保留一行",
            )
        )
    if np.any(np.diff(dens) < -0.02):
        issues.append(
            Issue(
                "density_decreases",
                Severity.WARNING,
                "馏出密度随温度出现明显下降（越重的馏分通常密度越高），"
                "请核实密度测量或单位（20 ℃ 标准密度）",
            )
        )
    return DensityModel(temps, dens, issues)


def mass_increment(
    curve: PreparedCurve,
    density: DensityModel,
    feed_density: float,
    a: float,
    b: float,
) -> tuple[float | None, list[Issue], float]:
    """温度段 [a,b] 内馏出液的质量百分数（进料质量基准，%）。

    逐曲线结点段积分：部分重叠段的体积增量用 PCHIP 在该小段两端求值，
    密度取该结点段（而非任意切点）中点对应的馏出液密度。
    超出实测范围的部分不计（不评估），范围内但密度表缺失的段不计入数值、
    其体积量经第三个返回值 ``missing_vol_pct`` 单独报告（也不外推密度）。

    返回 ``(质量百分数或None, issues, 缺密度段体积百分数)``；
    全部段都缺密度时质量百分数为 None。
    """
    issues: list[Issue] = list(density.issues)
    lo, hi = min(a, b), max(a, b)
    lo = max(lo, curve.t_min)
    hi = min(hi, curve.t_max)
    if hi < lo - 1e-12:
        return 0.0, issues, 0.0

    knots = curve.temps
    mass_units = 0.0
    counted_any = False
    missing: set[float] = set()
    missing_vol = 0.0
    for i in range(len(knots) - 1):
        u, v = float(knots[i]), float(knots[i + 1])
        seg_lo, seg_hi = max(lo, u), min(hi, v)
        if seg_hi <= seg_lo:
            continue
        dv = float(curve.pchip(seg_hi) - curve.pchip(seg_lo))
        mid = 0.5 * (u + v)
        rho, iss = density.at(mid)
        if rho is None:
            missing.add(round(mid, 6))
            missing_vol += dv
            continue
        mass_units += dv * rho
        counted_any = True

    if missing:
        shown = ", ".join(f"{m:g} ℃" for m in sorted(missing)[:5])
        issues.append(
            Issue(
                "density_missing_segments",
                Severity.WARNING,
                f"以下馏出段缺密度数据（段中点 {shown}），"
                f"对应 {missing_vol:.2f} 个体积百分点未计入质量百分数"
                "（不猜测密度），请补充密度表后再看完整质量基准",
            )
        )
        if not counted_any:
            return None, issues, missing_vol
    return mass_units / feed_density, issues, missing_vol
