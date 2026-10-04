import os
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

# 必须在导入 app.* 之前设置：API 测试使用临时 SQLite，与开发库隔离
_TMP_DB = _BACKEND / ".pytest_tmp" / "test_api.db"
_TMP_DB.parent.mkdir(exist_ok=True)
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c
