"""方案导出：Markdown 与 JSON。

每份导出都必须写明：插值方法、密度换算依据、温度适用范围、“不外推”声明。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone


def _fmt(v, nd: int = 2) -> str:
    if v is None:
        return "—（数据不足）"
    return f"{v:.{nd}f}"


def build_markdown(exp: dict, plan: dict, result: dict) -> str:
    rng = result["applicable_range"]
    totals = result["totals"]
    basis_name = "质量基准（进料质量 %）" if plan["basis"] == "mass" else "体积基准（进料体积 %）"
    lines: list[str] = []
    lines.append(f"# 蒸馏切割方案：{plan['name']}")
    lines.append("")
    lines.append(f"- 试验：{exp['name']}" + (f"（样品 {exp['sample_id']}）" if exp.get("sample_id") else ""))
    lines.append(f"- 导出时间（UTC）：{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}")
    lines.append(f"- 当前产率口径：{basis_name}")
    cond = exp.get("conditions") or {}
    if cond:
        lines.append("- 试验条件：" + "；".join(f"{k}={v}" for k, v in cond.items()))
    lines.append(
        f"- 进料密度（20 ℃）：{exp['feed_density_g_cm3']:.3f} g/cm³"
    )
    lines.append("")

    lines.append("## 1. 方法与适用范围")
    m = result["method"]
    lines.append(f"- 插值方法：{m['interpolation']}")
    lines.append(f"- 曲线坐标：{m['recovery_axis']}")
    lines.append(f"- 质量/体积换算：{m['mass_conversion']}")
    lines.append(f"- 外推策略：{m['extrapolation']}")
    lines.append(
        f"- 适用温度范围：{rng['temp_c'][0]:g} ~ {rng['temp_c'][1]:g} ℃；"
        f"对应回收 {rng['recovered_pct'][0]:g} ~ {rng['recovered_pct'][1]:g}%"
    )
    lines.append(
        "  低于下限的轻端、高于上限的高温段均**无数据、不外推**；"
        "请通过补充离线试验点扩大适用范围。"
    )
    lines.append(f"- 总量核对式：{m['identity']}")
    lines.append("")

    lines.append("## 2. 馏分切点与产率")
    lines.append("| # | 馏分 | 初馏点 ℃ | 终馏点 ℃ | 体积产率 % | 质量产率 % | 标记 |")
    lines.append("|---|------|---------:|---------:|----------:|----------:|------|")
    for c in result["cuts"]:
        flags = "、".join(_FLAG_ZH.get(f, f) for f in c["flags"]) or ""
        lines.append(
            f"| {c['index'] + 1} | {c['name']} | {c['start_temp_c']:g} | {c['end_temp_c']:g} | "
            f"{_fmt(c['volume_yield_pct'])} | {_fmt(c['mass_yield_pct'])} | {flags} |"
        )
    lines.append("")

    lines.append("## 3. 重叠、缺口与残余")
    if result["overlaps"]:
        lines.append("**重叠（同一温度段被重复计入产率）：**")
        for o in result["overlaps"]:
            lines.append(
                f"- {o['cut_a_name']} × {o['cut_b_name']}："
                f"{o['from_temp_c']:g}~{o['to_temp_c']:g} ℃"
                f"（宽 {o['width_c']:g} ℃，{_fmt(o.get('volume_pct'))} 体积%，"
                f"{_fmt(o.get('mass_pct'))} 质量%）"
                + ("（部分在实测范围外）" if o["partial_outside_range"] else "")
                + ("（完全在实测范围外）" if o["fully_outside_range"] else "")
            )
    else:
        lines.append("**重叠：** 无。")
    lines.append("")
    if result["gaps"]:
        lines.append("**缺口（温度段可蒸出但未被馏分覆盖）：**")
        kind_zh = {"front": "前缺口（低于首切点）", "inter": "中间缺口", "tail": "尾缺口（高于末切点）"}
        for g in result["gaps"]:
            lines.append(
                f"- {kind_zh[g['kind']]}：{g['from_temp_c']:g}~{g['to_temp_c']:g} ℃"
                f"（宽 {g['width_c']:g} ℃，{_fmt(g.get('volume_pct'))} 体积%，"
                f"{_fmt(g.get('mass_pct'))} 质量%）"
            )
    lines.append("")
    lines.append("**总量核对（体积基准，进料=100%）：**")
    lines.append("")
    lines.append("| 项目 | 体积 % | 质量 % |")
    lines.append("|------|------:|------:|")
    rows = [
        ("馏分名义产率之和（含重叠）", totals["nominal_yield_pct"], _m(totals, "nominal_yield_pct")),
        ("馏分并集产率（去重叠）", totals["union_yield_pct"], _m(totals, "union_yield_pct")),
        ("其中：重叠量", totals["overlap_pct"], _m(totals, "overlap_pct")),
        ("前缺口", totals["front_gap_pct"], _m(totals, "front_gap_pct")),
        ("中间缺口", totals["inter_gap_pct"], _m(totals, "inter_gap_pct")),
        ("尾缺口", totals["tail_gap_pct"], _m(totals, "tail_gap_pct")),
        ("范围内未切出馏出液", totals["uncut_distillate_pct"], _m(totals, "uncut_distillate_pct")),
        ("起点前回收（低于实测起点）", totals["light_unassigned_pct"], _m(totals, "light_unassigned_pct")),
        ("未回收残渣毛额（100−R_max，含损失/不凝气）", totals["residue_bottoms_gross_pct"], _m(totals, "residue_bottoms_gross_pct")),
        ("其中：扣除声明损失后的残渣净额", totals["residue_bottoms_pct"], _m(totals, "residue_bottoms_pct")),
        ("残余合计（未切出+起点前+未回收毛额）", totals["residual_total_pct"], _m(totals, "residual_total_pct")),
        ("其中声明：试验损失（已含在未回收毛额内）", totals["loss_pct"], totals["loss_pct"]),
        ("闭合合计（应=100%）", totals["identity_sum_pct"], _m(totals, "identity_sum_pct")),
    ]
    for name, v, mv in rows:
        lines.append(f"| {name} | {_fmt(v)} | {_fmt(mv)} |")
    lines.append("")
    lines.append(
        f"体积基准闭合：{'通过（=100%）' if totals['identity_ok'] else '不通过：残差 ' + str(totals['identity_residual_pct']) + '%'}"
    )
    mb = totals.get("mass")
    if mb and mb.get("identity_note"):
        lines.append(f"质量基准说明：{mb['identity_note']}")
    lines.append("")

    if result["issues"]:
        lines.append("## 4. 数据与切点提示（请核实）")
        for it in result["issues"]:
            icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(it["severity"], "•")
            lines.append(f"- {icon} [{it['code']}] {it['message']}")
        lines.append("")

    lines.append("---")
    lines.append(
        "> 本方案仅基于给定的离线蒸馏试验数据，用于培训与产率核对，"
        "不用于控制真实装置。温度范围外不存在的高温数据一律未外推。"
    )
    return "\n".join(lines)


_FLAG_ZH = {
    "out_of_range": "切点越界",
    "clipped_to_range": "已按实测范围截断",
    "zero_width": "零宽（相邻切点相等）",
    "inverted": "区间反向",
}


def _m(totals: dict, key: str):
    mb = totals.get("mass")
    return mb.get(key) if mb else None


def build_json_payload(exp: dict, plan: dict, result: dict) -> dict:
    return {
        "exported_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope_notice": (
            "仅基于给定离线试验数据，不控制真实装置；"
            "适用温度范围外不外推，不存在的高温数据未做任何假设。"
        ),
        "experiment": exp,
        "plan": plan,
        "result": result,
    }


def to_json(exp: dict, plan: dict, result: dict) -> str:
    return json.dumps(
        build_json_payload(exp, plan, result), ensure_ascii=False, indent=2, allow_nan=False
    )
