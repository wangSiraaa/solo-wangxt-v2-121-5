"""核心计算规则测试（不依赖数据库/网络）。"""
from __future__ import annotations

import math

import pytest

from app.curve import prepare_curve
from app.density import prepare_density
from app.planning import evaluate_plan


def pc(ts, rs):
    return prepare_curve([{"temp_c": t, "recovered_pct": r} for t, r in zip(ts, rs)])


def dens(rows):
    return prepare_density(
        [{"temp_c": t, "density_g_cm3": d} for t, d in rows]
    )


# ---------- 曲线校验 ----------
def test_pchip_monotone_no_overshoot():
    c = pc([0, 50, 100, 150, 200], [0, 10, 40, 40, 100])
    # 平台段（100~150℃）不应产生样条过冲
    vals = [float(c.pchip(t)) for t in range(0, 201, 5)]
    assert all(vals[i] <= vals[i + 1] + 1e-9 for i in range(len(vals) - 1))
    assert max(vals) <= 100 + 1e-9


def test_decreasing_curve_is_blocking_error():
    c = pc([10, 20, 30, 40], [0, 20, 15, 40])
    assert any(i.code == "curve_decreases" and i.severity == "error" for i in c.issues)
    assert c.hard_errors


def test_duplicate_temp_conflict_errors():
    c = pc([10, 20, 20, 30], [0, 20, 25, 40])
    assert any(i.code == "duplicate_temp_conflict" for i in c.issues)


def test_missing_endpoints_warn_but_not_block():
    c = pc([50, 100, 200, 300], [5, 15, 60, 85])
    codes = {i.code for i in c.issues}
    assert "missing_low_endpoint" in codes
    assert "missing_high_endpoint" in codes
    assert not c.hard_errors


def test_recovery_over_100_blocks():
    c = pc([10, 50, 100], [0, 60, 103])
    assert any(i.code == "recovery_over_100" for i in c.hard_errors)


def test_recovery_total_abnormally_low_warns():
    c = pc([10, 50, 100], [0, 30, 55])
    assert any(i.code == "recovery_total_low" and i.severity == "warning" for i in c.issues)


# ---------- 不外推 ----------
def test_no_extrapolation_below_and_above():
    c = pc([10, 50, 100], [0, 40, 90])
    v, iss = c.recovery_at(5)
    assert v is None and iss is not None and iss.code == "out_of_range"
    v, iss = c.recovery_at(120)
    assert v is None and iss.code == "out_of_range"
    v, iss = c.recovery_at(50)
    assert v == pytest.approx(40) and iss is None


# ---------- 密度换算 ----------
def test_mass_conversion_constant_density_equals_volume_pct():
    # 馏出液密度恒等于进料密度 => 质量% = 体积%
    pts = [(10, 0), (50, 25), (100, 60), (150, 100)]
    c = pc([p[0] for p in pts], [p[1] for p in pts])
    d = dens([(10, 0.8), (150, 0.8)])
    res = evaluate_plan(
        c,
        [{"name": "f", "start_temp_c": 10, "end_temp_c": 150}],
        loss_pct=0.0,
        basis="mass",
        density=d,
        feed_density=0.8,
        residue_density=0.8,
    )
    row = res["cuts"][0]
    assert row["volume_yield_pct"] == pytest.approx(100, abs=1e-6)
    assert row["mass_yield_pct"] == pytest.approx(100, abs=1e-6)


def test_heavier_distillate_mass_exceeds_volume():
    # 进料 0.8，馏分越重密度越高（0.7→0.9），全馏出段平均>0.8 => 质量%>体积%
    pts = [(10, 0), (50, 25), (100, 60), (150, 100)]
    c = pc([p[0] for p in pts], [p[1] for p in pts])
    d = dens([(10, 0.7), (50, 0.75), (100, 0.85), (150, 0.9)])
    res = evaluate_plan(
        c,
        [{"name": "f", "start_temp_c": 10, "end_temp_c": 150}],
        0.0,
        "mass",
        d,
        feed_density=0.8,
        residue_density=1.0,
    )
    row = res["cuts"][0]
    assert row["mass_yield_pct"] > row["volume_yield_pct"]


def test_density_gap_partial_count_and_missing_reported():
    pts = [(10, 0), (50, 30), (100, 70), (200, 100)]
    c = pc([p[0] for p in pts], [p[1] for p in pts])
    d = dens([(10, 0.7), (50, 0.75)])  # 只覆盖段中点 30℃（密度表上界 50℃）
    res = evaluate_plan(
        c, [{"name": "f", "start_temp_c": 10, "end_temp_c": 200}],
        0.0, "mass", d, feed_density=0.8, residue_density=1.0,
    )
    row = res["cuts"][0]
    # 仅首段（段中点 30℃，密度线性插值 (0.7+0.75)/2=0.725）30 个体积点计入；
    # 其余 70 个点缺密度，不猜
    assert row["mass_yield_pct"] == pytest.approx(30 * 0.725 / 0.8)
    assert any(i["code"] == "density_missing_segments" for i in res["issues"])
    msg = [i["message"] for i in res["issues"] if i["code"] == "density_missing_segments"][0]
    assert "70.00" in msg


def test_density_gap_fully_missing_returns_none():
    pts = [(10, 0), (50, 30), (100, 70), (200, 100)]
    c = pc([p[0] for p in pts], [p[1] for p in pts])
    d = dens([(10, 0.7), (50, 0.75)])
    # 只切高温段 => 全部段都缺密度
    res = evaluate_plan(
        c, [{"name": "f", "start_temp_c": 100, "end_temp_c": 200}],
        0.0, "mass", d, feed_density=0.8, residue_density=1.0,
    )
    assert res["cuts"][0]["mass_yield_pct"] is None


# ---------- 切割：连续 / 重叠 / 缺口 / 零宽 ----------
def _full_curve():
    return pc([10, 50, 100, 150, 200], [0, 25, 50, 75, 100])


def test_continuous_cuts_no_gap_no_overlap_and_identity_closes():
    c = _full_curve()
    cuts = [
        {"name": "a", "start_temp_c": 10, "end_temp_c": 100},
        {"name": "b", "start_temp_c": 100, "end_temp_c": 200},
    ]
    res = evaluate_plan(c, cuts, loss_pct=0.0)
    t = res["totals"]
    assert t["overlap_pct"] == 0
    assert t["inter_gap_pct"] == 0
    assert t["front_gap_pct"] == 0 and t["tail_gap_pct"] == 0
    assert [r["volume_yield_pct"] for r in res["cuts"]] == [50.0, 50.0]
    assert t["identity_ok"], t
    assert t["residual_total_pct"] == pytest.approx(0.0, abs=1e-9)


def test_overlap_detected_and_nominal_double_counts():
    c = _full_curve()
    cuts = [
        {"name": "a", "start_temp_c": 10, "end_temp_c": 120},
        {"name": "b", "start_temp_c": 100, "end_temp_c": 200},
    ]
    res = evaluate_plan(c, cuts, 0.0)
    assert len(res["overlaps"]) == 1
    ov = res["overlaps"][0]
    # 重叠区 100~120℃
    assert (ov["from_temp_c"], ov["to_temp_c"]) == (100, 120)
    t = res["totals"]
    assert t["nominal_yield_pct"] - t["union_yield_pct"] == pytest.approx(t["overlap_pct"])
    assert t["overlap_pct"] == pytest.approx(ov["volume_pct"])
    # 并集仍覆盖 10~200℃ => 闭合恒等式仍成立
    assert t["identity_ok"]


def test_gap_detected_and_counts_as_uncovered():
    c = _full_curve()
    cuts = [
        {"name": "a", "start_temp_c": 10, "end_temp_c": 80},
        {"name": "b", "start_temp_c": 120, "end_temp_c": 200},
    ]
    res = evaluate_plan(c, cuts, 0.0)
    g = [g for g in res["gaps"] if g["kind"] == "inter"]
    assert len(g) == 1 and (g[0]["from_temp_c"], g[0]["to_temp_c"]) == (80, 120)
    t = res["totals"]
    assert t["inter_gap_pct"] == pytest.approx(g[0]["volume_pct"])
    assert g[0]["volume_pct"] > 0
    # 缺口部分计入“范围内未切出馏出液”，闭合仍为 100%
    assert t["identity_ok"]
    assert t["uncut_distillate_pct"] == pytest.approx(g[0]["volume_pct"])


def test_zero_width_cut_from_equal_adjacent_temperatures():
    c = _full_curve()
    cuts = [
        {"name": "a", "start_temp_c": 10, "end_temp_c": 100},
        {"name": "degenerate", "start_temp_c": 100, "end_temp_c": 100},
        {"name": "b", "start_temp_c": 100, "end_temp_c": 200},
    ]
    res = evaluate_plan(c, cuts, 0.0)
    mid = res["cuts"][1]
    assert "zero_width" in mid["flags"]
    assert mid["volume_yield_pct"] == 0.0
    assert any(i["code"] == "cut_zero_width" for i in res["issues"])
    assert res["totals"]["identity_ok"]


def test_out_of_range_cut_is_clipped_not_extrapolated():
    c = _full_curve()  # 10~200℃
    cuts = [{"name": "a", "start_temp_c": 150, "end_temp_c": 260}]
    res = evaluate_plan(c, cuts, 0.0)
    row = res["cuts"][0]
    assert "out_of_range" in row["flags"] and "clipped_to_range" in row["flags"]
    # 150~200℃ => 25 个百分点；200℃ 以上不外推
    assert row["volume_yield_pct"] == pytest.approx(25.0, abs=1e-9)
    assert any(i["code"] == "out_of_range" for i in res["issues"])
    # 尾缺口完全落在实测范围外 => 无体积、状态 outside
    tail = [g for g in res["gaps"] if g["kind"] == "tail"][0]
    assert tail["fully_outside_range"]


def test_inverted_cut_skipped_but_identity_still_closes():
    c = _full_curve()
    cuts = [
        {"name": "a", "start_temp_c": 150, "end_temp_c": 50},
        {"name": "b", "start_temp_c": 10, "end_temp_c": 200},
    ]
    res = evaluate_plan(c, cuts, 0.0)
    assert "inverted" in res["cuts"][0]["flags"]
    assert res["cuts"][0]["volume_yield_pct"] is None
    assert any(i["code"] == "cut_inverted" and i["severity"] == "error" for i in res["issues"])
    assert res["totals"]["identity_ok"]


# ---------- 损失 / 残渣 / 总量 ----------
def test_loss_and_bottoms_close_to_100():
    # 曲线只到 80% 回收（300℃）：未回收残渣毛额 = 20%（含损失/不凝气）；
    # 用户声明损失 2% => 残渣净额 18%。恒等式用毛额，恒为 100%。
    c = pc([10, 100, 200, 300], [0, 30, 60, 80])
    cuts = [{"name": "a", "start_temp_c": 10, "end_temp_c": 300}]
    res = evaluate_plan(c, cuts, loss_pct=2.0)
    t = res["totals"]
    assert t["residue_bottoms_pct"] == pytest.approx(18.0)
    assert t["residue_bottoms_gross_pct"] == pytest.approx(20.0)
    assert t["identity_ok"]
    assert t["identity_sum_pct"] == pytest.approx(100.0, abs=1e-9)


def test_excessive_loss_is_error():
    c = pc([10, 100], [0, 90])
    res = evaluate_plan(c, [{"name": "a", "start_temp_c": 10, "end_temp_c": 100}], 20.0)
    # 未回收只有 10%，声明 20% 损失 => 物料平衡容纳不下（此例 90% 回收为 warning 级）
    assert any(i["code"] == "loss_exceeds_unrecovered" for i in res["issues"])
    # 全回收曲线再给损失 => 硬错误
    c2 = pc([10, 100], [0, 100])
    res2 = evaluate_plan(c2, [{"name": "a", "start_temp_c": 10, "end_temp_c": 100}], 1.0)
    err = [i for i in res2["issues"] if i["code"] == "loss_exceeds_unrecovered"]
    assert err and err[0]["severity"] == "error"


def test_mass_identity_closes_with_full_data_and_densities():
    # 全部 100 体积单位均蒸出（R 从 0 到 100%）、损失 0%。
    # 取密度关于回收体积百分比线性 0.7→0.9，使馏出液平均密度恰为 0.8 = 进料密度，
    # 则 100 体积单位进料的质量 = 全部馏出液质量，质量基准严格闭合到 100%。
    pts = [(10, 0), (50, 25), (100, 60), (150, 100)]
    c = pc([p[0] for p in pts], [p[1] for p in pts])
    # 段中点温度：30,75,125℃，段体积增量 25/35/40；取段密度
    # 0.70/0.80/0.8625，使体积加权平均密度恰为 0.8 = 进料密度，
    # 则全部馏出液质量 = 100 体积单位进料的质量，质量基准严格闭合到 100%。
    d_rows = [(30, 0.70), (75, 0.80), (125, 0.8625)]
    feed = 0.8
    d = dens(d_rows)
    res = evaluate_plan(
        c, [{"name": "a", "start_temp_c": 10, "end_temp_c": 150}],
        0.0, "mass", d, feed_density=feed, residue_density=1.0,
    )
    mb = res["totals"]["mass"]
    assert mb["identity_ok"], mb
    assert mb["identity_sum_pct"] == pytest.approx(100.0, abs=1e-6)


def test_export_range_and_no_extrapolation_declared():
    c = pc([30, 100, 200], [5, 40, 90])
    res = evaluate_plan(c, [{"name": "a", "start_temp_c": 30, "end_temp_c": 200}], 1.0)
    assert res["applicable_range"]["temp_c"] == [30, 200]
    assert res["applicable_range"]["extrapolation"] == "none"
    assert "PCHIP" in res["method"]["interpolation"]
    assert "不" in res["method"]["extrapolation"]


def test_blocking_curve_still_returns_result_flagged():
    c = pc([10, 20, 30], [0, 30, 20])
    res = evaluate_plan(c, [{"name": "a", "start_temp_c": 10, "end_temp_c": 30}], 0.0)
    assert res["has_blocking_errors"]
