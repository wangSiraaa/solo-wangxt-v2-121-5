"""按温度切点计算各馏分产率，并核对重叠、缺口、残余与总量闭合。

物理约定（体积基准，进料 = 100 体积单位）
-----------------------------------------
设累积回收曲线为 R(T)（单调非降，单位：进料体积 %）：

* 馏分 i（温度区间 [s_i, e_i]）产率 = R(e_i) − R(s_i)；
* **实测温度范围以外不外推**：切点越界的部分不计产率，只给提示；
* 相邻馏分共享端点（e_i == s_j）是正常的连续切割，缺口/重叠均为 0；
* 区间交错 => 重叠（overlap，同一部分被两个馏分重复计产率）；
* 区间断开 => 缺口（gap，能蒸出却未被任何馏分拿走）；
* 首尾之外分别记“前缺口/尾缺口”；
* 总量闭合恒等式（体积基准）：

      馏分并集产率 + 范围内缺口 + 轻端未归属(R_min) + 残渣 + 损失 = 100%

  其中“残渣”= 100% − 损失 − R(T_max)，即试验中最重、从未蒸出的部分。

质量基准用 ``density.mass_increment`` 逐段换算后执行同一套区间与闭合逻辑；
残渣质量需要单独的残渣密度，缺失则残渣质量留空并提示。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .curve import Issue, PreparedCurve, Severity
from .density import DensityModel, mass_increment

_ID_TOL = 1e-6
_W_TOL = 1e-9


@dataclass
class _Interval:
    lo: float
    hi: float


def _merge(intervals: list[_Interval]) -> list[_Interval]:
    if not intervals:
        return []
    iv = sorted(intervals, key=lambda x: (x.lo, x.hi))
    out = [_Interval(iv[0].lo, iv[0].hi)]
    for x in iv[1:]:
        if x.lo <= out[-1].hi + _W_TOL:
            out[-1].hi = max(out[-1].hi, x.hi)
        else:
            out.append(_Interval(x.lo, x.hi))
    return out


def evaluate_plan(
    curve: PreparedCurve,
    cuts: list[dict],
    loss_pct: float = 0.0,
    basis: str = "volume",
    density: DensityModel | None = None,
    feed_density: float | None = None,
    residue_density: float | None = None,
) -> dict:
    """计算方案。返回结构化结果（cuts/overlaps/gaps/totals/issues/method/range）。"""
    issues: list[Issue] = list(curve.issues)
    tmin, tmax = curve.t_min, curve.t_max
    rmin, rmax = curve.r_min, curve.r_max

    # ---- 体积区间 -> 回收增量（严格限定在实测范围内）----
    def interval_vol(u: float, v: float) -> tuple[float | None, str]:
        """返回 ([u,v] 落在实测范围内的) 体积回收增量及状态。"""
        lo, hi = min(u, v), max(u, v)
        if hi <= lo + _W_TOL:
            if lo < tmin - _W_TOL or lo > tmax + _W_TOL:
                return None, "outside"
            return 0.0, "inside"
        clo, chi = max(lo, tmin), min(hi, tmax)
        if chi <= clo:
            return None, "outside"
        inc = float(curve.pchip(chi) - curve.pchip(clo))
        state = "inside" if (clo == lo and chi == hi) else "partial"
        return max(inc, 0.0), state

    # ---- 逐馏分 ----
    cut_rows: list[dict] = []
    valid_intervals: list[_Interval] = []
    endpoints: list[float] = []
    for idx, c in enumerate(cuts):
        s, e = float(c["start_temp_c"]), float(c["end_temp_c"])
        name = str(c["name"])
        endpoints.extend([s, e])
        flags: list[str] = []
        inverted = e < s - _W_TOL
        zero = abs(e - s) <= _W_TOL and not inverted
        if inverted:
            flags.append("inverted")
            issues.append(
                Issue(
                    "cut_inverted",
                    Severity.ERROR,
                    f"馏分「{name}」终馏点 {e:g} ℃ 低于初馏点 {s:g} ℃，"
                    "区间反向，已跳过不计产率",
                )
            )
        if zero:
            flags.append("zero_width")
            issues.append(
                Issue(
                    "cut_zero_width",
                    Severity.WARNING,
                    f"馏分「{name}」初/终切点相等（{s:g} ℃），宽度为零，产率为 0%",
                )
            )
        if s < tmin - _W_TOL or e > tmax + _W_TOL:
            flags.append("out_of_range")

        rs, iss_s = curve.recovery_at(s)
        re_, iss_e = curve.recovery_at(e)
        for it in (iss_s, iss_e):
            if it is not None:
                issues.append(
                    Issue(
                        it.code,
                        it.severity,
                        f"馏分「{name}」：{it.message}",
                    )
                )

        vol = None
        clipped = None
        if not inverted:
            vol, state = interval_vol(s, e)
            if state == "partial":
                flags.append("clipped_to_range")
            if state != "outside":
                clo, chi = max(min(s, e), tmin), min(max(s, e), tmax)
                clipped = [clo, chi]
                if not zero:
                    valid_intervals.append(_Interval(clo, chi))

        cut_rows.append(
            {
                "index": idx,
                "name": name,
                "start_temp_c": s,
                "end_temp_c": e,
                "flags": flags,
                "start_recovery_pct": round(rs, 4) if rs is not None else None,
                "end_recovery_pct": round(re_, 4) if re_ is not None else None,
                "clipped_range_c": clipped,
                "volume_yield_pct": round(vol, 4) if vol is not None else None,
                "mass_yield_pct": None,  # 下方统一补
            }
        )

    # ---- 重叠（所有成对流，按实测范围内的交集计）----
    overlaps: list[dict] = []
    for i in range(len(cuts)):
        for j in range(i + 1, len(cuts)):
            a, b = cuts[i], cuts[j]
            u = max(float(a["start_temp_c"]), float(b["start_temp_c"]))
            v = min(float(a["end_temp_c"]), float(b["end_temp_c"]))
            if v <= u + _W_TOL:
                continue  # 至多共端点，不算重叠
            inc, state = interval_vol(u, v)
            overlaps.append(
                {
                    "cut_a": i,
                    "cut_b": j,
                    "cut_a_name": a["name"],
                    "cut_b_name": b["name"],
                    "from_temp_c": u,
                    "to_temp_c": v,
                    "width_c": round(v - u, 4),
                    "volume_pct": round(inc, 4) if inc is not None else None,
                    "partial_outside_range": state == "partial",
                    "fully_outside_range": state == "outside",
                }
            )

    # ---- 缺口（按起点排序后的相邻馏分之间 + 首尾）----
    order = sorted(range(len(cuts)), key=lambda i: (
        float(cuts[i]["start_temp_c"]), float(cuts[i]["end_temp_c"])
    ))
    gaps: list[dict] = []

    def add_gap(kind: str, u: float, v: float, a_name: str | None, b_name: str | None) -> None:
        width = v - u
        if width <= _W_TOL:
            return  # 共享端点 => 连续无缺口；反向区间不构成缺口
        inc, state = interval_vol(u, v)
        gaps.append(
            {
                "kind": kind,
                "from_temp_c": u,
                "to_temp_c": v,
                "width_c": round(width, 4),
                "after_cut": a_name,
                "before_cut": b_name,
                "volume_pct": round(inc, 4) if inc is not None else None,
                "partial_outside_range": state == "partial",
                "fully_outside_range": state == "outside",
            }
        )

    first = cuts[order[0]]
    fs = float(first["start_temp_c"])
    if fs <= tmin:
        if fs < tmin:  # 首切点伸到实测范围以下：低温段无数据、不外推
            add_gap("front", fs, tmin, None, str(first["name"]))
    else:
        add_gap("front", tmin, fs, None, str(first["name"]))
    for k in range(len(order) - 1):
        a, b = cuts[order[k]], cuts[order[k + 1]]
        add_gap(
            "inter",
            float(a["end_temp_c"]),
            float(b["start_temp_c"]),
            str(a["name"]),
            str(b["name"]),
        )
    last = cuts[order[-1]]
    le = float(last["end_temp_c"])
    if le < tmax:
        add_gap("tail", le, tmax, str(last["name"]), None)
    elif le > tmax:  # 末切点伸到实测范围以上：高温段无数据、不外推
        add_gap("tail", tmax, le, str(last["name"]), None)

    # ---- 体积总量 ----
    merged = _merge(valid_intervals)
    union_vol = 0.0
    for iv in merged:
        inc, _ = interval_vol(iv.lo, iv.hi)
        union_vol += inc or 0.0
    nominal_vol = sum(
        r["volume_yield_pct"] for r in cut_rows if r["volume_yield_pct"] is not None
    )
    overlap_vol = max(nominal_vol - union_vol, 0.0)

    def gap_sum(kind: str) -> float:
        return sum(
            g["volume_pct"] for g in gaps
            if g["kind"] == kind and g["volume_pct"] is not None
        )

    front_vol, inter_vol, tail_vol = (gap_sum(k) for k in ("front", "inter", "tail"))
    # 体积基准物料平衡（进料 = 100 体积单位）：
    #   已回收 R_max = 馏分并集 + 范围内未切出 + 起点前回收(R_min)
    #   未回收 100−R_max = 塔底残渣（含蒸馏损失/不凝气，体积近似）
    # 二者相加恒为 100%。用户给的“试验损失”从残渣内部扣除展示（residue_bottoms_net）。
    uncut_vol = max(rmax - rmin - union_vol, 0.0)
    light_vol = rmin  # 曲线起点之前的回收液（缺 0% 端点时 >0）
    bottoms_gross_vol = 100.0 - rmax
    bottoms_net_vol = max(bottoms_gross_vol - loss_pct, 0.0)
    loss_over_unrecovered = max(loss_pct - bottoms_gross_vol, 0.0)
    # 残余合计 = 已回收中未切走的部分 + 起点前回收 + 未回收（残渣毛额）
    residual_vol = uncut_vol + light_vol + bottoms_gross_vol
    identity_vol = union_vol + uncut_vol + light_vol + bottoms_gross_vol
    identity_res = identity_vol - 100.0

    if loss_over_unrecovered > _ID_TOL:
        sev = Severity.ERROR if rmax >= 100.0 - 1e-6 else Severity.WARNING
        issues.append(
            Issue(
                "loss_exceeds_unrecovered",
                sev,
                f"试验损失 {loss_pct:g}% 超过未回收余量 100−{rmax:g}%="
                f"{bottoms_gross_vol:g}%，物料平衡无法容纳；"
                "请核实损失口径（通常损失已包含在未回收部分中）或回收总量",
            )
        )

    # ---- 质量基准（有密度模型时）----
    mass_block: dict = _mass_block(
        curve=curve,
        density=density,
        feed_density=feed_density,
        residue_density=residue_density,
        cuts=cuts,
        cut_rows=cut_rows,
        valid_intervals=valid_intervals,
        merged=merged,
        overlaps=overlaps,
        gaps=gaps,
        loss_pct=loss_pct,
        tmin=tmin,
        tmax=tmax,
        issues=issues,
    )

    totals = {
        "nominal_yield_pct": round(nominal_vol, 4),
        "union_yield_pct": round(union_vol, 4),
        "overlap_pct": round(overlap_vol, 4),
        "front_gap_pct": round(front_vol, 4),
        "inter_gap_pct": round(inter_vol, 4),
        "tail_gap_pct": round(tail_vol, 4),
        "uncut_distillate_pct": round(uncut_vol, 4),
        "light_unassigned_pct": round(light_vol, 4),
        # 残渣毛额 = 100−R_max（含试验损失/不凝气）；净额 = 毛额 − 损失
        "residue_bottoms_pct": round(bottoms_net_vol, 4),
        "residue_bottoms_gross_pct": round(bottoms_gross_vol, 4),
        "residual_total_pct": round(residual_vol, 4),
        "loss_pct": loss_pct,
        "identity_sum_pct": round(identity_vol, 6),
        "identity_residual_pct": round(identity_res, 6),
        "identity_ok": abs(identity_res) <= _ID_TOL,
    }
    totals["mass"] = mass_block

    method = {
        "interpolation": "SciPy PchipInterpolator（PCHIP 保形分段三次 Hermite，单调保持、不过冲）",
        "recovery_axis": "累积回收体积百分比（进料体积基准），纵轴随温度单调非降",
        "mass_conversion": (
            "各曲线结点段体积增量 × 段中点温度对应的馏出液密度（线性插值）/ 进料密度；"
            "残渣使用独立残渣密度；质量%与体积%是两套基准，不共用横轴"
        ),
        "extrapolation": "不做任何外推：切点超出实测温度区间的部分不计产率，只提示核实",
        "identity": (
            "体积基准：馏分并集 + 范围内未切出 + 起点前回收(R_min) + "
            "未回收残渣(100−R_max，含损失) = 100%"
        ),
    }

    return {
        "basis": basis,
        "applicable_range": {
            "temp_c": [tmin, tmax],
            "recovered_pct": [rmin, rmax],
            "extrapolation": "none",
        },
        "cuts": cut_rows,
        "overlaps": overlaps,
        "gaps": gaps,
        "totals": totals,
        "issues": [i.as_dict() for i in issues],
        "has_blocking_errors": any(i.severity == Severity.ERROR for i in issues),
        "method": method,
    }


def _mass_block(
    *,
    curve: PreparedCurve,
    density: DensityModel | None,
    feed_density: float | None,
    residue_density: float | None,
    cuts: list[dict],
    cut_rows: list[dict],
    valid_intervals: list[_Interval],
    merged: list[_Interval],
    overlaps: list[dict],
    gaps: list[dict],
    loss_pct: float,
    tmin: float,
    tmax: float,
    issues: list[Issue],
) -> dict | None:
    """同一套区间逻辑的质量基准版本；缺少密度数据时返回 None。"""
    if density is None or feed_density is None:
        if any(f in ("out_of_range", "clipped_to_range") for r in cut_rows for f in r["flags"]):
            pass
        issues.append(
            Issue(
                "mass_basis_unavailable",
                Severity.WARNING,
                "未提供馏出温度—密度表或进料密度，无法给出质量百分数，"
                "当前仅输出体积基准（质量%与体积%不得混用）",
            )
        )
        return None

    def m_inc(u: float, v: float) -> tuple[float | None, list[Issue], float]:
        return mass_increment(curve, density, feed_density, u, v)

    local_issues: list[Issue] = []
    nominal_mass = 0.0
    for row, c in zip(cut_rows, cuts):
        if "inverted" in row["flags"]:
            continue
        m, ii, _miss = m_inc(float(c["start_temp_c"]), float(c["end_temp_c"]))
        local_issues.extend(ii)
        row["mass_yield_pct"] = round(m, 4) if m is not None else None
        if m is not None:
            nominal_mass += m

    union_mass: float | None = 0.0
    union_missing_vol = 0.0
    for iv in merged:
        m, ii, miss = m_inc(iv.lo, iv.hi)
        local_issues.extend(ii)
        union_missing_vol += miss
        if m is None:
            union_mass = None  # 至少有一个并集段完全无密度 => 并集质量不可得
        elif union_mass is not None:
            union_mass += m
    overlap_mass = (
        max(nominal_mass - union_mass, 0.0) if union_mass is not None else None
    )

    def m_gap(kind: str) -> float | None:
        total = 0.0
        complete = True
        for gp in gaps:
            if gp["kind"] != kind:
                continue
            m, ii, _miss = m_inc(gp["from_temp_c"], gp["to_temp_c"])
            local_issues.extend(ii)
            if m is None:
                complete = False
            else:
                total += m
        return total if complete else None

    front_m, inter_m, tail_m = (m_gap(k) for k in ("front", "inter", "tail"))

    for ov in overlaps:
        m, ii, _miss = m_inc(ov["from_temp_c"], ov["to_temp_c"])
        local_issues.extend(ii)
        ov["mass_pct"] = round(m, 4) if m is not None else None
    for gp in gaps:
        m, ii, _miss = m_inc(gp["from_temp_c"], gp["to_temp_c"])
        local_issues.extend(ii)
        gp["mass_pct"] = round(m, 4) if m is not None else None

    # 实测范围内总回收质量与“起点前回收”质量
    recovered_mass_full, ii_full, full_missing_vol = m_inc(tmin, tmax)
    local_issues.extend(ii_full)
    rmin = curve.r_min
    rmax = curve.r_max
    if rmin > 1e-9:
        light_mass: float | None = None  # 低于最低实测温度部分，密度未知
    else:
        light_mass = 0.0

    uncut_mass: float | None = None
    bottoms_mass: float | None = None
    bottoms_gross_mass: float | None = None
    identity_mass: float | None = None
    identity_ok = False
    identity_note = ""

    if recovered_mass_full is not None and union_mass is not None:
        uncut_mass = max(recovered_mass_full - union_mass, 0.0)
        if residue_density is not None:
            # 未回收体积百分数 = 100 − R_max（含损失/不凝气），
            # 按残渣密度换算为进料质量基准；试验损失从残渣内部扣减展示
            bottoms_vol_gross = max(100.0 - rmax, 0.0)
            bottoms_gross_mass = bottoms_vol_gross * residue_density / feed_density
            bottoms_vol_net = max(bottoms_vol_gross - loss_pct, 0.0)
            bottoms_mass = bottoms_vol_net * residue_density / feed_density

            if light_mass is not None and full_missing_vol <= 1e-9:
                identity_mass = union_mass + uncut_mass + light_mass + bottoms_gross_mass
                identity_ok = abs(identity_mass - 100.0) <= 0.1  # 密度有效位数下容差 0.1 个百分点
                if not identity_ok:
                    residual = identity_mass - 100.0
                    implied = recovered_mass_full / max(rmax, 1e-12) * feed_density
                    identity_note = (
                        f"质量闭合合计为 {identity_mass:.2f}%（残差 {residual:+.2f}%）。"
                        f"按回收体积反算的馏出液平均密度约 {implied:.3f} g/cm³，"
                        f"与进料密度 {feed_density:g} g/cm³ 不一致——这通常意味着"
                        "密度表/进料密度/回收曲线不是同一基准（如 20℃ 与 15℃ 混用、"
                        "体积收缩未修正）或抄录有误，请核实后质量基准才可严格闭合；"
                        "体积基准闭合始终成立"
                    )
                    issues.append(
                        Issue(
                            "mass_identity_mismatch",
                            Severity.WARNING,
                            identity_note,
                        )
                    )
            elif light_mass is None:
                identity_note = (
                    f"曲线首点已有 {rmin:g}% 回收且其密度未知，质量基准无法严格闭合，"
                    "体积基准闭合仍然成立"
                )
        else:
            identity_note = "未提供残渣密度，残渣质量与质量基准闭合无法计算"
            issues.append(
                Issue(
                    "residue_density_missing",
                    Severity.WARNING,
                    "缺少残渣（塔底）20 ℃ 密度，质量基准的残渣项留空；"
                    "体积基准结果不受影响",
                )
            )
    elif recovered_mass_full is None or union_mass is None:
        identity_note = "密度表未覆盖全部馏分段，质量基准合计不完整"

    # 密度表缺失只提示一次
    seen: set[str] = set()
    for it in local_issues:
        key = it.code
        if key in seen:
            continue
        seen.add(key)
        issues.append(it)

    residual_total: float | None = None
    if uncut_mass is not None and bottoms_gross_mass is not None and light_mass is not None:
        residual_total = uncut_mass + light_mass + bottoms_gross_mass

    return {
        "nominal_yield_pct": round(nominal_mass, 4),
        "union_yield_pct": round(union_mass, 4) if union_mass is not None else None,
        "overlap_pct": round(overlap_mass, 4) if overlap_mass is not None else None,
        "front_gap_pct": round(front_m, 4) if front_m is not None else None,
        "inter_gap_pct": round(inter_m, 4) if inter_m is not None else None,
        "tail_gap_pct": round(tail_m, 4) if tail_m is not None else None,
        "uncut_distillate_pct": round(uncut_mass, 4) if uncut_mass is not None else None,
        "light_unassigned_pct": light_mass,
        "residue_bottoms_pct": round(bottoms_mass, 4) if bottoms_mass is not None else None,
        "residue_bottoms_gross_pct": round(bottoms_gross_mass, 4) if bottoms_gross_mass is not None else None,
        "residual_total_pct": round(residual_total, 4) if residual_total is not None else None,
        "loss_pct": loss_pct,
        "identity_sum_pct": round(identity_mass, 6) if identity_mass is not None else None,
        "identity_ok": identity_ok,
        "identity_note": identity_note,
        "residue_density_g_cm3": residue_density,
    }
