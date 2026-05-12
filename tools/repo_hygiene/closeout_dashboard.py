from __future__ import annotations

import argparse
import html
import json
import os
import re
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

from .brokered_closeout import (
    HygieneError,
    closeout_script_command,
    effective_closeout_script_command,
    load_closeout_config,
    repo_state_dashboard_spec,
    repo_state_ledger_config,
    repo_state_path,
    repo_state_snapshot,
    rollback_policy,
)
from .core import normalize_rel, resolve_repo_root, sha256_text


DASHBOARD_ACTIONS_SCHEMA = "closeout-dashboard-actions.v1"
DASHBOARD_ENDPOINTS_SCHEMA = "closeout-dashboard-endpoints.v1"
SAFE_HISTORY_ID = re.compile(r"^[A-Za-z0-9_.-]+(?:\.json)?$")


def dashboard_endpoints(config: Dict[str, Any]) -> Dict[str, str]:
    dashboard = repo_state_dashboard_spec(config)
    configured = dashboard.get("endpoints")
    if not isinstance(configured, dict):
        configured = {}
    return {
        "page": str(configured.get("page") or "/closeout"),
        "latest": str(configured.get("latest") or "/api/closeout/repo-state/latest"),
        "historyIndex": str(configured.get("historyIndex") or "/api/closeout/repo-state/history-index"),
        "historySnapshot": str(configured.get("historySnapshot") or "/api/closeout/repo-state/history/{snapshotId}"),
        "actions": str(configured.get("actions") or "/api/closeout/actions"),
        "events": str(configured.get("events") or "/api/closeout/events"),
    }


def dashboard_actions_payload(
    repo_root_arg: Path,
    *,
    server_process_id: Optional[int] = None,
) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    dashboard = repo_state_dashboard_spec(config)
    ledger = repo_state_ledger_config(config)
    rollback = rollback_policy(config)
    endpoints = dashboard_endpoints(config)
    refresh_command = effective_closeout_script_command(
        config,
        "write-repo-state.ps1",
        ["-Write", "-LatestOnly"],
        str(ledger.get("liveRefreshCommand") or dashboard.get("refreshCommand") or ""),
    )
    refresh_command_policy = str(
        ledger.get("refreshCommandPolicy")
        or dashboard.get("refreshCommandPolicy")
        or "repo-owned-write-repo-state-latest-only"
    )
    helper = dashboard.get("helper") if isinstance(dashboard.get("helper"), dict) else {}
    disallowed = list(rollback.get("disallowedDefaultActions") or [])
    for extra in dashboard.get("rollbackForbiddenActions") or []:
        if extra not in disallowed:
            disallowed.append(str(extra))

    return {
        "schema": DASHBOARD_ACTIONS_SCHEMA,
        "status": "ready",
        "serverProcessId": int(server_process_id if server_process_id is not None else os.getpid()),
        "repoRoot": str(repo_root),
        "repoRootHash": sha256_text(str(repo_root).casefold())[:16],
        "endpointsSchema": DASHBOARD_ENDPOINTS_SCHEMA,
        "endpoints": endpoints,
        "helper": {
            "scriptPath": str(helper.get("scriptPath") or "tools\\closeout\\start-closeout-dashboard.ps1"),
            "module": str(helper.get("module") or "tools.repo_hygiene.closeout_dashboard"),
            "host": str(helper.get("host") or "127.0.0.1"),
            "port": int(helper.get("port") or 8765),
            "reuseExistingForSameRepo": bool(helper.get("reuseExistingForSameRepo", True)),
            "serverProcessIdSource": str(helper.get("serverProcessIdSource") or endpoints["actions"]),
            "readinessEndpoint": str(helper.get("readinessEndpoint") or endpoints["actions"]),
            "staleAfterMs": int(helper.get("staleAfterMs") or 15000),
        },
        "dashboard": {
            "localUrl": str(dashboard.get("localUrl") or "http://127.0.0.1:8765/closeout"),
            "stickyUrlPath": str(dashboard.get("stickyUrlPath") or "/closeout"),
            "autoRefreshMs": int(dashboard.get("autoRefreshMs") or 5000),
            "refreshCommandPolicy": refresh_command_policy,
            "mutationModel": str(dashboard.get("mutationModel") or "symbolic-action-request-only"),
            "feedAuthority": str(dashboard.get("feedAuthority") or "latest-json-is-display-feed-only"),
            "duplicateLaunchPolicy": str(dashboard.get("duplicateLaunchPolicy") or "reuse-same-repo-fail-foreign-owner"),
            "preservedClientStateKeys": list(dashboard.get("preservedClientStateKeys") or []),
        },
        "symbolicActions": [
            {
                "id": "refresh_repo_state",
                "label": "Refresh repo state feed",
                "actionability": "generated-feed-only",
                "command": refresh_command,
                "commandPolicy": refresh_command_policy,
                "writesHistory": bool(ledger.get("liveRefreshWritesHistory", False)),
            },
            {
                "id": "request_rollback",
                "label": "Request rollback plan",
                "actionability": str(rollback.get("readinessDefaultActionability") or "read-only-no-actor"),
                "readinessReason": "rollback actor has not validated an immutable source snapshot and closeout-rollback-manifest.v1",
                "requiredManifestSchema": str(rollback.get("requiredManifestSchema") or "closeout-rollback-manifest.v1"),
                "requiredManifestFields": list(rollback.get("requiredManifestFields") or []),
                "requiresUserApproval": bool(rollback.get("requireUserApprovalForRollback", True)),
                "requiresImmutableSourceSnapshot": bool(rollback.get("requireImmutableSourceSnapshotForRollback", True)),
                "exactTupleRequired": ["targetHead", "sourceSnapshotHash", "policyHash", "plannedStrategy", "userApproval"],
            },
            {
                "id": "request_retained_remediation",
                "label": "Request retained-candidate remediation",
                "actionability": "symbolic-request-only",
                "command": closeout_script_command("remediate-retained-closeout.ps1", ["-Apply"], config),
                "exactTupleRequired": ["candidateId", "actionId", "evidenceHash", "policyHash", "pinnedRefs"],
                "requestOnlyReason": "repo-owned retained-remediation actor must revalidate the tuple before mutation",
            },
        ],
        "forbiddenActions": disallowed,
    }


def latest_repo_state_payload(repo_root_arg: Path) -> Dict[str, Any]:
    return repo_state_snapshot(repo_root_arg, write=True, latest_only=True)


def history_index_payload(repo_root_arg: Path) -> Dict[str, Any]:
    snapshot = repo_state_snapshot(repo_root_arg, write=False)
    history = snapshot.get("closeout", {}).get("history", {})
    state_ledger = snapshot.get("stateLedger", {})
    return {
        "schema": history.get("schema") or "closeout-history-index.v1",
        "status": "success",
        "historyRoot": state_ledger.get("historyRoot"),
        "entryCount": history.get("entryCount", 0),
        "workBlockCount": history.get("workBlockCount", 0),
        "skippedCount": history.get("skippedCount", 0),
        "errors": history.get("errors", []),
        "entries": history.get("entries", []),
        "recentWorkBlocks": history.get("recentWorkBlocks", []),
        "limit": history.get("limit"),
    }


def history_snapshot_payload(repo_root_arg: Path, snapshot_id: str) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    candidate = unquote(snapshot_id or "").strip()
    if not SAFE_HISTORY_ID.match(candidate):
        raise HygieneError("invalid history snapshot id")
    if not candidate.endswith(".json"):
        candidate = f"{candidate}.json"
    history_root = repo_state_path(repo_root, config, "historyRoot", ".claude-state/closeout/repo-state/history").resolve()
    path = (history_root / candidate).resolve()
    if history_root not in path.parents and path != history_root:
        raise HygieneError("history snapshot path escaped history root")
    if not path.exists():
        raise HygieneError("history snapshot not found: %s" % candidate)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    payload.setdefault("stateLedger", {})
    payload["stateLedger"]["servedHistorySnapshotId"] = candidate
    return payload


def dashboard_html(config: Dict[str, Any]) -> str:
    dashboard = repo_state_dashboard_spec(config)
    endpoints = dashboard_endpoints(config)
    title = "Closeout Dashboard"
    escaped_endpoints = html.escape(json.dumps(endpoints, sort_keys=True), quote=True)
    preserved_keys = list(dashboard.get("preservedClientStateKeys") or [])
    escaped_preserved_keys = html.escape(json.dumps(preserved_keys, sort_keys=True), quote=True)
    auto_refresh = int(dashboard.get("autoRefreshMs") or 5000)
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f7f8fb;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #5f6b7a;
      --line: #dce3ec;
      --accent: #146c94;
      --warn: #9a5a00;
      --ok: #177245;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #11151b;
        --panel: #181f27;
        --text: #edf3f8;
        --muted: #a6b3c0;
        --line: #2b3642;
        --accent: #65c7f7;
        --warn: #ffc15a;
        --ok: #7ee0a3;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 "Segoe UI", Arial, sans-serif;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 2;
      border-bottom: 1px solid var(--line);
      background: color-mix(in srgb, var(--panel) 92%, transparent);
      backdrop-filter: blur(12px);
      padding: 14px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    h1, h2 {{ margin: 0; letter-spacing: 0; }}
    h1 {{ font-size: 20px; }}
    h2 {{ font-size: 16px; margin-bottom: 10px; }}
    main {{ width: min(1200px, 100%); margin: 0 auto; padding: 18px; display: grid; gap: 14px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-width: 0;
    }}
    .full {{ grid-column: 1 / -1; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .chip {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 8px;
      color: var(--muted);
      background: transparent;
      white-space: nowrap;
    }}
    .chip.ok {{ color: var(--ok); }}
    .chip.warn {{ color: var(--warn); }}
    button {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 10px;
      color: var(--text);
      background: var(--panel);
      cursor: pointer;
    }}
    button:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px 6px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    code, pre {{ font-family: Consolas, "SFMono-Regular", monospace; }}
    pre {{ overflow: auto; max-height: 360px; margin: 0; color: var(--muted); }}
    .muted {{ color: var(--muted); }}
    @media (max-width: 780px) {{ .grid {{ grid-template-columns: 1fr; }} header {{ align-items: flex-start; flex-direction: column; }} }}
  </style>
</head>
<body data-endpoints=\"{escaped_endpoints}\" data-refresh-ms=\"{auto_refresh}\" data-preserved-client-state-keys=\"{escaped_preserved_keys}\">
  <header>
    <div>
      <h1>Closeout Dashboard</h1>
      <div class=\"muted\" id=\"subtitle\">Repo-owned state feed</div>
    </div>
    <div class=\"chips\">
      <span class=\"chip\" id=\"refresh-status\">Idle</span>
      <button id=\"refresh-button\" type=\"button\">Refresh</button>
    </div>
  </header>
  <main>
    <section class=\"grid\">
      <article class=\"panel\">
        <h2>Repo State</h2>
        <div class=\"chips\" id=\"repo-chips\"></div>
      </article>
      <article class=\"panel\">
        <h2>Rollback Readiness</h2>
        <div class=\"chips\" id=\"rollback-chips\"></div>
      </article>
      <article class=\"panel full\">
        <h2>Dirty Files</h2>
        <div id=\"dirty-table\" class=\"muted\">Loading...</div>
      </article>
      <article class=\"panel full\">
        <h2>Closeout History</h2>
        <div id=\"history-table\" class=\"muted\">Loading...</div>
      </article>
      <article class=\"panel full\">
        <h2>Actions</h2>
        <pre id=\"actions-json\">Loading...</pre>
      </article>
    </section>
  </main>
  <script>
    const endpoints = JSON.parse(document.body.dataset.endpoints);
    const refreshMs = Number(document.body.dataset.refreshMs || 5000);
    const preservedClientStateKeys = JSON.parse(document.body.dataset.preservedClientStateKeys || "[]");
    const stateKey = "mlv-closeout-dashboard-state";
    function byId(id) {{ return document.getElementById(id); }}
    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>\"']/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[c]));
    }}
    function chip(label, value, tone) {{ return `<span class=\"chip ${{tone || ""}}\">${{escapeHtml(label)}}: ${{escapeHtml(value)}}</span>`; }}
    function table(rows, columns) {{
      if(!rows.length) return '<span class="muted">None</span>';
      return `<table><thead><tr>${{columns.map(c => `<th>${{escapeHtml(c.label)}}</th>`).join("")}}</tr></thead><tbody>${{rows.map(row => `<tr>${{columns.map(c => `<td>${{escapeHtml(row[c.key] ?? "")}}</td>`).join("")}}</tr>`).join("")}}</tbody></table>`;
    }}
    async function getJson(path) {{
      const response = await fetch(path, {{cache: "no-store"}});
      if(!response.ok) throw new Error(`${{response.status}} ${{response.statusText}}`);
      return response.json();
    }}
    function configuredClientState() {{
      const stored = JSON.parse(localStorage.getItem(stateKey) || "{{}}");
      const state = {{}};
      for (const key of preservedClientStateKeys) {{
        if (key === "scrollPosition") state.scrollPosition = {{x: window.scrollX, y: window.scrollY}};
        else if (key === "focusedElement") state.focusedElement = document.activeElement && document.activeElement.id || "";
        else if (key === "selectedWorkBlockId") state.selectedWorkBlockId = stored.selectedWorkBlockId || "";
        else if (key === "expandedRows") state.expandedRows = Array.isArray(stored.expandedRows) ? stored.expandedRows : [];
        else if (key === "activeHistoryFilters") state.activeHistoryFilters = stored.activeHistoryFilters && typeof stored.activeHistoryFilters === "object" ? stored.activeHistoryFilters : {{}};
      }}
      state.scrollY = window.scrollY;
      state.focusedId = document.activeElement && document.activeElement.id || "";
      return state;
    }}
    function saveClientState() {{
      localStorage.setItem(stateKey, JSON.stringify(configuredClientState()));
    }}
    function restoreClientState() {{
      try {{
        const stored = JSON.parse(localStorage.getItem(stateKey) || "{{}}");
        const focused = stored.focusedElement || stored.focusedId;
        if(focused && byId(focused)) byId(focused).focus({{preventScroll: true}});
        const scroll = stored.scrollPosition && Number.isFinite(stored.scrollPosition.y) ? stored.scrollPosition.y : stored.scrollY;
        if(Number.isFinite(scroll)) window.scrollTo({{top: scroll, behavior: "instant"}});
      }} catch(_err) {{}}
    }}
    async function refresh() {{
      saveClientState();
      byId("refresh-status").textContent = "Refreshing";
      try {{
        const [latest, history, actions] = await Promise.all([
          getJson(endpoints.latest),
          getJson(endpoints.historyIndex),
          getJson(endpoints.actions)
        ]);
        byId("subtitle").textContent = latest.repo.root;
        const branch = latest.branch || {{}};
        byId("repo-chips").innerHTML = [
          chip("branch", branch.currentBranch || "detached"),
          chip("head", String(branch.head || "").slice(0, 12)),
          chip("dirty", latest.dirty.clean ? "clean" : latest.dirty.entryCount, latest.dirty.clean ? "ok" : "warn"),
          chip("worktrees", (latest.worktrees || []).length),
          chip("stashes", (latest.stashes || []).length)
        ].join("");
        const readiness = ((latest.rollback || {{}}).readiness || {{}});
        byId("rollback-chips").innerHTML = [
          chip("actionability", readiness.actionability || "unknown", readiness.evidenceFresh ? "ok" : "warn"),
          chip("evidence", readiness.evidenceStatus || "unknown"),
          chip("latest feed evidence", readiness.latestFeedIsRollbackEvidence ? "yes" : "no")
        ].join("");
        byId("dirty-table").innerHTML = table(latest.dirty.entries || [], [
          {{key:"xy", label:"Status"}},
          {{key:"path", label:"Path"}}
        ]);
        byId("history-table").innerHTML = table(history.entries || [], [
          {{key:"workBlockId", label:"Work block"}},
          {{key:"latestAuditType", label:"Latest audit"}},
          {{key:"latestOutcome", label:"Outcome"}},
          {{key:"latestSeenAt", label:"Seen"}}
        ]);
        byId("actions-json").textContent = JSON.stringify(actions, null, 2);
        byId("refresh-status").textContent = "Updated " + new Date().toLocaleTimeString();
        restoreClientState();
      }} catch(error) {{
        byId("refresh-status").textContent = "Error";
        byId("actions-json").textContent = String(error);
      }}
    }}
    let pollingTimer = null;
    function startPolling() {{
      if(!pollingTimer) pollingTimer = window.setInterval(refresh, refreshMs);
    }}
    function startEventStream() {{
      if(!("EventSource" in window) || !endpoints.events) {{
        startPolling();
        return;
      }}
      try {{
        const source = new EventSource(endpoints.events);
        source.addEventListener("ready", refresh);
        source.addEventListener("repo-state", refresh);
        source.onerror = () => {{
          source.close();
          startPolling();
        }};
      }} catch(_err) {{
        startPolling();
      }}
    }}
    byId("refresh-button").addEventListener("click", refresh);
    window.addEventListener("beforeunload", saveClientState);
    refresh();
    startEventStream();
  </script>
</body>
</html>"""


class CloseoutDashboardHandler(BaseHTTPRequestHandler):
    server: "CloseoutDashboardServer"

    def _write_json(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_html(self, payload: str) -> None:
        data = payload.encode("utf-8")
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_error(self, status: HTTPStatus, message: str) -> None:
        self._write_json({"status": "error", "error": message}, status=status)

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        endpoints = dashboard_endpoints(self.server.config)
        try:
            if path in {"/", endpoints["page"].rstrip("/")}:
                self._write_html(dashboard_html(self.server.config))
                return
            if path == endpoints["latest"]:
                self._write_json(latest_repo_state_payload(self.server.repo_root))
                return
            if path == endpoints["historyIndex"]:
                self._write_json(history_index_payload(self.server.repo_root))
                return
            if path == endpoints["actions"]:
                self._write_json(dashboard_actions_payload(self.server.repo_root))
                return
            prefix = endpoints["historySnapshot"].split("{snapshotId}", 1)[0].rstrip("/")
            if path.startswith(prefix + "/"):
                snapshot_id = path[len(prefix) + 1 :]
                self._write_json(history_snapshot_payload(self.server.repo_root, snapshot_id))
                return
            if path == endpoints["events"]:
                self.send_response(HTTPStatus.OK.value)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                interval = max(1.0, float(repo_state_dashboard_spec(self.server.config).get("autoRefreshMs") or 5000) / 1000.0)
                ready_payload = json.dumps({"status": "ready", "endpoint": endpoints["latest"]}, sort_keys=True)
                self.wfile.write(f"event: ready\ndata: {ready_payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                try:
                    while True:
                        time.sleep(interval)
                        payload = json.dumps(
                            {"status": "tick", "endpoint": endpoints["latest"], "serverProcessId": os.getpid()},
                            sort_keys=True,
                        )
                        self.wfile.write(f"event: repo-state\ndata: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
                return
            self._write_error(HTTPStatus.NOT_FOUND, "unknown closeout dashboard route")
        except HygieneError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # pragma: no cover - final fail-closed boundary
            self._write_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return


class CloseoutDashboardServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], repo_root: Path) -> None:
        super().__init__(server_address, handler_class)
        self.repo_root = resolve_repo_root(repo_root)
        self.config = load_closeout_config(self.repo_root)


def run_server(repo_root: Path, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise HygieneError("closeout dashboard may only bind a localhost address")
    server = CloseoutDashboardServer((host, int(port)), CloseoutDashboardHandler, repo_root)
    print(json.dumps(dashboard_actions_payload(server.repo_root, server_process_id=os.getpid()), indent=2, sort_keys=True), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the read-only closeout dashboard.")
    parser.add_argument("--repo-root", default=".", help="Path inside the target Git repo.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        run_server(Path(args.repo_root), host=args.host, port=args.port)
        return 0
    except HygieneError as exc:
        print("closeout dashboard error: %s" % exc, flush=True)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
