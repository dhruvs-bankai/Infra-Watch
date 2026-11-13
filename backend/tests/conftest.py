from collections.abc import Iterator
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

os.environ["DATABASE_URL"] = "sqlite:///./infrawatch-import-test.db"
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-that-is-long-enough-for-unit-tests-123456"
os.environ["INITIAL_ADMIN_SETUP_TOKEN"] = "test-initial-setup-token-that-is-long-enough-123456"

from app.core.config import Settings
from app.core.database import Base
from app.main import create_app
from app.models import AppSetting


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'infrawatch-test.db'}",
        jwt_secret_key="test-jwt-secret-that-is-long-enough-for-unit-tests-123456",
        initial_admin_setup_token="test-initial-setup-token-that-is-long-enough-123456",
        prometheus_discovery_dir=str(tmp_path / "file_sd"),
        monitoring_allowed_cidrs="10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7",
        monitoring_allowed_ports="9100,19100,8080,9182,9256",
    )
    application = create_app(settings)
    Base.metadata.create_all(application.state.engine)
    # The initial setup state is normally inserted by Alembic.
    with application.state.session_factory.begin() as session:
        if session.scalar(select(AppSetting).where(AppSetting.key == "initial_admin_setup_complete")) is None:
            session.add(AppSetting(key="initial_admin_setup_complete", value={"complete": False}))
    with TestClient(application) as test_client:
        yield test_client
    application.state.engine.dispose()


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/setup",
        json={
            "setup_token": "test-initial-setup-token-that-is-long-enough-123456",
            "username": "admin",
            "password": "Safe-test-password-2026!",
        },
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
