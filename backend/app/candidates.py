"""候选切点方案的批量解析（CSV / JSON）与逐项评估。

用途
----
老师手头常有几套候选切点，希望**先在同一条试验曲线上批量试算比较**，
再决定把哪一套带入编辑器保存。本模块负责：

* 把 CSV 文本或 JSON 结构解析成候选列表（每项 = 候选名称 + 切点列表，
  可选 ``loss_pct`` / ``basis``）；
* 解析/校验错误**按候选隔离**：某一项无效只标记该项，其余候选照常评估；
* 评估完全复用 ``planning.evaluate_plan``（与单项 ``/evaluate`` 同一套规则），
  因此“预览结果”与之后“采用并保存”的结果一致；
* 全程只读试验数据，不写库、不修改试验。

CSV 格式（首行为表头，同一候选的多个馏分占多行，按候选名称归组）::

    candidate,cut,start,end,loss_pct
    方案甲,轻馏分,55,180,1.0
    方案甲,重馏分,180,340,1.0

表头支持常见中文别名（候选/方案、馏分、初馏点、终馏点、损失、口径）；
候选名称留空的行继承上一行的候选（兼容 Excel 合并单元格导出的 CSV）。
"""
from __future__ import annotations

import csv
import io
from typing import Any

from pydantic import ValidationError

from .curve import Severity
from .planning import evaluate_plan
from .schemas import PlanIn


class CandidateFormatError(ValueError):
    """整份候选文件无法解析（缺列、为空等）——与单项无效区分开。"""


# 表头别名 -> 内部字段名
_HEADER_ALIASES: dict[str, set[str]] = {
    "candidate": {"candidate", "plan", "name", "方案", "候选", "候选名称", "方案名称"},
    "cut": {"cut", "cut_name", "fraction", "馏分", "馏分名称", "段", "段名称"},
    "start": {"start", "start_temp_c", "初馏点", "起始温度", "起点温度"},
    "end": {"end", "end_temp_c", "终馏点", "终止温度", "终点温度"},
    "loss": {"loss", "loss_pct", "损失", "试验损失"},
    "basis": {"basis", "口径", "基准"},
}
_REQUIRED = ("candidate", "cut", "start", "end")


def parse_candidates_csv(text: str) -> tuple[list[dict], list[str]]:
    """把 CSV 文本解析为候选字典列表。

    返回 ``(candidates, row_errors)``：

    * ``candidates`` 中每项为 ``{"name", "cuts", "loss_pct", "basis", "_errors"}``，
      ``_errors`` 收集该候选自己的行级问题（如某行温度不是数字）——只让该候选无效；
    * ``row_errors`` 是无法归属到任何候选的整行问题（如首行就缺候选名）。

    整份文件级的问题（空内容、缺必需列、没有任何数据行）抛
    :class:`CandidateFormatError`。
    """
    if not text or not text.strip():
        raise CandidateFormatError("CSV 内容为空")

    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    rows = [
        [c.strip() for c in row]
        for row in csv.reader(io.StringIO(text), dialect)
        if any(c.strip() for c in row)
    ]
    if not rows:
        raise CandidateFormatError("CSV 内容为空")

    header = [h.lstrip("﻿").strip().lower() for h in rows[0]]
    col: dict[str, int] = {}
    for idx, h in enumerate(header):
        for key, aliases in _HEADER_ALIASES.items():
            if h in aliases and key not in col:
                col[key] = idx
    missing = [k for k in _REQUIRED if k not in col]
    if missing:
        raise CandidateFormatError(
            "CSV 缺少必需列："
            + "、".join(missing)
            + "。需要表头 candidate/cut/start/end（或中文别名：候选/馏分/初馏点/终馏点），"
            "可选 loss_pct（损失）与 basis（口径）"
        )

    candidates: list[dict] = []
    by_name: dict[str, dict] = {}
    row_errors: list[str] = []
    current_name: str | None = None

    def cell(row: list[str], key: str) -> str:
        i = col.get(key)
        return row[i] if i is not None and i < len(row) else ""

    for ln, row in enumerate(rows[1:], start=2):  # 行号按含表头的原始文件计
        name = cell(row, "candidate") or current_name
        if not name:
            row_errors.append(f"第 {ln} 行：缺少候选名称且前面没有可继承的候选，已跳过该行")
            continue
        current_name = name
        cand = by_name.get(name)
        if cand is None:
            cand = {
                "name": name,
                "cuts": [],
                "loss_pct": 0.0,
                "basis": "volume",
                "_loss_seen": False,
                "_errors": [],
            }
            by_name[name] = cand
            candidates.append(cand)

        if cell(row, "loss") and not cand["_loss_seen"]:
            cand["_loss_seen"] = True
            try:
                cand["loss_pct"] = float(cell(row, "loss"))
            except ValueError:
                cand["_errors"].append(
                    f"第 {ln} 行：损失 '{cell(row, 'loss')}' 不是数字"
                )
        if cell(row, "basis"):
            b = cell(row, "basis").lower()
            if b in ("volume", "mass"):
                cand["basis"] = b
            else:
                cand["_errors"].append(
                    f"第 {ln} 行：未知口径 '{cell(row, 'basis')}'（应为 volume 或 mass）"
                )

        s_raw, e_raw = cell(row, "start"), cell(row, "end")
        cut_name = cell(row, "cut") or f"馏分{len(cand['cuts']) + 1}"
        try:
            s = float(s_raw)
        except ValueError:
            cand["_errors"].append(f"第 {ln} 行：初馏点 '{s_raw}' 不是数字")
            continue
        try:
            e = float(e_raw)
        except ValueError:
            cand["_errors"].append(f"第 {ln} 行：终馏点 '{e_raw}' 不是数字")
            continue
        cand["cuts"].append({"name": cut_name, "start_temp_c": s, "end_temp_c": e})

    if not candidates:
        raise CandidateFormatError("CSV 中没有可用的候选数据行")
    return candidates, row_errors


def format_validation_error(exc: ValidationError) -> str:
    """把 Pydantic 校验错误压缩成一行中文可读的定位信息。"""
    parts = []
    for err in exc.errors()[:3]:  # 只列前几条，避免刷屏
        loc = ".".join(str(x) for x in err["loc"])
        parts.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    extra = "" if len(exc.errors()) <= 3 else f" 等 {len(exc.errors())} 处问题"
    return "；".join(parts) + extra


def summarize_result(result: dict) -> dict:
    """从完整评估结果提取批量预览用的对照摘要（产率/平衡/重叠缺口/问题数）。"""
    t = result["totals"]
    issues = result["issues"]
    n_err = sum(1 for i in issues if i["severity"] == Severity.ERROR)
    n_warn = sum(1 for i in issues if i["severity"] == Severity.WARNING)
    n_info = sum(1 for i in issues if i["severity"] == Severity.INFO)
    return {
        "nominal_yield_pct": t["nominal_yield_pct"],
        "union_yield_pct": t["union_yield_pct"],
        "overlap_pct": t["overlap_pct"],
        "gap_total_pct": round(
            t["front_gap_pct"] + t["inter_gap_pct"] + t["tail_gap_pct"], 4
        ),
        "residual_total_pct": t["residual_total_pct"],
        "identity_ok": t["identity_ok"],
        "identity_residual_pct": t["identity_residual_pct"],
        "n_errors": n_err,
        "n_warnings": n_warn,
        "n_infos": n_info,
        "n_issues": len(issues),
        "has_blocking_errors": result["has_blocking_errors"],
    }


def preview_one(raw: Any, index: int, evaluate) -> dict:
    """校验并评估单个候选；任何失败都只反映在该项的返回里。

    ``evaluate`` 是 ``(PlanIn) -> dict`` 的计算回调（由路由注入，内部即
    ``_eval_experiment``），保证与单项 ``/evaluate`` 完全同一套规则。
    """
    name = raw.get("name") if isinstance(raw, dict) else None
    if not isinstance(raw, dict):
        return _invalid(index, name, "候选项必须是对象（含 name 与 cuts）")
    row_errors = raw.get("_errors") or []
    try:
        plan = PlanIn.model_validate(
            {k: v for k, v in raw.items() if not k.startswith("_")}
        )
    except ValidationError as exc:
        msg = format_validation_error(exc)
        if row_errors:
            msg = "；".join(row_errors + [msg])
        return _invalid(index, name, msg)
    if row_errors:
        return _invalid(index, plan.name, "；".join(row_errors))

    result = evaluate(plan)
    return {
        "index": index,
        "name": plan.name,
        "valid": True,
        "error": None,
        "plan": {
            "name": plan.name,
            "basis": plan.basis,
            "loss_pct": plan.loss_pct,
            "cuts": [c.model_dump() for c in plan.cuts],
        },
        "summary": summarize_result(result),
        "result": result,
    }


def _invalid(index: int, name: Any, error: str) -> dict:
    return {
        "index": index,
        "name": str(name) if name else f"（第 {index + 1} 项，未命名）",
        "valid": False,
        "error": error,
        "plan": None,
        "summary": None,
        "result": None,
    }
