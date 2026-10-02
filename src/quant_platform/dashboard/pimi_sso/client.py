from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class SsoUnavailable(Exception):
    pass


def safe_path(value: str) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        return "/"
    decoded = unquote(value)
    if (not value.startswith("/") or decoded.startswith("//") or "\\" in decoded
            or any(ord(c) < 32 for c in decoded) or urlsplit(value).netloc):
        return "/"
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SsoClient:
    def __init__(self, config_path: Path, transport=None):
        self.enabled = config_path.is_file()
        self.configured = False
        self.transport = transport
        self.config = {}
        self._opener = urllib.request.build_opener(_NoRedirect(), urllib.request.ProxyHandler({}))
        if not self.enabled:
            return
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
            if config.get("enabled") is False:
                self.enabled = False
                return
            self.config = config
            self.app_id = config["app_id"]
            self.secret = config["secret"]
            self.home_url = config["home_url"].rstrip("/")
            self.public_url = config["public_url"].rstrip("/")
            self.internal_url = config.get("internal_url", "http://127.0.0.1:5003").rstrip("/")
            self.secure = not config.get("insecure_test", False)
            for value in (self.home_url, self.public_url):
                parsed = urlsplit(value)
                valid_scheme = (parsed.scheme == "https" if self.secure else
                                parsed.scheme == "http" and parsed.hostname in LOOPBACK)
                if not valid_scheme or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
                    raise ValueError("Invalid public origin")
            if self.secure and urlsplit(self.home_url).hostname == urlsplit(self.public_url).hostname:
                raise ValueError("Home and apps need distinct hostnames for cookie isolation")
            internal = urlsplit(self.internal_url)
            if (not (internal.scheme == "https" or internal.scheme == "http" and internal.hostname in LOOPBACK)
                    or not internal.hostname or internal.path or internal.query or internal.fragment or internal.username):
                raise ValueError("Back channel requires loopback HTTP or verified HTTPS")
            if internal.scheme == "https":
                context = ssl.create_default_context(cafile=config.get("ca_file") or None)
                self._opener = urllib.request.build_opener(_NoRedirect(), urllib.request.ProxyHandler({}),
                                                           urllib.request.HTTPSHandler(context=context))
            if not re.fullmatch(r"[a-z0-9-]+", self.app_id) or len(self.secret) < 32:
                raise ValueError("Invalid client credentials")
            self.cookie_name = ("__Host-rp_" if self.secure else "rp_test_") + self.app_id
            self.flow_cookie = self.cookie_name + "_flow"
            self.callback = self.public_url + "/auth/home/callback"
            self.configured = True
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # An existing malformed configuration must fail closed, not restore legacy access.
            self.configured = False

    def _sign(self, value: dict) -> str:
        body = base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()
        return body + "." + hmac.new(self.secret.encode(), body.encode(), "sha256").hexdigest()

    def _unsign(self, raw: str) -> dict | None:
        try:
            if len(raw) > 12000:
                return None
            body, signature = raw.rsplit(".", 1)
            expected = hmac.new(self.secret.encode(), body.encode(), "sha256").hexdigest()
            if not hmac.compare_digest(signature, expected):
                return None
            value = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
            return value if isinstance(value, dict) and value.get("expires_at", 0) > time.time() else None
        except (ValueError, TypeError, KeyError):
            return None

    def post(self, path: str, form: dict) -> dict:
        if not self.configured:
            raise SsoUnavailable("Home SSO configuration is incomplete")
        if self.transport:
            return self.transport(path, form)
        basic = base64.b64encode((self.app_id + ":" + self.secret).encode()).decode()
        req = urllib.request.Request(self.internal_url + path, data=urlencode(form).encode(), method="POST",
                                     headers={"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self._opener.open(req, timeout=2.5) as response:
                payload = json.loads(response.read(128000))
            if not isinstance(payload, dict):
                raise ValueError("Invalid response")
            return payload
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise SsoUnavailable("Home cannot verify this session") from exc

    def begin(self, path: str) -> tuple[str, str]:
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        flow = self._sign({"state": state, "verifier": verifier, "next": safe_path(path), "expires_at": int(time.time()) + 300})
        location = self.home_url + "/sso/authorize?" + urlencode({"client_id": self.app_id, "redirect_uri": self.callback,
            "response_type": "code", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
        return location, flow

    def finish(self, raw_flow: str, state: str, code: str) -> tuple[str, str, int] | None:
        flow = self._unsign(raw_flow)
        if not flow or not state or not hmac.compare_digest(state, flow.get("state", "")) or not 32 <= len(code) <= 128:
            return None
        result = self.post("/sso/token", {"grant_type": "authorization_code", "code": code,
                                        "redirect_uri": self.callback, "code_verifier": flow["verifier"]})
        if not result.get("access_token") or result.get("token_type") != "Bearer":
            return None
        return result["access_token"], safe_path(flow["next"]), int(result["expires_in"])

    def identity(self, token: str) -> dict | None:
        if not 32 <= len(token) <= 128:
            return None
        result = self.post("/sso/introspect", {"token": token})
        return result if result.get("active") is True else None

    def service_identity(self, authorization: str, path: str, method: str):
        if not authorization.startswith("Bearer rp_sk_") or len(authorization) > 150:
            return None
        result = self.post("/sso/service-introspect", {"token": authorization[7:], "path": path, "method": method})
        return result if result.get("active") is True else None

    def users(self) -> list[dict]:
        return self.post("/sso/users", {}).get("users", [])


def is_loopback(remote: str, host: str) -> bool:
    try:
        return ipaddress.ip_address(remote).is_loopback and urlsplit("//" + host).hostname in LOOPBACK
    except ValueError:
        return False


def register_flask(app, config_path: Path, identity_mapper, *, health_paths=("/health",), allow_loopback=False):
    """Install before the legacy gate. Identity mapping preserves application roles/data keys."""
    from flask import g, jsonify, redirect, request

    client = SsoClient(config_path)
    app.extensions["home_sso_client"] = client

    def set_cookie(response, name, value, ttl):
        response.set_cookie(name, value, max_age=ttl, secure=client.secure, httponly=True, samesite="Lax", path="/")

    @app.before_request
    def home_sso_gate():
        g.home_sso_active = False
        if not client.enabled:
            return None
        g.home_sso_active = True
        if not client.configured:
            return jsonify(error="Home 統一登入設定不完整。"), 503
        local = is_loopback(request.remote_addr or "", request.host)
        if local and request.path in health_paths:
            return None
        if allow_loopback and local and not request.headers.get("Cf-Access-Jwt-Assertion"):
            origin = request.headers.get("Origin", request.host_url.rstrip("/"))
            if origin.rstrip("/") == request.host_url.rstrip("/"):
                g.home_sso_active = False
                return None
        if request.host != urlsplit(client.public_url).netloc:
            return jsonify(error="不接受此主機名稱。"), 400
        if request.path in {"/auth/home/logout", "/cdn-cgi/access/logout"}:
            return redirect(client.home_url + "/logout", code=303)
        if request.path == "/auth/home/callback":
            if request.method != "GET":
                return jsonify(error="無效的登入回應。"), 400
            try:
                result = client.finish(request.cookies.get(client.flow_cookie, ""), request.args.get("state", ""), request.args.get("code", ""))
            except SsoUnavailable:
                return jsonify(error="登入服務暫時無法驗證，請稍後再試。"), 503
            if not result:
                return jsonify(error="登入要求已過期或驗證失敗，請由 Home 重新登入。"), 400
            response = redirect(result[1], code=303)
            set_cookie(response, client.cookie_name, result[0], result[2])
            response.delete_cookie(client.flow_cookie, secure=client.secure, httponly=True, samesite="Lax", path="/")
            return response
        machine = bool(request.headers.get("Authorization"))
        try:
            identity = (client.service_identity(request.headers["Authorization"], request.path, request.method)
                        if machine else client.identity(request.cookies.get(client.cookie_name, "")))
        except SsoUnavailable:
            return jsonify(error="登入服務暫時無法驗證，請稍後再試。"), 503
        if not identity:
            if machine or request.path.startswith("/api/") or request.method not in {"GET", "HEAD"}:
                return jsonify(error="請先由 Home 登入。", login_url=client.public_url + "/auth/home/login"), 401
            location, flow = client.begin("/" if request.path in {"/auth/home/login", "/login"} else request.full_path.rstrip("?"))
            response = redirect(location)
            set_cookie(response, client.flow_cookie, flow, 300)
            return response
        if not machine and request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("Origin", client.public_url).rstrip("/") != client.public_url:
                return jsonify(error="不允許跨來源寫入。"), 403
            if request.mimetype != "application/json" and not request.headers.get("X-Requested-With") and request.headers.get("Origin") != client.public_url:
                return jsonify(error="請由應用程式送出此操作。"), 403
        mapped = identity_mapper(identity)
        if mapped is False:
            return jsonify(error="此帳號沒有系統使用權限。"), 403
        g.home_sso_user = identity
        return None

    @app.after_request
    def home_sso_headers(response):
        if client.enabled:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
        return response
    return client
