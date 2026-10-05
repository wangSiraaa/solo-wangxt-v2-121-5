"""候选方案批量预览（CSV/JSON）的 API 测试。"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module", autouse=True)
def _seed(client):
    client.post("/api/seed")


CSV_MIXED = """candidate,cut,start_temp_c,end_temp_c
有效-两馏分,轻馏分,30,185
有效-两馏分,重馏分,185,450
越界方案,高温馏分,200,700
重叠方案,甲,100,250
重叠方案,乙,200,350
温度非数字,坏馏分,abc,300
"""

JSON_MIXED = [
    {
        "name": "JSON-有效",
        "basis": "volume",
        "cuts": [
            {"name": "轻", "start_temp_c": 30, "end_temp_c": 200},
            {"name": "重", "start_temp_c": 200, "end_temp_c": 500},
        ],
    },
    {
        "name": "JSON-重叠+越界",
        "cuts": [
            {"name": "甲", "start_temp_c": 100, "end_temp_c": 400},
            {"name": "乙", "start_temp_c": 300, "end_temp_c": 650},
        ],
    },
    {"cuts": [{"name": "无名称候选", "start_temp_c": 50, "end_temp_c": 100}]},
    {"name": "JSON-空切点", "cuts": []},
]


def _b_id(client) -> int:
    exps = client.get("/api/experiments").json()
    return exps[1]["id"]  # 示例B：30~600 ℃ 完整曲线


def test_csv_preview_mixed_validity(client):
    bid = _b_id(client)
    r = client.post(
        f"/api/experiments/{bid}/candidates/preview",
        json={"content": CSV_MIXED, "format": "csv"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 4
    assert body["valid_count"] == 3
    assert body["invalid_count"] == 1

    by_name = {c["name"]: c for c in body["candidates"]}
    good = by_name["有效-两馏分"]
    assert good["valid"] and good["errors"] == []
    assert good["summary"]["issue_count"] == 0
    assert good["summary"]["overlap_count"] == 0
    assert good["summary"]["identity_ok"]
    assert good["summary"]["volume_yield_pct"] > 0

    oor = by_name["越界方案"]
    assert oor["valid"]  # 越界是评估结果，不是结构无效
    assert any(i["code"] == "out_of_range" for i in oor["result"]["issues"])
    assert any("out_of_range" in f for f in ["out_of_range"])
    assert oor["summary"]["warning_count"] >= 1

    ov = by_name["重叠方案"]
    assert ov["valid"]
    assert ov["summary"]["overlap_count"] == 1
    assert ov["result"]["overlaps"][0]["volume_pct"] > 0

    bad = by_name["温度非数字"]
    assert not bad["valid"]
    assert bad["result"] is None and bad["summary"] is None
    assert any("初馏点不是数字" in e for e in bad["errors"])


def test_json_preview_mixed_validity(client):
    bid = _b_id(client)
    r = client.post(
        f"/api/experiments/{bid}/candidates/preview",
        json={"content": __import__("json").dumps(JSON_MIXED), "format": "json"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 4 and body["valid_count"] == 2 and body["invalid_count"] == 2

    names = [c["name"] for c in body["candidates"]]
    assert "JSON-有效" in names and "JSON-重叠+越界" in names
    by_name = {c["name"]: c for c in body["candidates"]}
    ov = by_name["JSON-重叠+越界"]
    assert ov["summary"]["overlap_count"] == 1
    assert any(i["code"] == "out_of_range" for i in ov["result"]["issues"])

    for c in body["candidates"]:
        if not c["valid"]:
            assert c["result"] is None
            assert c["errors"]


def test_preview_does_not_create_plans_or_mutate_experiment(client):
    bid = _b_id(client)
    exp_before = client.get(f"/api/experiments/{bid}").json()
    points_before = exp_before["points"]
    plans_before = exp_before["plans"]

    r = client.post(
        f"/api/experiments/{bid}/candidates/preview",
        json={"content": CSV_MIXED, "format": "csv"},
    )
    assert r.status_code == 200

    exp_after = client.get(f"/api/experiments/{bid}").json()
    assert exp_after["points"] == points_before
    assert len(exp_after["plans"]) == len(plans_before)


def test_valid_candidate_result_equals_standalone_evaluate(client):
    bid = _b_id(client)
    plan = {
        "name": "JSON-有效",
        "basis": "volume",
        "loss_pct": 0,
        "cuts": [
            {"name": "轻", "start_temp_c": 30, "end_temp_c": 200},
            {"name": "重", "start_temp_c": 200, "end_temp_c": 500},
        ],
    }
    prev = client.post(
        f"/api/experiments/{bid}/candidates/preview",
        json={"content": __import__("json").dumps([plan]), "format": "json"},
    ).json()
    item = prev["candidates"][0]
    assert item["valid"]
    single = client.post(f"/api/experiments/{bid}/evaluate", json=plan).json()
    assert item["result"] == single
    # 用同一 payload 保存，结果与预览一致
    saved = client.post(f"/api/experiments/{bid}/plans", json=plan).json()
    assert saved["result"] == single


def test_malformed_inputs_return_422_and_create_nothing(client):
    bid = _b_id(client)
    plans_before = len(client.get(f"/api/experiments/{bid}").json()["plans"])

    for bad in (
        "not a json at all {{{",       # 非法 JSON
        "[1, 2]",                        # 顶层不是候选对象数组（单项错误，整体合法 → 200，下面单独断言）
    ):
        r = client.post(
            f"/api/experiments/{bid}/candidates/preview",
            json={"content": bad, "format": "json"},
        )
        if bad == "not a json at all {{{":
            assert r.status_code == 422
            assert r.json()["detail"]["errors"]
        else:
            # 单项无效只标记，不吞掉（返回 200，每项 invalid）
            assert r.status_code == 200
            assert all(not c["valid"] for c in r.json()["candidates"])

    # CSV 缺必需列
    r = client.post(
        f"/api/experiments/{bid}/candidates/preview",
        json={"content": "candidate,cut,start_temp_c\n甲,a,30\n", "format": "csv"},
    )
    assert r.status_code == 422
    assert "终馏点" in r.json()["detail"]["message"]

    # 空内容
    r = client.post(
        f"/api/experiments/{bid}/candidates/preview",
        json={"content": "   ", "format": "auto"},
    )
    assert r.status_code == 422

    # JSON 顶层结构错误
    r = client.post(
        f"/api/experiments/{bid}/candidates/preview",
        json={"content": '{"wrong": 1}', "format": "json"},
    )
    assert r.status_code == 422

    plans_after = len(client.get(f"/api/experiments/{bid}").json()["plans"])
    assert plans_after == plans_before


def test_csv_with_basis_and_loss_columns(client):
    bid = _b_id(client)
    csv_text = (
        "candidate,cut,start_temp_c,end_temp_c,basis,loss_pct\n"
        "质量方案,轻,30,185,mass,0.5\n"
        "质量方案,重,185,600,mass,0.5\n"
    )
    r = client.post(
        f"/api/experiments/{bid}/candidates/preview",
        json={"content": csv_text, "format": "csv"},
    )
    assert r.status_code == 200, r.text
    item = r.json()["candidates"][0]
    assert item["valid"]
    assert item["basis"] == "mass"
    assert item["loss_pct"] == 0.5
    assert item["result"]["basis"] == "mass"
    assert item["summary"]["mass_yield_pct"] is not None


def test_auto_detect_and_chinese_headers(client):
    bid = _b_id(client)
    csv_text = (
        "候选名称,馏分名称,初馏点,终馏点\n"
        "中文表头方案,轻,30,200\n"
        "中文表头方案,重,200,400\n"
    )
    r = client.post(
        f"/api/experiments/{bid}/candidates/preview",
        json={"content": csv_text},  # format 默认 auto，首字符非 {[ => CSV
    )
    assert r.status_code == 200, r.text
    item = r.json()["candidates"][0]
    assert item["valid"] and item["cut_count"] == 2


def test_preview_unknown_experiment_404(client):
    r = client.post(
        "/api/experiments/9999/candidates/preview",
        json={"content": "[]", "format": "json"},
    )
    assert r.status_code == 404
