"""候选方案批量预览（/api/experiments/{id}/candidates/preview）测试。

覆盖验收点：
* 混合有效 / 越界 / 切点重叠的候选能逐项说明；
* 单项无效只标记该候选，不影响其他候选；
* 选择有效项后保存结果与单独评估一致；
* 错误格式返回 422 且不创建方案；
* 预览不修改原始试验数据。
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module", autouse=True)
def seeded(client):
    r = client.post("/api/seed")
    assert r.status_code in (200, 409), r.text


@pytest.fixture(scope="module")
def exp_a(client):
    """示例A：实测范围 55~340 ℃、缺端点，适合演示越界。"""
    exps = client.get("/api/experiments").json()
    return next(e for e in exps if e["name"].startswith("示例A"))


def _plans_count(client, exp_id: int) -> int:
    return len(client.get(f"/api/experiments/{exp_id}").json()["plans"])


def test_json_batch_mixed_candidates_itemized(client, exp_a):
    """有效 + 越界 + 重叠 + 无效 四类候选同批：逐项给出结论，互不吞没。"""
    payload = {
        "candidates": [
            {
                "name": "连续三段",
                "basis": "volume",
                "loss_pct": 1.0,
                "cuts": [
                    {"name": "轻", "start_temp_c": 55, "end_temp_c": 150},
                    {"name": "中", "start_temp_c": 150, "end_temp_c": 250},
                    {"name": "重", "start_temp_c": 250, "end_temp_c": 340},
                ],
            },
            {
                "name": "末段越界",
                "cuts": [
                    {"name": "轻", "start_temp_c": 55, "end_temp_c": 185},
                    {"name": "重", "start_temp_c": 185, "end_temp_c": 370},
                ],
            },
            {
                "name": "两段重叠",
                "cuts": [
                    {"name": "石脑油", "start_temp_c": 55, "end_temp_c": 200},
                    {"name": "煤油", "start_temp_c": 150, "end_temp_c": 300},
                ],
            },
            {"name": "坏候选", "cuts": []},  # cuts 为空 => 仅该项无效
        ]
    }
    before = _plans_count(client, exp_a["id"])
    r = client.post(f"/api/experiments/{exp_a['id']}/candidates/preview", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 4 and body["valid_count"] == 3
    cands = body["candidates"]

    # ① 有效候选：有产率/平衡/问题摘要，闭合成立
    ok = cands[0]
    assert ok["valid"] and ok["error"] is None
    s = ok["summary"]
    assert s["union_yield_pct"] > 0
    assert s["identity_ok"] and abs(s["identity_residual_pct"]) < 1e-6
    assert s["overlap_pct"] == 0
    assert s["n_issues"] == s["n_errors"] + s["n_warnings"] + s["n_infos"]
    assert ok["result"]["cuts"][0]["volume_yield_pct"] is not None

    # ② 越界候选：逐项说明 —— 370 ℃ 超实测终点 340 ℃，截断且不外推
    oor = cands[1]
    assert oor["valid"]  # 越界是数据提示，不是格式无效
    assert "out_of_range" in oor["result"]["cuts"][1]["flags"]
    assert "clipped_to_range" in oor["result"]["cuts"][1]["flags"]
    assert any(i["code"] == "out_of_range" for i in oor["result"]["issues"])
    assert oor["summary"]["n_warnings"] >= 1
    assert oor["result"]["applicable_range"]["extrapolation"] == "none"

    # ③ 重叠候选：重叠量 > 0 且指明馏分对
    ov = cands[2]
    assert ov["valid"]
    assert ov["summary"]["overlap_pct"] > 0
    assert len(ov["result"]["overlaps"]) == 1
    o = ov["result"]["overlaps"][0]
    assert o["from_temp_c"] == 150 and o["to_temp_c"] == 200
    assert ov["summary"]["nominal_yield_pct"] > ov["summary"]["union_yield_pct"]

    # ④ 无效候选：只标记自己，带原因
    bad = cands[3]
    assert not bad["valid"] and bad["error"]
    assert bad["plan"] is None and bad["summary"] is None and bad["result"] is None

    # 预览不落库：方案数不变
    assert _plans_count(client, exp_a["id"]) == before


def test_preview_does_not_touch_experiment_data(client, exp_a):
    before = client.get(f"/api/experiments/{exp_a['id']}").json()
    payload = {"candidates": [{"name": "x", "cuts": [{"name": "f", "start_temp_c": 55, "end_temp_c": 100}]}]}
    r = client.post(f"/api/experiments/{exp_a['id']}/candidates/preview", json=payload)
    assert r.status_code == 200
    after = client.get(f"/api/experiments/{exp_a['id']}").json()
    assert before["points"] == after["points"]
    assert before["density_rows"] == after["density_rows"]
    assert before["feed_density_g_cm3"] == after["feed_density_g_cm3"]


def test_saved_result_matches_preview(client, exp_a):
    """采用有效候选并保存：结果与批量预览（= 单独评估）完全一致。"""
    cand = {
        "name": "一致性核对",
        "basis": "volume",
        "loss_pct": 1.5,
        "cuts": [
            {"name": "f1", "start_temp_c": 55, "end_temp_c": 185},
            {"name": "f2", "start_temp_c": 185, "end_temp_c": 340},
        ],
    }
    body = client.post(
        f"/api/experiments/{exp_a['id']}/candidates/preview",
        json={"candidates": [cand]},
    ).json()
    previewed = body["candidates"][0]
    assert previewed["valid"]

    # 与单独 /evaluate 一致
    ev = client.post(f"/api/experiments/{exp_a['id']}/evaluate", json=cand).json()
    assert previewed["result"] == ev

    # 与保存后的结果快照一致
    saved = client.post(f"/api/experiments/{exp_a['id']}/plans", json=cand)
    assert saved.status_code == 201, saved.text
    assert saved.json()["result"] == previewed["result"]


def test_csv_batch_with_chinese_headers_and_row_isolation(client, exp_a):
    """CSV（中文表头、合并单元格式留空候选名）+ 行级错误只影响所属候选。"""
    csv_text = (
        "候选,馏分,初馏点,终馏点,损失\n"
        "方案甲,轻馏分,55,180,1.0\n"
        ",重馏分,180,340,1.0\n"          # 候选名留空 => 继承上一行
        "方案乙,轻馏分,55,abc,\n"          # 非数字 => 仅方案乙无效
        "方案乙,重馏分,180,340,\n"
        "方案丙,全馏分,55,370,0.5\n"       # 越界 => 有效但带提示
    )
    r = client.post(
        f"/api/experiments/{exp_a['id']}/candidates/preview",
        content=csv_text.encode("utf-8"),
        headers={"Content-Type": "text/csv; charset=utf-8"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3 and body["valid_count"] == 2
    by_name = {c["name"]: c for c in body["candidates"]}

    jia = by_name["方案甲"]
    assert jia["valid"]
    assert len(jia["plan"]["cuts"]) == 2
    assert jia["plan"]["loss_pct"] == 1.0

    yi = by_name["方案乙"]
    assert not yi["valid"] and "abc" in yi["error"]

    bing = by_name["方案丙"]
    assert bing["valid"]
    assert "out_of_range" in bing["result"]["cuts"][0]["flags"]


def test_malformed_inputs_return_422_and_create_nothing(client, exp_a):
    before = _plans_count(client, exp_a["id"])

    # CSV 缺必需列
    r = client.post(
        f"/api/experiments/{exp_a['id']}/candidates/preview",
        content="候选,馏分\n甲,轻\n".encode("utf-8"),
        headers={"Content-Type": "text/csv"},
    )
    assert r.status_code == 422

    # 空 CSV
    r = client.post(
        f"/api/experiments/{exp_a['id']}/candidates/preview",
        content=b"   \n",
        headers={"Content-Type": "text/csv"},
    )
    assert r.status_code == 422

    # JSON 结构不对（不是数组也没有 candidates 字段）
    r = client.post(
        f"/api/experiments/{exp_a['id']}/candidates/preview",
        json={"foo": 1},
    )
    assert r.status_code == 422

    # 空候选数组
    r = client.post(
        f"/api/experiments/{exp_a['id']}/candidates/preview", json={"candidates": []}
    )
    assert r.status_code == 422

    # 非法 JSON 体
    r = client.post(
        f"/api/experiments/{exp_a['id']}/candidates/preview",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422

    assert _plans_count(client, exp_a["id"]) == before


def test_json_bare_list_and_invalid_item_types(client, exp_a):
    """直接 POST 数组也可；非对象项、缺字段项各自标记无效，其余照常。"""
    r = client.post(
        f"/api/experiments/{exp_a['id']}/candidates/preview",
        json=[
            "不是对象",
            {"name": "缺cuts"},
            {"name": "好候选", "cuts": [{"name": "f", "start_temp_c": 55, "end_temp_c": 120}]},
        ],
    )
    assert r.status_code == 200, r.text
    cands = r.json()["candidates"]
    assert [c["valid"] for c in cands] == [False, False, True]
    assert cands[0]["error"]
    assert cands[2]["summary"]["union_yield_pct"] > 0
