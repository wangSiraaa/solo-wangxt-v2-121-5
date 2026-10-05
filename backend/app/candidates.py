"""候选切点方案批量预览的输入解析（CSV / JSON）。

用途：老师一次拿到多套候选切点，先在**选定的同一试验**上逐项复用
``planning.evaluate_plan`` 的现有评估规则做比较，再决定保存哪一套。
本模块只负责把文本解析成结构化候选，**不碰数据库、不改原始试验数据**。

容错约定
--------
* 文件级格式错误（不是合法 CSV/JSON、缺少必需列、内容为空）抛
  :class:`CandidateInputError`，由 API 层返回 422，不创建任何方案；
* 单项错误（某候选缺名称、某个温度不是数字、切点列表为空……）只标记
  该候选（``errors`` 非空），不影响其他候选继续进入评估；
* 越界、重叠、零宽、反向等属于**业务评估结果**而非解析错误，
  解析层一律放行，交给现有评估规则逐项说明。

CSV 格式（首行表头，列名兼容常见别名；多余列忽略）::

    candidate,cut,start_temp_c,end_temp_c,basis,loss_pct
    方案甲,轻馏分,60,180,volume,0
    方案甲,重馏分,180,320,,
    方案乙,宽馏分,50,350,mass,1.2

同一候选名称的多行聚合为一个候选；``basis``/``loss_pct`` 为候选级
可选项，留空取请求默认值，同一候选内取值不一致记为该候选错误。

JSON 格式（数组或 ``{"candidates": [...]}``）::

    [
      {"name": "方案甲", "cuts": [
          {"name": "轻馏分", "start_temp_c": 60, "end_temp_c": 180}],
       "basis": "volume", "loss_pct": 0}
    ]
"""
from __future__ import annotations

import csv
import io
import json
import math
from typing import Any, Literal

CandidateFormat = Literal["csv", "json", "auto"]

# 表头别名（比较前先做 _norm_header 归一化）
_CANDIDATE_KEYS = {
    "candidate", "candidatename", "plan", "planname",
    "候选", "候选名称", "方案", "方案名称",
}
_CUT_KEYS = {"cut", "cutname", "name", "馏分", "馏分名称", "名称"}
_START_KEYS = {"starttempc", "starttemp", "start", "初馏点", "初馏点温度", "起点温度", "起点"}
_END_KEYS = {"endtempc", "endtemp", "end", "终馏点", "终馏点温度", "终点温度", "终点"}
_BASIS_KEYS = {"basis", "口径", "基准", "产率口径", "产率基准"}
_LOSS_KEYS = {"losspct", "loss", "损失", "试验损失", "损失率", "损失pct"}


class CandidateInputError(ValueError):
    """文件级格式错误：整份输入无法解析为候选列表。"""

    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.errors = errors or [message]


def _norm_header(h: str) -> str:
    return (
        h.strip()
        .lower()
        .replace(" ", "")
        .replace("_", "")
        .replace("℃", "")
        .replace("°c", "")
    )


def _as_finite_float(v: Any) -> float | None:
    """只接受数字（JSON 的 bool 不算）与可解析字符串；非有限值拒绝。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
    elif isinstance(v, str) and v.strip():
        try:
            x = float(v.strip())
        except ValueError:
            return None
    else:
        return None
    return x if math.isfinite(x) else None


def parse_candidate_bundle(
    content: str,
    fmt: CandidateFormat = "auto",
    default_basis: str = "volume",
    default_loss_pct: float = 0.0,
) -> list[dict]:
    """把 CSV/JSON 文本解析为候选 dict 列表。

    返回每项::

        {"name": str | None, "basis": str, "loss_pct": float,
         "cuts": list[dict], "errors": list[str]}

    ``errors`` 非空表示该单项无效（API 层只标记、不评估）；
    整份输入无法解析时抛 :class:`CandidateInputError`。
    """
    if not isinstance(content, str) or not content.strip():
        raise CandidateInputError("候选内容为空，请粘贴 CSV/JSON 文本或选择文件")

    text = content.lstrip("﻿").strip()
    if fmt == "auto":
        fmt = "json" if text[0] in "{[" else "csv"

    if fmt == "json":
        return _parse_json(text, default_basis, default_loss_pct)
    if fmt == "csv":
        return _parse_csv(text, default_basis, default_loss_pct)
    raise CandidateInputError(f"不支持的格式：{fmt}")


# ---------------- JSON ----------------
def _parse_json(text: str, default_basis: str, default_loss_pct: float) -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise CandidateInputError(
            "JSON 解析失败，请检查括号、逗号与引号（也可把格式显式切到 CSV）",
            [f"JSON 第 {e.lineno} 行第 {e.colno} 列：{e.msg}"],
        ) from e

    if isinstance(data, dict) and isinstance(data.get("candidates"), list):
        items = data["candidates"]
    elif isinstance(data, list):
        items = data
    else:
        raise CandidateInputError(
            "JSON 顶层必须是候选数组，或 {\"candidates\": [...]} 对象"
        )
    if not items:
        raise CandidateInputError("候选列表为空")

    out: list[dict] = []
    for i, item in enumerate(items):
        entry = {
            "name": None,
            "basis": default_basis,
            "loss_pct": default_loss_pct,
            "cuts": [],
            "errors": [],
        }
        where = f"第 {i + 1} 项"
        if not isinstance(item, dict):
            entry["errors"].append(f"{where}：候选必须是对象，实际为 {type(item).__name__}")
            out.append(entry)
            continue

        name = item.get("name", item.get("candidate"))
        if isinstance(name, str) and name.strip():
            entry["name"] = name.strip()
        else:
            entry["errors"].append(f"{where}：缺少候选名称（name）或名称为空")

        basis = item.get("basis")
        if basis is not None:
            if basis in ("volume", "mass"):
                entry["basis"] = basis
            else:
                entry["errors"].append(
                    f"{where}：产率口径 basis 只能是 volume 或 mass，得到 {basis!r}"
                )
        loss = item.get("loss_pct", item.get("loss"))
        if loss is not None:
            lv = _as_finite_float(loss)
            if lv is None or not 0.0 <= lv <= 100.0:
                entry["errors"].append(f"{where}：试验损失 loss_pct 必须是 0~100 的数字")
            else:
                entry["loss_pct"] = lv

        raw_cuts = item.get("cuts")
        if not isinstance(raw_cuts, list) or not raw_cuts:
            entry["errors"].append(f"{where}：切点列表 cuts 必须是非空数组")
            out.append(entry)
            continue

        for j, c in enumerate(raw_cuts):
            cwhere = f"{where} 第 {j + 1} 个切点"
            if not isinstance(c, dict):
                entry["errors"].append(f"{cwhere}：必须是对象")
                continue
            cname = c.get("name", c.get("cut_name"))
            if not isinstance(cname, str) or not cname.strip():
                entry["errors"].append(f"{cwhere}：缺少馏分名称（name）")
                cname = cname if isinstance(cname, str) else ""
            sv = _as_finite_float(c.get("start_temp_c", c.get("start_temp", c.get("start"))))
            ev = _as_finite_float(c.get("end_temp_c", c.get("end_temp", c.get("end"))))
            if sv is None:
                entry["errors"].append(f"{cwhere}：初馏点 start_temp_c 缺失或不是有限数字")
            if ev is None:
                entry["errors"].append(f"{cwhere}：终馏点 end_temp_c 缺失或不是有限数字")
            if sv is not None and ev is not None:
                entry["cuts"].append(
                    {"name": cname.strip(), "start_temp_c": sv, "end_temp_c": ev}
                )
        out.append(entry)
    return out


# ---------------- CSV ----------------
def _resolve_columns(fieldnames: list[str] | None) -> dict[str, str]:
    """把实际表头映射到逻辑键；缺必需列/有歧义时抛文件级错误。"""
    if not fieldnames:
        raise CandidateInputError("CSV 缺少表头行")
    groups = {
        "candidate": _CANDIDATE_KEYS,
        "cut": _CUT_KEYS,
        "start": _START_KEYS,
        "end": _END_KEYS,
        "basis": _BASIS_KEYS,
        "loss": _LOSS_KEYS,
    }
    resolved: dict[str, str] = {}
    norm_to_raw = {_norm_header(f): f for f in fieldnames if f is not None}
    for key, aliases in groups.items():
        hits = [raw for norm, raw in norm_to_raw.items() if norm in aliases]
        if len(hits) > 1:
            raise CandidateInputError(
                f"CSV 表头中 {key} 对应的列有歧义：{', '.join(hits)}，请只保留一列"
            )
        if hits:
            resolved[key] = hits[0]
    missing = [k for k in ("candidate", "cut", "start", "end") if k not in resolved]
    if missing:
        raise CandidateInputError(
            "CSV 缺少必需列："
            + "、".join({"candidate": "候选名称", "cut": "馏分名称",
                         "start": "初馏点", "end": "终馏点"}[k] for k in missing)
            + f"。实际表头：{', '.join(f for f in fieldnames if f)}"
        )
    return resolved


def _parse_csv(text: str, default_basis: str, default_loss_pct: float) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    try:
        cols = _resolve_columns(reader.fieldnames)
    except CandidateInputError:
        raise

    # 候选名称 -> 聚合条目（保持首次出现顺序）
    order: list[str] = []
    groups: dict[str, dict] = {}

    def _new_entry() -> dict:
        return {
            "name": None,
            "basis": default_basis,
            "loss_pct": default_loss_pct,
            "cuts": [],
            "errors": [],
            "_basis_vals": set(),
            "_loss_vals": set(),
        }

    for lineno, row in enumerate(reader, start=2):
        cells = {k: (v or "").strip() for k, v in row.items() if k is not None}
        if not any(cells.values()):
            continue  # 跳过整行空白

        cand = cells.get(cols["candidate"], "")
        if not cand:
            # 无法归属到任何候选：该行单独标记，不吞掉其他候选
            entry = _new_entry()
            order.append(f"__anon_{lineno}__")
            groups[f"__anon_{lineno}__"] = entry
            entry["errors"].append(f"CSV 第 {lineno} 行：缺少候选名称")
        else:
            if cand not in groups:
                order.append(cand)
                e = _new_entry()
                e["name"] = cand
                groups[cand] = e
            entry = groups[cand]

        cut_name = cells.get(cols["cut"], "")
        if not cut_name:
            entry["errors"].append(f"CSV 第 {lineno} 行：缺少馏分名称")

        sv = _as_finite_float(cells.get(cols["start"], ""))
        ev = _as_finite_float(cells.get(cols["end"], ""))
        raw_s = cells.get(cols["start"], "")
        raw_e = cells.get(cols["end"], "")
        if raw_s and sv is None:
            entry["errors"].append(f"CSV 第 {lineno} 行：初馏点不是数字（{raw_s!r}）")
        elif not raw_s:
            entry["errors"].append(f"CSV 第 {lineno} 行：缺少初馏点")
        if raw_e and ev is None:
            entry["errors"].append(f"CSV 第 {lineno} 行：终馏点不是数字（{raw_e!r}）")
        elif not raw_e:
            entry["errors"].append(f"CSV 第 {lineno} 行：缺少终馏点")

        if "basis" in cols:
            b = cells.get(cols["basis"], "")
            if b:
                if b not in ("volume", "mass"):
                    entry["errors"].append(
                        f"CSV 第 {lineno} 行：产率口径只能是 volume 或 mass（{b!r}）"
                    )
                else:
                    entry["_basis_vals"].add(b)
        if "loss" in cols:
            raw_l = cells.get(cols["loss"], "")
            if raw_l:
                lv = _as_finite_float(raw_l)
                if lv is None or not 0.0 <= lv <= 100.0:
                    entry["errors"].append(
                        f"CSV 第 {lineno} 行：试验损失必须是 0~100 的数字（{raw_l!r}）"
                    )
                else:
                    entry["_loss_vals"].add(lv)

        if cut_name and sv is not None and ev is not None:
            entry["cuts"].append(
                {"name": cut_name, "start_temp_c": sv, "end_temp_c": ev}
            )

    if not order:
        raise CandidateInputError("CSV 只有表头或全部为空白行，未找到任何候选")

    out: list[dict] = []
    for key in order:
        e = groups[key]
        if len(e["_basis_vals"]) > 1:
            e["errors"].append(
                f"候选「{e['name']}」的 basis 列取值不一致：{sorted(e['_basis_vals'])}"
            )
        elif e["_basis_vals"]:
            e["basis"] = next(iter(e["_basis_vals"]))
        if len(e["_loss_vals"]) > 1:
            e["errors"].append(
                f"候选「{e['name']}」的 loss_pct 列取值不一致：{sorted(e['_loss_vals'])}"
            )
        elif e["_loss_vals"]:
            e["loss_pct"] = next(iter(e["_loss_vals"]))
        if not e["cuts"] and not e["errors"]:
            e["errors"].append(f"候选「{e['name']}」没有任何可用切点行")
        e.pop("_basis_vals", None)
        e.pop("_loss_vals", None)
        out.append(e)
    return out
