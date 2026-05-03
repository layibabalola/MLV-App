import json
import secrets
import threading
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from agent_bridge import AgentBridge, utc_now


LOCAL_DASHBOARD_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class DashboardServerHandle:
    server: ThreadingHTTPServer
    thread: threading.Thread
    url: str
    token: str
    csrf_token: str

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def generate_dashboard_token() -> str:
    return secrets.token_urlsafe(32)


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, status: int, body: str) -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
    handler.end_headers()
    handler.wfile.write(data)


class BridgeDashboardHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address,
        RequestHandlerClass,
        *,
        bridge: AgentBridge,
        token: str,
        csrf_token: str,
        default_agent: str,
        default_project: Optional[str],
    ) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.bridge = bridge
        self.token = token
        self.csrf_token = csrf_token
        self.default_agent = default_agent
        self.default_project = default_project


class BridgeDashboardHandler(BaseHTTPRequestHandler):
    server: BridgeDashboardHTTPServer

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _reject_if_not_local(self) -> bool:
        host = str(self.client_address[0])
        if host in {"127.0.0.1", "::1"}:
            return False
        _json_response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "dashboard is localhost-only"})
        return True

    def _query(self) -> Dict[str, Any]:
        return {k: v[-1] for k, v in parse_qs(urlparse(self.path).query).items() if v}

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        bearer = header[len("Bearer ") :].strip() if header.startswith("Bearer ") else ""
        alt = self.headers.get("X-Bridge-Token", "").strip()
        query_token = str(self._query().get("token") or "").strip()
        return secrets.compare_digest(bearer, self.server.token) or secrets.compare_digest(
            alt, self.server.token
        ) or secrets.compare_digest(query_token, self.server.token)

    def _require_auth(self) -> bool:
        if self._reject_if_not_local():
            return False
        if self._authorized():
            return True
        _json_response(self, HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "missing or invalid dashboard token"})
        return False

    def _require_csrf(self) -> bool:
        header = self.headers.get("X-CSRF-Token", "").strip()
        if secrets.compare_digest(header, self.server.csrf_token):
            return True
        _json_response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "missing or invalid CSRF token"})
        return False

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        if length > 64 * 1024:
            raise ValueError("request body too large")
        payload = self.rfile.read(length).decode("utf-8")
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    def _agent_project(self) -> tuple[str, Optional[str]]:
        query = self._query()
        agent = str(query.get("agent") or self.server.default_agent)
        project = query.get("project") or self.server.default_project
        return agent, str(project) if project else None

    def do_GET(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        parsed = urlparse(self.path)
        agent, project = self._agent_project()
        if parsed.path == "/api/overview":
            result = self.server.bridge.dashboard_overview(agent=agent, project=project)
            _json_response(self, HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST, {
                "ok": result.ok,
                "status": result.status,
                "message": result.message,
                "data": result.data,
                "csrf_token": self.server.csrf_token,
            })
            return
        if parsed.path in {"", "/"}:
            result = self.server.bridge.dashboard_overview(agent=agent, project=project, format="markdown")
            markdown = result.data.get("markdown", "") if result.ok else result.message
            body = (
                "<!doctype html><html><head><meta charset=\"utf-8\">"
                "<meta name=\"csrf-token\" content=\"%s\">"
                "<title>Agent Bridge Dashboard</title>"
                "<style>body{font-family:ui-monospace,monospace;margin:2rem;max-width:70rem}"
                "pre{white-space:pre-wrap;border:1px solid #ddd;padding:1rem}</style></head>"
                "<body><h1>Agent Bridge Dashboard</h1><p>Local authenticated bridge admin surface.</p>"
                "<pre>%s</pre></body></html>"
            ) % (self.server.csrf_token, markdown.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            _html_response(self, HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST, body)
            return
        _json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._require_auth() or not self._require_csrf():
            return
        parsed = urlparse(self.path)
        try:
            payload = self._read_json_body()
        except (ValueError, json.JSONDecodeError) as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/api/revoke":
            result = self.server.bridge.revoke_contract(
                link_id=str(payload.get("link_id") or ""),
                project=str(payload.get("project") or self.server.default_project or ""),
                agent=str(payload.get("agent") or self.server.default_agent),
                session_id=payload.get("session_id"),
                reason=payload.get("reason"),
                source="dashboard",
                confirm_revoke=bool(payload.get("confirm_revoke")),
            )
        elif parsed.path == "/api/renew":
            result = self.server.bridge.renew_contract(
                link_id=str(payload.get("link_id") or ""),
                project=str(payload.get("project") or self.server.default_project or ""),
                agent=str(payload.get("agent") or self.server.default_agent),
                ttl_minutes=int(payload.get("ttl_minutes") or 120),
                session_id=payload.get("session_id"),
                source="dashboard",
                confirm_renew=bool(payload.get("confirm_renew")),
            )
        elif parsed.path == "/api/alias":
            result = self.server.bridge.rename_local_alias(
                link_id=str(payload.get("link_id") or ""),
                project=str(payload.get("project") or self.server.default_project or ""),
                agent=str(payload.get("agent") or self.server.default_agent),
                alias=str(payload.get("alias") or ""),
                source="dashboard",
            )
        else:
            _json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        status = HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST
        if result.status == "confirmation_required":
            status = HTTPStatus.CONFLICT
        _json_response(self, status, {"ok": result.ok, "status": result.status, "message": result.message, "data": result.data})


def start_dashboard_server(
    bridge: AgentBridge,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    token: Optional[str] = None,
    csrf_token: Optional[str] = None,
    default_agent: str = "codex",
    default_project: Optional[str] = None,
) -> DashboardServerHandle:
    if host not in LOCAL_DASHBOARD_HOSTS:
        raise ValueError("dashboard may only bind localhost, 127.0.0.1, or ::1")
    resolved_token = token or generate_dashboard_token()
    resolved_csrf = csrf_token or secrets.token_urlsafe(24)
    server = BridgeDashboardHTTPServer(
        (host, int(port)),
        BridgeDashboardHandler,
        bridge=bridge,
        token=resolved_token,
        csrf_token=resolved_csrf,
        default_agent=default_agent,
        default_project=default_project,
    )
    thread = threading.Thread(target=server.serve_forever, name="agent-bridge-dashboard", daemon=True)
    thread.start()
    actual_host, actual_port = server.server_address[:2]
    bridge._audit(
        {
            "id": str(uuid.uuid4()),
            "timestamp": utc_now(),
            "action": "dashboard_started",
            "accepted": True,
            "bind_host": actual_host,
            "port": actual_port,
            "auth_mode": "bearer_token",
            "csrf": True,
        }
    )
    return DashboardServerHandle(
        server=server,
        thread=thread,
        url="http://%s:%s" % (actual_host, actual_port),
        token=resolved_token,
        csrf_token=resolved_csrf,
    )
