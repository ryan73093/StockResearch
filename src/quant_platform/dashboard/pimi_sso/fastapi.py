"""Optional Starlette/FastAPI adapter; imported only by FastAPI applications."""
from urllib.parse import urlsplit

from .client import SsoUnavailable, is_loopback


async def gate(client, request, call_next, identity_mapper, *, health_paths=("/health",)):
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse, RedirectResponse

    def cookie(response, name, value, ttl):
        response.set_cookie(name, value, max_age=ttl, secure=client.secure, httponly=True, samesite="lax", path="/")

    async def run():
        if not client.configured:
            return JSONResponse({"error": "Home 統一登入設定不完整。"}, status_code=503)
        path, host = request.url.path, request.headers.get("host", "")
        remote = request.client.host if request.client else ""
        if is_loopback(remote, host) and path in health_paths:
            return await call_next(request)
        if host != urlsplit(client.public_url).netloc:
            return JSONResponse({"error": "不接受此主機名稱。"}, status_code=400)
        if path in {"/auth/home/logout", "/auth/logout", "/cdn-cgi/access/logout"}:
            return RedirectResponse(client.home_url + "/logout", status_code=303)
        if path == "/auth/home/callback":
            if request.method != "GET":
                return JSONResponse({"error": "無效的登入回應。"}, status_code=400)
            try:
                result = await run_in_threadpool(client.finish, request.cookies.get(client.flow_cookie, ""),
                                                request.query_params.get("state", ""), request.query_params.get("code", ""))
            except SsoUnavailable:
                return JSONResponse({"error": "Home 暫時無法完成驗證。"}, status_code=503)
            response = RedirectResponse(result[1] if result else client.public_url + "/auth/home/login", status_code=303)
            if result:
                cookie(response, client.cookie_name, result[0], result[2])
            else:
                response = JSONResponse({"error": "登入回應已過期或無效。"}, status_code=400)
            response.delete_cookie(client.flow_cookie, secure=client.secure, httponly=True, samesite="lax", path="/")
            return response
        authorization = request.headers.get("authorization", "")
        try:
            identity = (await run_in_threadpool(client.service_identity, authorization, path, request.method)
                        if authorization else await run_in_threadpool(client.identity, request.cookies.get(client.cookie_name, "")))
        except SsoUnavailable:
            return JSONResponse({"error": "Home 暫時無法驗證登入，請稍後再試。"}, status_code=503)
        if not identity:
            if authorization or path.startswith("/api/") or request.method not in {"GET", "HEAD"}:
                return JSONResponse({"error": "請先由 Home 登入。", "login_url": client.public_url + "/auth/home/login"}, status_code=401)
            next_path = "/" if path in {"/login", "/auth/home/login"} else path + ("?" + request.url.query if request.url.query else "")
            location, flow = client.begin(next_path)
            response = RedirectResponse(location, status_code=302)
            cookie(response, client.flow_cookie, flow, 300)
            return response
        if not authorization and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != client.public_url:
                return JSONResponse({"error": "不允許跨來源寫入。"}, status_code=403)
            content_type = request.headers.get("content-type", "").split(";")[0]
            if content_type != "application/json" and not request.headers.get("x-requested-with") and origin != client.public_url:
                return JSONResponse({"error": "請由應用程式送出此操作。"}, status_code=403)
        if await run_in_threadpool(identity_mapper, request, identity) is False:
            return JSONResponse({"error": "此帳號沒有系統使用權限。"}, status_code=403)
        request.state.home_sso_user = identity
        return await call_next(request)

    response = await run()
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "same-origin",
                             "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"})
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' https: data:; media-src 'self'; "
        "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    return response
