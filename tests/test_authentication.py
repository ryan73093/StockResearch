from urllib.parse import parse_qs, urlparse

import pytest

from quant_platform.application.authentication import (
    AuthenticationService, GoogleIdentity, api_write_authorization,
)
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.database.repositories import SqlAlchemyAuthenticationRepository


class FakeGoogleClient:
    def authorization_url(self, state: str, code_challenge: str) -> str:
        assert code_challenge
        return f"https://accounts.example/auth?state={state}"

    def exchange(self, code: str, code_verifier: str) -> GoogleIdentity:
        assert code == "authorization-code"
        assert code_verifier
        return GoogleIdentity(
            subject="google-sub-123", email="researcher@example.com",
            email_verified=True, display_name="研究使用者",
            avatar_url="https://example.com/avatar.png", hosted_domain=None,
        )


def _service(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'auth.db'}",
        google_oauth_enabled=True, google_client_id="client-id",
        google_client_secret="client-secret",
        google_redirect_uri="http://127.0.0.1:5000/auth/google/callback",
    )
    container = build_container(settings)
    repository = SqlAlchemyAuthenticationRepository(container.database.session_factory)
    service = AuthenticationService(settings, repository, FakeGoogleClient())
    return container, service


def test_google_login_state_is_single_use_and_session_is_server_side(tmp_path):
    _, service = _service(tmp_path)
    start = service.begin("browser-nonce", "/portfolios")
    state = parse_qs(urlparse(start.authorization_url).query)["state"][0]
    result = service.complete(state, "authorization-code", "browser-nonce")
    assert result.user.email == "researcher@example.com"
    assert result.next_path == "/portfolios"
    assert service.authenticate(result.session_token).id == result.user.id
    with pytest.raises(ValueError):
        service.complete(state, "authorization-code", "browser-nonce")
    service.logout(result.session_token)
    assert service.authenticate(result.session_token) is None


def test_login_state_is_bound_to_browser_and_next_path_is_local(tmp_path):
    _, service = _service(tmp_path)
    start = service.begin("correct-browser", "https://evil.example/redirect")
    state = parse_qs(urlparse(start.authorization_url).query)["state"][0]
    with pytest.raises(ValueError):
        service.complete(state, "authorization-code", "wrong-browser")
    result = service.complete(state, "authorization-code", "correct-browser")
    assert result.next_path == "/"


def test_account_page_explains_disabled_local_mode(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'page.db'}"))
    response = create_app(container).test_client().get("/account")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "本機研究模式" in page
    assert "Google 登入尚未設定" in page


def test_flask_google_callback_sets_opaque_session_and_logout_revokes_it(tmp_path):
    container, service = _service(tmp_path)
    container.authentication_service = service
    client = create_app(container).test_client()
    start = client.get("/auth/google/start?next=/account")
    assert start.status_code == 302
    state = parse_qs(urlparse(start.location).query)["state"][0]
    callback = client.get(
        f"/auth/google/callback?state={state}&code=authorization-code"
    )
    assert callback.status_code == 302
    assert callback.location.endswith("/account")
    account = client.get("/account").get_data(as_text=True)
    assert "研究使用者" in account
    assert "researcher@example.com" in account
    client.post("/logout")
    assert "本機研究模式" in client.get("/account").get_data(as_text=True)


def test_production_web_is_read_only_until_login_is_configured(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'readonly.db'}", app_env="production"
    )
    client = create_app(build_container(settings)).test_client()
    response = client.post("/reports/generate/TW")
    assert response.status_code == 503
    assert "寫入操作維持關閉" in response.get_data(as_text=True)


def test_api_write_guard_is_constant_time_bearer_boundary():
    production = Settings(app_env="production", api_write_token="secret-token")
    assert api_write_authorization(production, "GET", "") is None
    assert api_write_authorization(production, "POST", "")[0] == 401
    assert api_write_authorization(production, "POST", "Bearer wrong")[0] == 401
    assert api_write_authorization(production, "POST", "Bearer secret-token") is None
    assert api_write_authorization(Settings(app_env="production"), "POST", "")[0] == 503
