from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import requests

from quant_platform.application.ports import AuthenticationRepository
from quant_platform.config.settings import Settings
from quant_platform.domain.entities import OAuthLoginState, PlatformUser, UserSession


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64_digest(value: str) -> str:
    digest = hashlib.sha256(value.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _safe_next_path(value: str | None) -> str:
    path = value or "/"
    return path if path.startswith("/") and not path.startswith("//") else "/"


def api_write_authorization(
    settings: Settings, method: str, authorization_header: str
) -> tuple[int, str] | None:
    if method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    configured = settings.api_write_token
    if settings.is_production and not configured:
        return 503, "正式環境缺少 API_WRITE_TOKEN，寫入端點已關閉。"
    if configured and not hmac.compare_digest(
        authorization_header, f"Bearer {configured}"
    ):
        return 401, "缺少或無效的 API Bearer token。"
    return None


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    subject: str
    email: str
    email_verified: bool
    display_name: str
    avatar_url: str | None
    hosted_domain: str | None


@dataclass(frozen=True, slots=True)
class LoginStart:
    authorization_url: str
    browser_nonce: str


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: PlatformUser
    session_token: str
    next_path: str


class GoogleOidcClient:
    AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
    USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def authorization_url(self, state: str, code_challenge: str) -> str:
        query = urlencode({
            "client_id": self._settings.google_client_id,
            "redirect_uri": self._settings.google_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        })
        return f"{self.AUTHORIZATION_ENDPOINT}?{query}"

    def exchange(self, code: str, code_verifier: str) -> GoogleIdentity:
        token_response = requests.post(self.TOKEN_ENDPOINT, data={
            "client_id": self._settings.google_client_id,
            "client_secret": self._settings.google_client_secret,
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": self._settings.google_redirect_uri,
        }, timeout=20)
        token_response.raise_for_status()
        access_token = token_response.json().get("access_token")
        if not access_token:
            raise ValueError("Google token 回應缺少 access_token")
        profile_response = requests.get(
            self.USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"}, timeout=20,
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
        return GoogleIdentity(
            subject=str(profile.get("sub", "")), email=str(profile.get("email", "")),
            email_verified=bool(profile.get("email_verified", False)),
            display_name=str(profile.get("name") or profile.get("email") or "Google 使用者"),
            avatar_url=profile.get("picture"), hosted_domain=profile.get("hd"),
        )


class AuthenticationService:
    def __init__(
        self, settings: Settings, repository: AuthenticationRepository,
        oidc_client: GoogleOidcClient,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._oidc = oidc_client

    @property
    def enabled(self) -> bool:
        return bool(
            self._settings.google_oauth_enabled and self._settings.google_client_id
            and self._settings.google_client_secret and self._settings.google_redirect_uri
        )

    def begin(self, browser_nonce: str, next_path: str | None = None) -> LoginStart:
        if not self.enabled:
            raise RuntimeError("Google 登入尚未設定")
        now = datetime.now(UTC)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        self._repository.save_login_state(OAuthLoginState(
            state_hash=_digest(state), browser_nonce_hash=_digest(browser_nonce),
            code_verifier=verifier, next_path=_safe_next_path(next_path),
            expires_at=now + timedelta(minutes=10), used_at=None,
        ))
        return LoginStart(self._oidc.authorization_url(state, _b64_digest(verifier)), browser_nonce)

    def complete(self, state: str, code: str, browser_nonce: str) -> LoginResult:
        now = datetime.now(UTC)
        login_state = self._repository.consume_login_state(
            _digest(state), _digest(browser_nonce), now
        )
        if login_state is None:
            raise ValueError("登入狀態無效、已使用或已逾時")
        identity = self._oidc.exchange(code, login_state.code_verifier)
        if not identity.subject or not identity.email or not identity.email_verified:
            raise ValueError("Google 帳號缺少已驗證的 Email")
        allowed_domain = self._settings.google_allowed_domain.strip().lower()
        email_domain = identity.email.rsplit("@", 1)[-1].lower()
        if allowed_domain and email_domain != allowed_domain:
            raise PermissionError("此 Google 帳號不在允許的網域")
        user = self._repository.upsert_user(PlatformUser(
            id=None, provider="google", provider_subject=identity.subject,
            email=identity.email.lower(), display_name=identity.display_name,
            avatar_url=identity.avatar_url, is_active=True,
            created_at=now, last_login_at=now,
        ))
        if user.id is None:
            raise RuntimeError("使用者建立失敗")
        raw_token = secrets.token_urlsafe(48)
        self._repository.save_session(UserSession(
            token_hash=_digest(raw_token), user_id=user.id, created_at=now,
            expires_at=now + timedelta(days=max(1, self._settings.auth_session_days)),
            last_seen_at=now, revoked_at=None,
        ))
        return LoginResult(user, raw_token, login_state.next_path)

    def authenticate(self, raw_token: str | None) -> PlatformUser | None:
        if not raw_token:
            return None
        return self._repository.user_for_session(_digest(raw_token), datetime.now(UTC))

    def logout(self, raw_token: str | None) -> None:
        if raw_token:
            self._repository.revoke_session(_digest(raw_token), datetime.now(UTC))
