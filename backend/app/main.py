"""FastAPI 入口。

端点
----
GET  /api/health
POST /api/seed                 导入 data/seed.json（仅库空时或 force=true）
GET  /api/experiments
POST /api/experiments
GET  /api/experiments/{id}
GET  /api/experiments/{id}/curve/sample
POST /api/experiments/{id}/evaluate                 仅计算，不落库
POST /api/experiments/{id}/candidates/preview       候选方案批量预览（CSV/JSON，仅计算，不落库）
POST /api/experiments/{id}/plans                    保存方案
GET  /api/plans/{id}                                读方案（重新计算）
GET  /api/plans/{id}/export?format=markdown|json    导出

仅处理库中给定的离线试验数据，不连接任何现场装置。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .candidates import (
    CandidateFormatError,
    parse_candidates_csv,
    preview_one,
)
from .config import settings
from .curve import prepare_curve
from .database import get_session, init_db
from .density import prepare_density
from .exporters import build_markdown, to_json
from .models import Experiment, Plan
from .planning import evaluate_plan
from .schemas import ExperimentIn, ExperimentOut, PlanIn, PlanOut

app = FastAPI(
    title="蒸馏曲线切点与产率核对（培训）",
    version="1.0.0",
    description="离线蒸馏试验数据的单调插值（PCHIP）、馏分切割与质量/体积产率核对。",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

SEED_PATH = Path(__file__).resolve().parents[2] / "data" / "seed.json"


@app.on_event("startup")
async def _startup() -> None:
    await init_db()


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


# ---- 公共计算辅助 ----
def _eval_experiment(exp: Experiment, payload: PlanIn) -> dict:
    curve = prepare_curve(exp.points)
    density = prepare_density(exp.density_rows or [])
    return evaluate_plan(
        curve=curve,
        cuts=[c.model_dump() for c in payload.cuts],
        loss_pct=payload.loss_pct,
        basis=payload.basis,
        density=density,
        feed_density=exp.feed_density_g_cm3,
        residue_density=exp.residue_density_g_cm3,
    )


def _exp_to_out(exp: Experiment) -> dict:
    return {
        "id": exp.id,
        "name": exp.name,
        "sample_id": exp.sample_id,
        "feed_density_g_cm3": exp.feed_density_g_cm3,
        "residue_density_g_cm3": exp.residue_density_g_cm3,
        "conditions": exp.conditions or {},
        "points": exp.points or [],
        "density_rows": exp.density_rows or [],
        "notes": exp.notes,
    }


# ---- 种子 ----
@app.post("/api/seed")
async def load_seed(force: bool = False) -> dict:
    async for session in get_session():
        if not force:
            n = (await session.execute(select(func.count(Experiment.id)))).scalar_one()
            if n:
                raise HTTPException(409, f"库中已有 {n} 条试验；确认覆盖请用 force=true")
        if force:
            for p in (await session.execute(select(Plan))).scalars().all():
                await session.delete(p)
            for e in (await session.execute(select(Experiment))).scalars().all():
                await session.delete(e)
            await session.flush()

        data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        created = 0
        for item in data["experiments"]:
            plans = item.pop("plans", [])
            residue_density = item.pop("residue_density_g_cm3", None)
            exp = Experiment(
                name=item["name"],
                sample_id=item.get("sample_id"),
                feed_density_g_cm3=item["feed_density_g_cm3"],
                residue_density_g_cm3=residue_density,
                conditions=item.get("conditions") or {},
                points=item["points"],
                density_rows=item.get("density_rows", []),
                notes=item.get("notes"),
            )
            session.add(exp)
            await session.flush()
            for p in plans:
                payload = PlanIn(**p)
                result = _eval_experiment(exp, payload)
                session.add(
                    Plan(
                        experiment_id=exp.id,
                        name=payload.name,
                        basis=payload.basis,
                        cuts=[c.model_dump() for c in payload.cuts],
                        loss_pct=payload.loss_pct,
                        result_snapshot=result,
                    )
                )
            created += 1
        await session.commit()
        return {"loaded_experiments": created}


# ---- 试验 ----
@app.get("/api/experiments")
async def list_experiments() -> list[ExperimentOut]:
    async for session in get_session():
        rows = (await session.execute(select(Experiment).order_by(Experiment.id))).scalars()
        return [ExperimentOut.model_validate(r) for r in rows]


@app.post("/api/experiments", status_code=201)
async def create_experiment(payload: ExperimentIn) -> ExperimentOut:
    async for session in get_session():
        exp = Experiment(
            name=payload.name,
            sample_id=payload.sample_id,
            feed_density_g_cm3=payload.feed_density_g_cm3,
            residue_density_g_cm3=payload.residue_density_g_cm3,
            conditions=payload.conditions,
            points=[p.model_dump() for p in payload.points],
            density_rows=[d.model_dump() for d in payload.density_rows],
            notes=payload.notes,
        )
        curve = prepare_curve(exp.points)  # 提前暴露硬错误
        if curve.hard_errors:
            raise HTTPException(
                422,
                detail={
                    "message": "蒸馏曲线数据校验未通过，请先核实修正",
                    "issues": [i.as_dict() for i in curve.hard_errors],
                },
            )
        session.add(exp)
        await session.commit()
        await session.refresh(exp)
        return ExperimentOut.model_validate(exp)


@app.get("/api/experiments/{exp_id}")
async def get_experiment(exp_id: int) -> dict:
    async for session in get_session():
        exp = await session.scalar(
            select(Experiment)
            .where(Experiment.id == exp_id)
            .options(selectinload(Experiment.plans))
        )
        if exp is None:
            raise HTTPException(404, "试验不存在")
        out = _exp_to_out(exp)
        out["plans"] = [
            {
                "id": p.id,
                "name": p.name,
                "basis": p.basis,
                "loss_pct": p.loss_pct,
                "cuts": p.cuts,
            }
            for p in sorted(exp.plans, key=lambda x: x.id)
        ]
        return out


@app.get("/api/experiments/{exp_id}/curve/sample")
async def curve_sample(exp_id: int, n: int = Query(200, ge=10, le=1000)) -> dict:
    async for session in get_session():
        exp = await session.get(Experiment, exp_id)
        if exp is None:
            raise HTTPException(404, "试验不存在")
        curve = prepare_curve(exp.points)
        ts, rs = curve.sample(n)
        return {
            "temps_c": ts,
            "recovered_pct": rs,
            "raw_points": exp.points,
            "range": {"temp_c": [curve.t_min, curve.t_max],
                      "recovered_pct": [curve.r_min, curve.r_max]},
            "issues": [i.as_dict() for i in curve.issues],
            "has_blocking_errors": bool(curve.hard_errors),
        }


# ---- 方案计算 / 保存 ----
@app.post("/api/experiments/{exp_id}/evaluate")
async def evaluate(exp_id: int, payload: PlanIn) -> dict:
    async for session in get_session():
        exp = await session.get(Experiment, exp_id)
        if exp is None:
            raise HTTPException(404, "试验不存在")
        return _eval_experiment(exp, payload)


# ---- 候选方案批量预览（只读评估，不落库、不改试验数据）----
@app.post("/api/experiments/{exp_id}/candidates/preview")
async def preview_candidates(exp_id: int, request: Request) -> dict:
    """接收一组候选切点方案（JSON 或 CSV），逐项复用单项评估规则试算。

    * JSON（``application/json``）：``{"candidates": [...]}`` 或直接 ``[...]``，
      每项与 ``/evaluate`` 的请求体相同（name/basis/loss_pct/cuts）；
    * CSV（``text/csv``）：见 ``candidates.parse_candidates_csv`` 的表头约定；
    * 单项无效只标记该项（``valid=false`` + 原因），不影响其他候选；
    * 整份文件格式错误返回 422；本端点不创建方案、不修改试验数据。
    """
    async for session in get_session():
        exp = await session.get(Experiment, exp_id)
        if exp is None:
            raise HTTPException(404, "试验不存在")

        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        row_errors: list[str] = []
        if ctype in ("text/csv", "text/plain", "application/csv"):
            raw_text = (await request.body()).decode("utf-8-sig", errors="replace")
            try:
                raw_candidates, row_errors = parse_candidates_csv(raw_text)
            except CandidateFormatError as exc:
                raise HTTPException(422, str(exc)) from exc
        else:
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(
                    422, "请求体不是合法 JSON；CSV 请使用 Content-Type: text/csv"
                ) from None
            if isinstance(body, list):
                raw_candidates = body
            elif isinstance(body, dict) and isinstance(body.get("candidates"), list):
                raw_candidates = body["candidates"]
            else:
                raise HTTPException(
                    422, "JSON 需为候选数组，或形如 {\"candidates\": [...]} 的对象"
                )

        if not raw_candidates:
            raise HTTPException(422, "未解析到任何候选方案")

        def _run(plan: PlanIn) -> dict:
            return _eval_experiment(exp, plan)

        candidates = [
            preview_one(raw, i, _run) for i, raw in enumerate(raw_candidates)
        ]
        return {
            "experiment_id": exp_id,
            "count": len(candidates),
            "valid_count": sum(1 for c in candidates if c["valid"]),
            "row_errors": row_errors,
            "candidates": candidates,
        }


@app.post("/api/experiments/{exp_id}/plans", status_code=201)
async def create_plan(exp_id: int, payload: PlanIn) -> dict:
    async for session in get_session():
        exp = await session.get(Experiment, exp_id)
        if exp is None:
            raise HTTPException(404, "试验不存在")
        result = _eval_experiment(exp, payload)
        plan = Plan(
            experiment_id=exp_id,
            name=payload.name,
            basis=payload.basis,
            cuts=[c.model_dump() for c in payload.cuts],
            loss_pct=payload.loss_pct,
            result_snapshot=result,
        )
        session.add(plan)
        await session.commit()
        await session.refresh(plan)
        return {"id": plan.id, "result": result}


@app.get("/api/plans/{plan_id}")
async def get_plan(plan_id: int) -> dict:
    async for session in get_session():
        plan = await session.scalar(
            select(Plan)
            .where(Plan.id == plan_id)
            .options(selectinload(Plan.experiment))
        )
        if plan is None:
            raise HTTPException(404, "方案不存在")
        payload = PlanIn(
            name=plan.name, basis=plan.basis, cuts=plan.cuts, loss_pct=plan.loss_pct
        )
        result = _eval_experiment(plan.experiment, payload)
        return {
            "plan": PlanOut.model_validate(plan).model_dump(),
            "result": result,
        }


@app.get("/api/plans/{plan_id}/export")
async def export_plan(
    plan_id: int, format: Literal["markdown", "json"] = "markdown"
) -> Response:
    async for session in get_session():
        plan = await session.scalar(
            select(Plan)
            .where(Plan.id == plan_id)
            .options(selectinload(Plan.experiment))
        )
        if plan is None:
            raise HTTPException(404, "方案不存在")
        payload = PlanIn(
            name=plan.name, basis=plan.basis, cuts=plan.cuts, loss_pct=plan.loss_pct
        )
        exp = plan.experiment
        result = _eval_experiment(exp, payload)
        exp_out = _exp_to_out(exp)
        plan_out = {
            "id": plan.id,
            "name": plan.name,
            "basis": plan.basis,
            "cuts": plan.cuts,
            "loss_pct": plan.loss_pct,
        }
        if format == "json":
            return Response(
                to_json(exp_out, plan_out, result),
                media_type="application/json",
                headers={
                    "Content-Disposition": 'attachment; filename="distillation_plan.json"'
                },
            )
        md = build_markdown(exp_out, plan_out, result)
        return PlainTextResponse(
            md,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="distillation_plan.md"'
            },
        )
