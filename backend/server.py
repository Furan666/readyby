"""Local review client and an actual MCP Streamable HTTP endpoint."""
from typing import Any
import json
import secrets
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from backend.store import Store, StaleDraft

ROOT = Path(__file__).resolve().parent.parent


def create_app(db_path=None, port=8877):
    store = Store(db_path or ROOT / "data" / "household.sqlite3")
    review_token = secrets.token_urlsafe(32)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{host}" for host in allowed_hosts}
    mcp = MCPServer("ReadyBy", version="0.1.0", instructions=(
        "Help recover a shared departure plan from user-reported facts. Read first, then draft changes. "
        "Drafts never alter the active household. Show assumptions and choices; the separate human review UI applies them. "
        "INFEASIBLE is a proved deadline conflict; UNKNOWN is not. This is a local fictional demo, not an Alexa+ connection."))

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(read_only_hint=True))
    def read_departure() -> dict[str, Any]:
        """Read saved progress, a resource-feasible plan, and any proven deadline conflict."""
        return store.view()

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    def draft_update(changes: dict) -> dict[str, Any]:
        """Preview reported time/progress/blocks or explicit duration/label edits. Does not commit. Only now, deadline, completed, blocks, task_changes are accepted."""
        return store.draft(changes)

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    def recovery_choices() -> dict[str, Any]:
        """Compare household-provided alternatives for a proven deadline conflict, without applying either."""
        return store.recovery_choices()

    @mcp.custom_route("/api/session", methods=["GET"])
    async def session(request: Request):
        return JSONResponse({"review_token": review_token}, headers={"Cache-Control": "no-store"})

    async def human_action(request, action):
        # Local same-origin CSRF protection, not proof of human presence or multi-user authentication.
        if not secrets.compare_digest(request.headers.get("X-ReadyBy-Token", "").encode("utf-8"), review_token.encode("ascii")):
            return JSONResponse({"error": "Reload this local review client first."}, status_code=403)
        try:
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 32768:
                    return JSONResponse({"error": "Request too large."}, status_code=413)
                body.extend(chunk)
            if action == "reset":
                return JSONResponse(store.reset())
            data = json.loads(body)
            if type(data) is not dict or set(data) != {"draft_id", "base_revision"}:
                raise ValueError("Review an exact draft before applying it.")
            return JSONResponse(store.commit(data["draft_id"], data["base_revision"]))
        except StaleDraft as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @mcp.custom_route("/api/commit", methods=["POST"])
    async def commit(request: Request):
        return await human_action(request, "commit")

    @mcp.custom_route("/api/reset", methods=["POST"])
    async def reset(request: Request):
        return await human_action(request, "reset")

    for route, filename in (("/", "index.html"), ("/app.js", "app.js"), ("/style.css", "style.css")):
        def handler(name):
            async def serve(request: Request):
                return FileResponse(ROOT / "web" / name, headers={"Cache-Control": "no-store"})
            return serve
        mcp.custom_route(route, methods=["GET"])(handler(filename))

    class LocalOnly(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.headers.get("host") not in allowed_hosts:
                return JSONResponse({"error": "Local host required."}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin not in allowed_origins:
                return JSONResponse({"error": "Same local origin required."}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"error": "Cross-site request refused."}, status_code=403)
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > 32768:
                return JSONResponse({"error": "Request too large."}, status_code=413)
            response = await call_next(request)
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

    app = mcp.streamable_http_app(json_response=True, stateless_http=True, max_request_body_size=32768,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=list(allowed_hosts), allowed_origins=list(allowed_origins)))
    app.add_middleware(LocalOnly)
    app.state.store = store
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=8877, log_level="warning")
