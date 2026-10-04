"""累积蒸馏曲线的校验与单调插值。

设计要点
--------
1. 横轴是 **温度**，纵轴是 **累积回收体积百分比** R(T)（进料体积基准）。
   质量百分比不是同一根横轴：见 ``density.py`` 的密度换算。
2. 插值统一采用 SciPy 的 ``PchipInterpolator``（保形分段三次 Hermite / PCHIP），
   它在单调输入下保持单调，不会像普通三次样条那样在平台段产生过冲/假振荡。
3. **严格不外推**：所有求值只在实测温度区间内进行，区间外返回 ``None`` 并
   生成 ``out_of_range`` 提示。导出文件同样写明适用范围。
4. 曲线下降、回收总量异常等数据问题先返回 issue 列表，由前端提示核实。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.interpolate import PchipInterpolator

# 数值容差
_MONO_TOL = 1e-6
_ROUND_TOL = 1e-9


class Severity:
    ERROR = "error"      # 数据不合法，不能计算
    WARNING = "warning"  # 结果可用，但需核实
    INFO = "info"


@dataclass
class Issue:
    code: str
    severity: str
    message: str

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "message": self.message}


@dataclass
class PreparedCurve:
    """校验后的曲线与 PCHIP 插值器。"""

    temps: np.ndarray
    recovered: np.ndarray
    pchip: PchipInterpolator
    issues: list[Issue] = field(default_factory=list)

    @property
    def t_min(self) -> float:
        return float(self.temps[0])

    @property
    def t_max(self) -> float:
        return float(self.temps[-1])

    @property
    def r_min(self) -> float:
        return float(self.recovered[0])

    @property
    def r_max(self) -> float:
        return float(self.recovered[-1])

    @property
    def hard_errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    # ---- 求值（严格不外推）----
    def recovery_at(self, t: float) -> tuple[float | None, Issue | None]:
        """R(T)：温度 -> 累积回收 %。区间外返回 (None, issue)。"""
        if t < self.t_min - _ROUND_TOL:
            return None, Issue(
                "out_of_range",
                Severity.WARNING,
                f"切点 {t:g} ℃ 低于实测起点 {self.t_min:g} ℃，"
                f"低温段无数据，不外推（缺端点请先补试验点）",
            )
        if t > self.t_max + _ROUND_TOL:
            return None, Issue(
                "out_of_range",
                Severity.WARNING,
                f"切点 {t:g} ℃ 高于实测终点 {self.t_max:g} ℃，"
                f"高温段无数据，不外推",
            )
        t = min(max(t, self.t_min), self.t_max)
        return float(self.pchip(t)), None

    def recovery_batch(self, temps: list[float]) -> tuple[list[float | None], list[Issue]]:
        vals: list[float | None] = []
        issues: list[Issue] = []
        for t in temps:
            v, iss = self.recovery_at(t)
            vals.append(v)
            if iss is not None:
                issues.append(iss)
        return vals, issues

    def sample(self, n: int = 200) -> tuple[list[float], list[float]]:
        """在适用温度范围内均匀取样，供前端画曲线（不越出实测范围）。"""
        ts = np.linspace(self.t_min, self.t_max, n)
        rs = self.pchip(ts)
        # PCHIP 单调输入下结果应落在 [r_min, r_max]，做一次保护性裁剪
        rs = np.clip(rs, min(self.r_min, 0.0), max(self.r_max, 100.0))
        return ts.tolist(), rs.tolist()


def prepare_curve(raw_points: list[dict]) -> PreparedCurve:
    """从 ``[{"temp_c":..,"recovered_pct":..}]`` 构造校验后的插值曲线。

    校验规则
    ~~~~~~~~
    * 至少 2 个点、字段合法（由 Pydantic 层先把关）；
    * 温度重复但回收量不同 => error（一条曲线不能有两个值）；
      温度+回收量完全相同 => 去重（info）；
    * 任一相邻段回收量下降（超出容差）=> error：
      累积蒸馏曲线必须单调非降，请核实抄录数据；
    * 末点回收总量 ``R_max`` 与常识范围（80~100%）偏差大 => warning，
      且 ``R_max > 100`` 或明显超过进料 => error；
    * 首点回收量 > 0 => info（缺少 IBP/0% 端点，低温段不完整）；
      末点回收量 < 100 => warning（缺少 100% 端点，高温残渣靠边界界定，
      不外推）。
    """
    issues: list[Issue] = []
    pts = sorted(
        ((float(p["temp_c"]), float(p["recovered_pct"])) for p in raw_points),
        key=lambda x: x[0],
    )
    temps_l: list[float] = []
    rec_l: list[float] = []
    for t, r in pts:
        if temps_l and abs(t - temps_l[-1]) <= _MONO_TOL:
            if abs(r - rec_l[-1]) <= _MONO_TOL:
                issues.append(
                    Issue("duplicate_point", Severity.INFO, f"温度 {t:g} ℃ 处重复点已去重")
                )
                continue
            issues.append(
                Issue(
                    "duplicate_temp_conflict",
                    Severity.ERROR,
                    f"同一温度 {t:g} ℃ 对应两个不同的累积回收量 "
                    f"({rec_l[-1]:g}% 与 {r:g}%)，请核实数据",
                )
            )
            # 保留第一个点，继续检查其余数据
            continue
        temps_l.append(t)
        rec_l.append(r)

    if len(temps_l) < 2:
        issues.append(
            Issue("too_few_points", Severity.ERROR, "去重后曲线点数少于 2，无法插值")
        )
        # 无法构造 PCHIP，退化为常量（调用方应先看 hard_errors）
        temps = np.array(temps_l or [0.0, 1.0])
        rec = np.array(rec_l or [0.0, 0.0])
        if temps.size < 2:
            temps = np.array([temps[0], temps[0] + 1.0])
            rec = np.array([rec[0], rec[0]])
        return PreparedCurve(temps, rec, PchipInterpolator(temps, rec), issues)

    temps = np.asarray(temps_l, dtype=float)
    rec = np.asarray(rec_l, dtype=float)

    diffs = np.diff(rec)
    n_drop = int(np.sum(diffs < -_MONO_TOL))
    if n_drop:
        first = int(np.argmax(diffs < -_MONO_TOL))
        issues.append(
            Issue(
                "curve_decreases",
                Severity.ERROR,
                f"累积回收曲线出现 {n_drop} 处下降，例如 "
                f"{temps[first]:g} ℃({rec[first]:g}%) -> "
                f"{temps[first + 1]:g} ℃({rec[first + 1]:g}%)。"
                "累积蒸馏曲线必须单调非降，请核实试验/抄录数据",
            )
        )

    r_min, r_max = rec[0], rec[-1]
    if r_min > 0.0 + 1e-4:
        issues.append(
            Issue(
                "missing_low_endpoint",
                Severity.INFO,
                f"首个实测点回收量为 {r_min:g}%（温度 {temps[0]:g} ℃），"
                "缺少 0% 端点（IBP 之前的轻端无法界定），低温侧不外推",
            )
        )
    if r_max < 100.0 - 1e-4:
        issues.append(
            Issue(
                "missing_high_endpoint",
                Severity.WARNING,
                f"最末实测点仅回收 {r_max:g}%（温度 {temps[-1]:g} ℃），"
                "缺少 100% 端点；高于该温度的馏分无数据、不外推，"
                "残余量按‘未回收’处理",
            )
        )
    if r_max > 100.0 + 1e-4:
        issues.append(
            Issue(
                "recovery_over_100",
                Severity.ERROR,
                f"累积回收总量为 {r_max:g}%，超过进料的 100%，不可能成立，"
                "请核实基准（体积/质量是否混用）与抄录数据",
            )
        )
    elif r_max < 80.0:
        issues.append(
            Issue(
                "recovery_total_low",
                Severity.WARNING,
                f"总回收量仅 {r_max:g}%，明显低于常规蒸馏试验的常见范围"
                "（约 80~100%，含残渣），请核实是否漏记重端馏分或试验中断",
            )
        )

    return PreparedCurve(temps, rec, PchipInterpolator(temps, rec), issues)
