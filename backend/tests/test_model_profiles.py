from __future__ import annotations

from fastapi.testclient import TestClient


def _create_profile(
    client: TestClient,
    name: str,
    *,
    is_default: bool = False,
) -> dict[str, object]:
    response = client.post(
        "/api/v3/model-profiles",
        json={
            "name": name,
            "provider": "MOCK",
            "base_url": "http://127.0.0.1/mock",
            "model": "deterministic-mock",
            "temperature": 0.2,
            "timeout_seconds": 30,
            "is_default": is_default,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_model_profiles_have_one_explicit_default_and_support_deletion(
    client: TestClient,
) -> None:
    first = _create_profile(client, "first")
    second = _create_profile(client, "second", is_default=True)

    profiles = client.get("/api/v3/model-profiles").json()["items"]
    assert sum(profile["is_default"] for profile in profiles) == 1
    first_profile = next(profile for profile in profiles if profile["id"] == first["id"])
    second_profile = next(profile for profile in profiles if profile["id"] == second["id"])
    assert first_profile["is_default"] is False
    assert second_profile["is_default"] is True

    cannot_disable = client.patch(
        f"/api/v3/model-profiles/{second['id']}", json={"enabled": False}
    )
    assert cannot_disable.status_code == 409
    assert cannot_disable.json()["error"]["code"] == "MODEL_DEFAULT_CANNOT_DISABLE"

    deleted = client.delete(f"/api/v3/model-profiles/{second['id']}")
    assert deleted.status_code == 204
    profiles = client.get("/api/v3/model-profiles").json()["items"]
    assert len(profiles) == 1
    assert profiles[0]["id"] == first["id"]
    assert profiles[0]["is_default"] is True


def test_model_profile_never_returns_api_key_and_mock_test_is_available(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "with-key",
            "provider": "MOCK",
            "base_url": "http://127.0.0.1/mock",
            "model": "deterministic-mock",
            "api_key": "a-secret-that-must-not-return",
            "temperature": 0,
            "timeout_seconds": 30,
        },
    )
    assert response.status_code == 201, response.text
    profile = response.json()
    assert "api_key" not in profile
    assert "encrypted_api_key" not in profile
    assert profile["has_api_key"] is True

    tested = client.post(f"/api/v3/model-profiles/{profile['id']}/test")
    assert tested.status_code == 200
    assert tested.json()["ok"] is True
