"""API 冒烟测试：使用 conftest 配置的临时 SQLite。"""
from __future__ import annotations


def test_seed_and_evaluate_and_export(client):
    r = client.post("/api/seed")
    assert r.status_code == 200, r.text
    assert r.json()["loaded_experiments"] == 3

    # 重复导入应被拒绝
    assert client.post("/api/seed").status_code == 409

    exps = client.get("/api/experiments").json()
    assert len(exps) == 3

    # 示例C：曲线下降 => 曲线采样带硬错误
    cs = client.get(f"/api/experiments/{exps[2]['id']}/curve/sample").json()
    assert cs["has_blocking_errors"]
    assert any(i["code"] == "curve_decreases" for i in cs["issues"])

    # 示例A：缺端点 + 越界切点
    plan = {
        "name": "t",
        "basis": "volume",
        "loss_pct": 1.5,
        "cuts": [
            {"name": "f1", "start_temp_c": 55, "end_temp_c": 185},
            {"name": "f2", "start_temp_c": 185, "end_temp_c": 370},
        ],
    }
    res = client.post(f"/api/experiments/{exps[0]['id']}/evaluate", json=plan).json()
    assert res["applicable_range"]["extrapolation"] == "none"
    assert any(i["code"] == "missing_high_endpoint" for i in res["issues"])
    assert any(i["code"] == "out_of_range" for i in res["issues"])
    assert res["totals"]["identity_ok"]

    # 保存后导出 Markdown / JSON
    saved = client.post(f"/api/experiments/{exps[0]['id']}/plans", json=plan).json()
    pid = saved["id"]
    md = client.get(f"/api/plans/{pid}/export?format=markdown")
    assert md.status_code == 200 and "PCHIP" in md.text and "适用" in md.text
    js = client.get(f"/api/plans/{pid}/export?format=json")
    assert js.status_code == 200
    assert js.json()["result"]["method"]["interpolation"]


def test_create_experiment_rejects_decreasing_curve(client):
    payload = {
        "name": "bad",
        "feed_density_g_cm3": 0.8,
        "points": [
            {"temp_c": 10, "recovered_pct": 0},
            {"temp_c": 50, "recovered_pct": 30},
            {"temp_c": 90, "recovered_pct": 20},
        ],
    }
    r = client.post("/api/experiments", json=payload)
    assert r.status_code == 422
    assert any(i["code"] == "curve_decreases" for i in r.json()["detail"]["issues"])


def test_example_b_mass_basis_zero_width_and_mass_identity(client):
    exps = client.get("/api/experiments").json()
    bid = exps[1]["id"]
    plan = {
        "name": "b-live",
        "basis": "mass",
        "loss_pct": 0,
        "cuts": [
            {"name": "轻", "start_temp_c": 30, "end_temp_c": 185},
            {"name": "零宽", "start_temp_c": 185, "end_temp_c": 185},
            {"name": "重", "start_temp_c": 185, "end_temp_c": 600},
        ],
    }
    r = client.post(f"/api/experiments/{bid}/evaluate", json=plan).json()
    # 零宽馏分产率 0，不产生重叠/缺口
    assert r["cuts"][1]["volume_yield_pct"] == 0
    assert r["cuts"][1]["mass_yield_pct"] == 0
    assert "zero_width" in r["cuts"][1]["flags"]
    assert r["overlaps"] == []
    assert [g for g in r["gaps"] if g["volume_pct"]] == []
    # 质量与体积产率不同（密度随馏分变化），但两者各自闭合
    assert r["cuts"][0]["mass_yield_pct"] != r["cuts"][0]["volume_yield_pct"]
    assert r["totals"]["identity_ok"]
    assert r["totals"]["mass"]["identity_ok"]


def test_export_json_declares_scope_and_range(client):
    exps = client.get("/api/experiments").json()
    plan = {
        "name": "a-live", "basis": "volume", "loss_pct": 1.5,
        "cuts": [{"name": "f", "start_temp_c": 55, "end_temp_c": 370}],
    }
    saved = client.post(f"/api/experiments/{exps[0]['id']}/plans", json=plan).json()
    js = client.get(f"/api/plans/{saved['id']}/export?format=json").json()
    assert "不外推" in js["scope_notice"]
    assert js["result"]["applicable_range"]["extrapolation"] == "none"
    assert "PCHIP" in js["result"]["method"]["interpolation"]
