from fastapi.testclient import TestClient


SETUP = {
    "setup_token": "test-initial-setup-token-that-is-long-enough-123456",
    "username": "PrimaryAdmin",
    "password": "Safe-test-password-2026!",
}


def test_first_run_setup_login_and_logout(client: TestClient) -> None:
    setup = client.post("/api/v1/auth/setup", json=SETUP)
    assert setup.status_code == 201
    assert setup.json()["user"]["username"] == "primaryadmin"
    access_token = setup.json()["access_token"]
    assert len(access_token.split(".")) == 3

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me.status_code == 200
    assert me.json()["is_admin"] is True

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "PRIMARYADMIN", "password": SETUP["password"]},
    )
    assert login.status_code == 200
    fresh_token = login.json()["access_token"]
    logout = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {fresh_token}"})
    assert logout.status_code == 200
    invalidated = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {fresh_token}"})
    assert invalidated.status_code == 401


def test_setup_token_is_required_and_setup_is_one_time(client: TestClient) -> None:
    bad = client.post("/api/v1/auth/setup", json={**SETUP, "setup_token": "wrong-token-which-is-long-enough-to-pass-schema"})
    assert bad.status_code == 403
    good = client.post("/api/v1/auth/setup", json=SETUP)
    assert good.status_code == 201
    repeat = client.post("/api/v1/auth/setup", json={**SETUP, "username": "another-admin"})
    assert repeat.status_code == 409


def test_login_does_not_reveal_unknown_user(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"username": "unknown", "password": "bad"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_protected_endpoint_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/servers")
    assert response.status_code == 401
