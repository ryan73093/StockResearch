from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard import cloudflare_access
from quant_platform.dashboard.app import create_app

TEAM = "https://raspy-mode-cc1c.cloudflareaccess.com"
AUD = "test-audience"
PUBLIC = "https://stockresearch.pimi-sunsun.com"
OWNER = "owner@example.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=KEY.public_key())


def _token(email=OWNER, audience=AUD, issuer=TEAM, expires_in=300):
    now = datetime.now(UTC)
    return jwt.encode(
        {"email": email, "aud": audience, "iss": issuer, "iat": now,
         "exp": now + timedelta(seconds=expires_in)},
        KEY, algorithm="RS256",
    )


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    real_validator = cloudflare_access.CloudflareAccessValidator
    monkeypatch.setattr(
        cloudflare_access, "CloudflareAccessValidator",
        lambda team, aud: real_validator(team, aud, FakeJwks()),
    )

    def build(aud=AUD):
        settings = Settings(
            database_url=f"sqlite:///{tmp_path / 'access.db'}", scheduler_in_web=False,
            auth_mode="cloudflare-access", public_url=PUBLIC, access_team_domain=TEAM,
            access_aud=aud, access_allowed_emails=OWNER,
        )
        return create_app(build_container(settings)).test_client()

    return build


def _public(client, path="/guide", token=None, method="get", **headers):
    if token:
        headers["Cf-Access-Jwt-Assertion"] = token
    return getattr(client, method)(
        path, base_url=PUBLIC, environ_base={"REMOTE_ADDR": "127.0.0.1"}, headers=headers,
    )


def test_local_loopback_request_is_allowed(make_client):
    response = make_client().get(
        "/guide", base_url="http://127.0.0.1:5000", environ_base={"REMOTE_ADDR": "127.0.0.1"}
    )
    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"


def test_tunnel_request_without_token_is_rejected(make_client):
    # cloudflared connects from loopback but keeps the public Host header.
    assert _public(make_client()).status_code == 401


def test_owner_token_is_accepted(make_client):
    response = _public(make_client(), token=_token())
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"


def test_other_account_is_forbidden(make_client):
    assert _public(make_client(), token=_token(email="someone@example.com")).status_code == 403


@pytest.mark.parametrize(
    "token",
    [
        _token(audience="other-app"),
        _token(issuer="https://other.cloudflareaccess.com"),
        _token(expires_in=-60),
        "not-a-jwt",
    ],
    ids=["wrong-audience", "wrong-issuer", "expired", "garbage"],
)
def test_invalid_tokens_are_rejected(make_client, token):
    assert _public(make_client(), token=token).status_code == 401


def test_half_configured_access_fails_closed(make_client):
    assert _public(make_client(aud=""), token=_token()).status_code == 503


def test_cross_origin_write_is_rejected(make_client):
    response = _public(
        make_client(), path="/paper-trading/orders", token=_token(), method="post",
        Origin="https://evil.example",
    )
    assert response.status_code == 403


def test_health_stays_reachable_for_local_probes(make_client):
    assert _public(make_client(), path="/health").status_code in {200, 503}
