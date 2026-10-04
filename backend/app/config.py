"""运行配置。

默认使用本地 SQLite，便于离线演示与测试；
设置 DATABASE_URL 环境变量即可切换到 PostgreSQL，例如：

    postgresql+asyncpg://user:password@localhost:5432/distillation

数据库类型不影响任何插值与产率计算逻辑（计算全部在纯 NumPy/SciPy 层完成）。
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./distillation.db"
    # 允许前端直连开发服务器时跨域；生产按实际域名收紧
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
