"""ORM 模型：离线蒸馏试验 + 馏分切割方案。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base, JSONLike


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Experiment(Base):
    """一次离线实沸点（TBP）/ 恩氏蒸馏试验。

    points 中 recovered_pct 为累积回收体积百分比（进料体积基准，0..100）；
    feed_density 为进料 20 ℃ 密度，density_rows 为各馏出温度段密度
    （以段中点温度插值），用于质量/体积基准换算。
    """

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sample_id: Mapped[str | None] = mapped_column(String(100))
    feed_density_g_cm3: Mapped[float] = mapped_column(Float, nullable=False)
    # 蒸馏残渣（塔底）20 ℃ 密度，单独给出；质量基准闭合需要它
    residue_density_g_cm3: Mapped[float | None] = mapped_column(Float, nullable=True)
    # {"apparatus": "TBP", "pressure_mm_hg": 760, "standard": "ASTM D2892", ...}
    conditions: Mapped[dict] = mapped_column(JSONLike, default=dict)
    # [{"temp_c": 35.0, "recovered_pct": 2.1}, ...]
    points: Mapped[list] = mapped_column(JSONLike, default=list)
    # [{"temp_c": 80.0, "density_g_cm3": 0.68}, ...]（馏出温度-密度表）
    density_rows: Mapped[list] = mapped_column(JSONLike, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    plans: Mapped[list["Plan"]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )


class Plan(Base):
    """用户保存的一组馏分切点。产率在读取时按当前数据重新计算，
    同时保留保存时刻的结果快照（result_snapshot）便于追溯。"""

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    basis: Mapped[str] = mapped_column(String(10), default="volume")  # volume | mass
    # [{"name": "轻石脑油", "start_temp_c": 35, "end_temp_c": 100}, ...]
    cuts: Mapped[list] = mapped_column(JSONLike, default=list)
    loss_pct: Mapped[float] = mapped_column(Float, default=0.0)
    result_snapshot: Mapped[dict | None] = mapped_column(JSONLike, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    experiment: Mapped[Experiment] = relationship(back_populates="plans")
