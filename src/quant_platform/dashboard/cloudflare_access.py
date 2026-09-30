"""Cloudflare Access authentication for the published dashboard.

The site is published through a dedicated Cloudflare Tunnel with the origin
bound to 127.0.0.1. Cloudflare Access (Google login) authenticates at the edge
and forwards a signed ``Cf-Access-Jwt-Assertion``; this module verifies that
JWT (signature, audience, issuer, expiry, email) and allows only configured
owner emails. Direct loopback requests to 127.0.0.1/localhost stay open for
local use. Everything else fails closed, including a half-configured setup.
Pattern follows the VectorDB project's ``access_control.py``.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from flask import Flask, g, jsonify, request

logger = logging.getLogger(__name__)

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
EXEMPT_PATHS = {"/health"}


def _email(value: object) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if EMAIL_PATTERN.fullmatch(candidate) else ""


@dataclass(frozen=True, slots=True)
class AccessUser:
    email: str
    display_name: str
    source: str


class CloudflareAccessValidator:
    def __init__(self, team_domain: str, audience: str, jwks_client: object | None = None):
        self.issuer = team_domain.strip().rstrip("/")
        self.audience = audience.strip()
        parsed = urlparse(self.issuer)
        self.configured = bool(
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.hostname.endswith(".cloudflareaccess.com")
            and self.audience
        )
        self._jwks = jwks_client
        if self.configured and self._jwks is None:
            from jwt import PyJWKClient

            self._jwks = PyJWKClient(f"{self.issuer}/cdn-cgi/access/certs")

    def email(self, token: str | None) -> str | None:
        if not self.configured or not token:
            return None
        try:
            import jwt
            from jwt.exceptions import PyJWTError

            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "iss", "aud", "email"]},
            )
        except (PyJWTError, ValueError, TypeError) as exc:
            logger.warning("Rejected Cloudflare Access token: %s", exc)
            return None
        return _email(claims.get("email")) or None


def _is_local_request() -> bool:
    try:
        remote_is_loopback = ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False
    host = urlparse(f"//{request.host}").hostname
    local_origin = f"{request.scheme}://{request.host}".rstrip("/")
    origin = request.headers.get("Origin", "").rstrip("/")
    return (
        remote_is_loopback
        and host in LOCAL_HOSTS
        and not request.headers.get("Cf-Access-Jwt-Assertion")
        and (not origin or origin == local_origin)
    )


def register_cloudflare_access(app: Flask, settings, validator=None) -> None:
    """Install the Access gate when ``AUTH_MODE=cloudflare-access``."""
    if settings.auth_mode != "cloudflare-access":
        return
    access = validator or CloudflareAccessValidator(
        settings.access_team_domain, settings.access_aud
    )
    public_origin = settings.public_url.strip().rstrip("/")
    parsed_origin = urlparse(public_origin)
    allowed = {
        email for email in (_email(item) for item in settings.access_allowed_emails.split(","))
        if email
    }
    configured = bool(
        access.configured
        and allowed
        and parsed_origin.scheme == "https"
        and parsed_origin.hostname
        and not parsed_origin.path.rstrip("/")
    )
    app.extensions["cloudflare_access_configured"] = configured

    @app.before_request
    def cloudflare_access_gate():
        g.access_user = None
        if request.path in EXEMPT_PATHS:
            return None
        if _is_local_request():
            g.access_user = AccessUser("local@localhost", "本機使用者", "loopback")
            return None
        if not configured:
            return jsonify(error="Cloudflare Access 尚未完成安全設定"), 503
        email = access.email(request.headers.get("Cf-Access-Jwt-Assertion"))
        if not email:
            return jsonify(error="Cloudflare 登入驗證失敗，請由正式網址重新登入"), 401
        if email not in allowed:
            return jsonify(error="此 Google 帳號沒有使用權限"), 403
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("Origin")
            if origin and origin.rstrip("/") != public_origin:
                return jsonify(error="不允許跨來源寫入"), 403
        g.access_user = AccessUser(email, email, "cloudflare-access")
        return None

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response
