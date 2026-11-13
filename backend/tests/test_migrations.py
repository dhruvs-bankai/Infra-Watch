import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_initial_alembic_migration_creates_schema(tmp_path):
    backend_dir = Path(__file__).resolve().parents[1]
    database = tmp_path / "migration-test.sqlite3"
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": "sqlite:///" + database.as_posix(),
            "JWT_SECRET_KEY": "migration-test-jwt-secret-that-is-long-enough-123456",
            "INITIAL_ADMIN_SETUP_TOKEN": "migration-test-setup-token-that-is-long-enough-123456",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "users",
            "servers",
            "tags",
            "audit_logs",
            "app_settings",
            "outbox_events",
            "server_integrations",
            "endpoint_monitors",
        }.issubset(tables)
        setup_value = connection.execute(
            "SELECT value FROM app_settings WHERE key = 'initial_admin_setup_complete'"
        ).fetchone()[0]
        assert '"complete": false' in setup_value
