import json
import secrets
import threading
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from agent_bridge import AgentBridge, BridgeResult, utc_now
from compact import reap_stale_server_pids
from core.paths import watcher_config_path_for_state_dir
from recover_bridge_session import inspect_bridge_runtime, recover_bridge_session


LOCAL_DASHBOARD_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class DashboardServerHandle:
    server: ThreadingHTTPServer
    thread: threading.Thread
    url: str
    token: str
    csrf_token: str

    @property
    def shutdown_requested(self) -> bool:
        return bool(getattr(self.server, "shutdown_requested", threading.Event()).is_set())

    def stop(self) -> None:
        if hasattr(self.server, "shutdown_requested"):
            self.server.shutdown_requested.set()
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
    handler.send_header(
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'",
    )
    handler.end_headers()
    handler.wfile.write(data)


def _html_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _script_json(value: Any) -> str:
    return json.dumps(value).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _dashboard_html(
    *,
    token: str,
    csrf_token: str,
    project: Optional[str],
    initial_payload: Dict[str, Any],
) -> str:
    template = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="csrf-token" content="__DASHBOARD_CSRF_META__">
  <title>Agent Bridge Dashboard</title>
  <style>
    :root{
      --bg:#07101b;
      --bg-elevated:#0d1a2d;
      --panel:#0f1d33cc;
      --panel-strong:#132440;
      --panel-soft:#101a2acc;
      --text:#eef5ff;
      --muted:#9cb0cb;
      --muted-strong:#bfd0e8;
      --line:rgba(173, 190, 215, .16);
      --brand:#6fd3ff;
      --brand-strong:#2cc4ff;
      --accent:#7ff7c4;
      --success:#70df9f;
      --warning:#ffbf63;
      --danger:#ff758f;
      --info:#9bb5ff;
      --shadow:0 22px 60px rgba(3, 8, 18, .34);
      --radius-xl:28px;
      --radius-lg:22px;
      --radius-md:16px;
      --radius-sm:12px;
      --mono:"IBM Plex Mono","Cascadia Code","Consolas","SFMono-Regular",monospace;
      --sans:"Space Grotesk","Segoe UI Variable Display","Aptos","Trebuchet MS",sans-serif;
    }
    *{box-sizing:border-box}
    html,body{margin:0;min-height:100%}
    body{
      font-family:var(--sans);
      color:var(--text);
      background:
        radial-gradient(circle at 8% 0%, rgba(111, 211, 255, .22), transparent 28%),
        radial-gradient(circle at 92% 6%, rgba(127, 247, 196, .14), transparent 24%),
        linear-gradient(180deg, #06101d 0%, #091426 45%, #07111d 100%);
      letter-spacing:.01em;
    }
    body::before{
      content:"";
      position:fixed;
      inset:0;
      pointer-events:none;
      background-image:
        linear-gradient(rgba(255,255,255,.03) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,.03) 1px, transparent 1px);
      background-size:32px 32px;
      mask-image:linear-gradient(180deg, rgba(0,0,0,.32), transparent 78%);
      opacity:.18;
    }
    a{color:inherit}
    button,input{
      font:inherit;
    }
    .shell{
      position:relative;
      max-width:1520px;
      margin:0 auto;
      padding:28px clamp(18px, 3vw, 40px) 40px;
    }
    .topbar{
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:24px;
      margin-bottom:20px;
    }
    .brand{
      max-width:820px;
    }
    .eyebrow{
      display:inline-flex;
      align-items:center;
      gap:8px;
      padding:8px 12px;
      border-radius:999px;
      background:rgba(111, 211, 255, .12);
      border:1px solid rgba(111, 211, 255, .2);
      color:var(--brand);
      font-size:.82rem;
      text-transform:uppercase;
      letter-spacing:.14em;
    }
    .eyebrow::before{
      content:"";
      width:8px;
      height:8px;
      border-radius:50%;
      background:linear-gradient(180deg, var(--accent), var(--brand));
      box-shadow:0 0 16px rgba(111, 211, 255, .55);
    }
    h1{
      margin:18px 0 10px;
      font-size:clamp(2.25rem, 4vw, 4rem);
      line-height:.96;
      letter-spacing:-.045em;
    }
    .lead{
      margin:0;
      max-width:72ch;
      color:var(--muted-strong);
      font-size:1.02rem;
      line-height:1.7;
    }
    .header-actions{
      display:flex;
      flex-wrap:wrap;
      justify-content:flex-end;
      gap:12px;
      min-width:min(100%, 360px);
    }
    .button{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      gap:10px;
      min-height:46px;
      padding:0 18px;
      border-radius:999px;
      border:1px solid transparent;
      background:rgba(255, 255, 255, .08);
      color:var(--text);
      cursor:pointer;
      transition:transform .18s ease, background .18s ease, border-color .18s ease, opacity .18s ease;
      box-shadow:0 10px 24px rgba(3, 8, 18, .18);
    }
    .button:hover:not(:disabled){
      transform:translateY(-1px);
      background:rgba(255, 255, 255, .12);
    }
    .button[data-busy="true"]{
      box-shadow:0 0 0 1px rgba(111, 211, 255, .18), 0 10px 24px rgba(3, 8, 18, .18);
    }
    .button[data-busy="true"]::after{
      content:"";
      width:10px;
      height:10px;
      border-radius:50%;
      background:currentColor;
      opacity:.28;
      box-shadow:0 0 0 6px rgba(255,255,255,.06);
    }
    .button:disabled{
      opacity:.48;
      cursor:not-allowed;
      transform:none;
    }
    .button-primary{
      background:linear-gradient(135deg, rgba(44, 196, 255, .94), rgba(127, 247, 196, .82));
      color:#04111f;
      font-weight:700;
    }
    .button-secondary{
      border-color:rgba(111, 211, 255, .26);
      background:rgba(111, 211, 255, .08);
    }
    .button-danger{
      border-color:rgba(255, 117, 143, .32);
      background:rgba(255, 117, 143, .08);
    }
    .status-banner{
      display:flex;
      align-items:center;
      gap:10px;
      margin-bottom:18px;
      padding:14px 18px;
      border-radius:18px;
      background:rgba(11, 20, 35, .78);
      border:1px solid var(--line);
      color:var(--muted-strong);
      backdrop-filter:blur(12px);
    }
    .status-banner::before{
      content:"";
      width:10px;
      height:10px;
      border-radius:50%;
      background:var(--brand);
      box-shadow:0 0 18px rgba(111, 211, 255, .45);
      flex:none;
    }
    .dashboard-root{
      display:grid;
      gap:18px;
    }
    .panel{
      position:relative;
      overflow:hidden;
      border:1px solid var(--line);
      background:
        linear-gradient(180deg, rgba(255,255,255,.04), transparent 70%),
        var(--panel);
      border-radius:var(--radius-xl);
      box-shadow:var(--shadow);
      backdrop-filter:blur(18px);
    }
    .panel::after{
      content:"";
      position:absolute;
      inset:0;
      pointer-events:none;
      background:linear-gradient(135deg, rgba(255,255,255,.08), transparent 26%, transparent 72%, rgba(255,255,255,.03));
      opacity:.55;
    }
    .hero{
      display:grid;
      grid-template-columns:minmax(0, 1.5fr) minmax(280px, .9fr);
      gap:24px;
      padding:26px;
      min-height:320px;
    }
    .hero-copy,.hero-visual,.section-inner,.meta-grid,.status-card,.action-card,.pair-card,.pending-card,.contract-card,.rejection-card,.empty-state{
      position:relative;
      z-index:1;
    }
    .hero-title{
      margin:14px 0 10px;
      font-size:clamp(1.7rem, 3vw, 2.9rem);
      line-height:1.02;
      letter-spacing:-.04em;
    }
    .hero-subtitle{
      margin:0;
      max-width:60ch;
      color:var(--muted-strong);
      font-size:1rem;
      line-height:1.75;
    }
    .hero-chips,.meta-chips,.pair-chips,.pending-chips,.contract-chips,.rejection-chips{
      display:flex;
      flex-wrap:wrap;
      gap:10px;
    }
    .hero-chips{
      margin-bottom:16px;
    }
    .metric-grid,.status-grid,.action-grid,.cards-grid,.meta-grid{
      display:grid;
      gap:14px;
    }
    .stable-strip{
      display:flex;
      flex-wrap:wrap;
      gap:10px;
      margin-top:18px;
    }
    .section-stack,.summary-list{
      display:grid;
      gap:14px;
    }
    .metric-grid{
      grid-template-columns:repeat(4, minmax(0, 1fr));
      margin-top:24px;
    }
    .metric-card{
      padding:18px;
      border-radius:22px;
      border:1px solid rgba(173, 190, 215, .12);
      background:rgba(7, 15, 26, .42);
      min-height:132px;
    }
    .metric-label{
      font-size:.76rem;
      text-transform:uppercase;
      letter-spacing:.16em;
      color:var(--muted);
    }
    .metric-value{
      margin-top:10px;
      font-size:clamp(2rem, 3vw, 2.8rem);
      line-height:.92;
      letter-spacing:-.06em;
    }
    .metric-hint{
      margin-top:10px;
      color:var(--muted-strong);
      font-size:.93rem;
      line-height:1.5;
    }
    .hero-visual{
      display:flex;
      align-items:center;
      justify-content:center;
      padding:12px;
    }
    .hero-visual-frame{
      width:min(100%, 360px);
      border-radius:30px;
      padding:12px;
      border:1px solid rgba(173, 190, 215, .14);
      background:
        radial-gradient(circle at top, rgba(111, 211, 255, .16), transparent 50%),
        rgba(7, 15, 26, .58);
      box-shadow:inset 0 1px 0 rgba(255, 255, 255, .06);
    }
    .section{
      padding:22px 24px 24px;
    }
    .section-head{
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:18px;
      margin-bottom:18px;
    }
    .section-kicker{
      display:block;
      color:var(--brand);
      font-size:.78rem;
      text-transform:uppercase;
      letter-spacing:.16em;
      margin-bottom:8px;
    }
    .section-title{
      margin:0;
      font-size:1.32rem;
      letter-spacing:-.02em;
    }
    .section-copy{
      margin:8px 0 0;
      color:var(--muted-strong);
      line-height:1.65;
    }
    .surface-header,.action-header,.pair-header,.pending-header,.contract-header,.rejection-header{
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:12px;
      margin-bottom:14px;
    }
    .status-grid{
      grid-template-columns:repeat(auto-fit, minmax(240px, 1fr));
    }
    .status-card,.action-card,.pair-card,.pending-card,.contract-card,.rejection-card,.empty-state{
      padding:18px;
      border-radius:22px;
      border:1px solid rgba(173, 190, 215, .12);
      background:linear-gradient(180deg, rgba(255,255,255,.04), rgba(6,12,21,.28));
      min-height:100%;
    }
    .spotlight-card{
      margin-top:20px;
      padding:18px 18px 16px;
      border-radius:22px;
      border:1px solid rgba(111, 211, 255, .18);
      background:
        radial-gradient(circle at top right, rgba(111, 211, 255, .12), transparent 38%),
        rgba(8, 16, 28, .72);
      box-shadow:inset 0 1px 0 rgba(255,255,255,.04);
    }
    .spotlight-title{
      margin:8px 0 6px;
      font-size:1.05rem;
      letter-spacing:-.02em;
    }
    .spotlight-copy{
      margin:0;
      color:var(--muted-strong);
      line-height:1.58;
    }
    .spotlight-card .code-block{
      margin-top:12px;
    }
    .surface-title,.action-title,.pair-title,.pending-title,.contract-title,.rejection-title{
      margin:0;
      font-size:1rem;
      letter-spacing:-.02em;
    }
    .surface-copy,.action-copy,.pair-copy,.pending-copy,.contract-copy,.rejection-copy,.empty-copy{
      margin:0;
      color:var(--muted-strong);
      line-height:1.62;
      font-size:.95rem;
    }
    .surface-stat{
      margin:16px 0 10px;
      font-size:2rem;
      letter-spacing:-.05em;
      line-height:.95;
    }
    .surface-meta,.pair-meta,.pending-meta,.contract-meta,.rejection-meta{
      display:flex;
      flex-wrap:wrap;
      gap:8px;
      margin-top:14px;
    }
    .detail-list{
      display:grid;
      gap:10px;
      margin-top:14px;
    }
    .summary-item{
      display:grid;
      grid-template-columns:minmax(0, 1fr) auto;
      gap:12px;
      align-items:center;
      padding:12px 14px;
      border-radius:16px;
      border:1px solid rgba(173, 190, 215, .12);
      background:rgba(6, 12, 22, .42);
    }
    .summary-item-title{
      font-weight:700;
      letter-spacing:-.01em;
    }
    .summary-item-copy{
      margin-top:5px;
      color:var(--muted);
      font-size:.86rem;
      line-height:1.45;
    }
    .summary-item-actions{
      display:flex;
      flex-wrap:wrap;
      justify-content:flex-end;
      gap:8px;
    }
    .detail-row{
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:18px;
      color:var(--muted-strong);
      font-size:.92rem;
    }
    .detail-row dt{
      color:var(--muted);
      min-width:0;
      flex:1;
    }
    .detail-row dd{
      margin:0;
      text-align:right;
      min-width:0;
      flex:1;
      font-family:var(--mono);
      font-size:.86rem;
      word-break:break-word;
    }
    .pill{
      display:inline-flex;
      align-items:center;
      gap:8px;
      padding:8px 12px;
      border-radius:999px;
      border:1px solid transparent;
      font-size:.8rem;
      letter-spacing:.02em;
      line-height:1;
      white-space:nowrap;
    }
    .pill::before{
      content:"";
      width:8px;
      height:8px;
      border-radius:50%;
      background:currentColor;
      box-shadow:0 0 12px currentColor;
      opacity:.92;
    }
    .tone-success{color:var(--success);background:rgba(112, 223, 159, .1);border-color:rgba(112, 223, 159, .2)}
    .tone-warning{color:var(--warning);background:rgba(255, 191, 99, .12);border-color:rgba(255, 191, 99, .24)}
    .tone-danger{color:var(--danger);background:rgba(255, 117, 143, .12);border-color:rgba(255, 117, 143, .24)}
    .tone-info{color:var(--info);background:rgba(155, 181, 255, .12);border-color:rgba(155, 181, 255, .22)}
    .tone-neutral{color:var(--muted-strong);background:rgba(191, 208, 232, .08);border-color:rgba(191, 208, 232, .16)}
    .action-grid,.cards-grid{
      grid-template-columns:repeat(auto-fit, minmax(300px, 1fr));
    }
    .meta-grid{
      grid-template-columns:1.4fr .9fr;
    }
    .code-block{
      margin:14px 0 0;
      padding:14px 16px;
      border-radius:16px;
      background:rgba(6, 12, 22, .75);
      border:1px solid rgba(173, 190, 215, .12);
      color:#d7e6ff;
      font-family:var(--mono);
      font-size:.85rem;
      line-height:1.65;
      word-break:break-word;
      white-space:pre-wrap;
    }
    .button-row{
      display:flex;
      flex-wrap:wrap;
      gap:10px;
      margin-top:16px;
    }
    .button-row .button{
      min-height:40px;
      padding:0 14px;
      font-size:.92rem;
      box-shadow:none;
    }
    .pair-card{
      display:grid;
      gap:16px;
    }
    .pair-identity{
      display:flex;
      gap:14px;
      align-items:flex-start;
    }
    .avatar{
      display:grid;
      place-items:center;
      width:52px;
      height:52px;
      border-radius:18px;
      font-weight:700;
      letter-spacing:.06em;
      color:#06101c;
      flex:none;
      box-shadow:0 12px 28px rgba(3, 8, 18, .22);
    }
    .avatar-codex{
      background:linear-gradient(135deg, #7ff7c4, #2cc4ff);
    }
    .avatar-claude{
      background:linear-gradient(135deg, #ffd580, #ff8ca0);
    }
    .avatar-other{
      background:linear-gradient(135deg, #c4d6ff, #7ff7c4);
    }
    details{
      border-top:1px solid rgba(173, 190, 215, .12);
      margin-top:14px;
      padding-top:14px;
    }
    summary{
      cursor:pointer;
      color:var(--muted-strong);
      list-style:none;
    }
    summary::-webkit-details-marker{
      display:none;
    }
    summary::after{
      content:"+";
      float:right;
      color:var(--brand);
      font-weight:700;
    }
    details[open] summary::after{
      content:"−";
    }
    .empty-state{
      display:grid;
      gap:16px;
      place-items:start;
      min-height:220px;
    }
    .empty-art{
      width:100%;
      max-width:250px;
      opacity:.92;
    }
    .mono{
      font-family:var(--mono);
    }
    .footer-note{
      margin-top:8px;
      color:var(--muted);
      font-size:.88rem;
    }
    .modal-root:empty{
      display:none;
    }
    .modal-root{
      position:fixed;
      inset:0;
      display:grid;
      place-items:center;
      padding:20px;
      background:rgba(4, 10, 18, .72);
      backdrop-filter:blur(12px);
      z-index:50;
    }
    .modal-card{
      width:min(480px, 100%);
      padding:22px;
      border-radius:24px;
      border:1px solid rgba(173, 190, 215, .16);
      background:linear-gradient(180deg, rgba(16, 26, 44, .96), rgba(8, 16, 28, .96));
      box-shadow:0 28px 70px rgba(2, 6, 14, .42);
    }
    .modal-title{
      margin:8px 0 8px;
      font-size:1.4rem;
      letter-spacing:-.03em;
    }
    .modal-copy{
      margin:0;
      color:var(--muted-strong);
      line-height:1.65;
    }
    .modal-field{
      display:grid;
      gap:8px;
      margin-top:16px;
      color:var(--muted-strong);
      font-size:.92rem;
    }
    .modal-field input{
      width:100%;
      min-height:48px;
      border-radius:14px;
      border:1px solid rgba(173, 190, 215, .16);
      background:rgba(6, 12, 22, .84);
      color:var(--text);
      padding:0 14px;
    }
    .modal-actions{
      display:flex;
      flex-wrap:wrap;
      justify-content:flex-end;
      gap:10px;
      margin-top:18px;
    }
    .toast{
      position:fixed;
      right:20px;
      bottom:20px;
      min-width:220px;
      max-width:min(420px, calc(100vw - 40px));
      padding:14px 16px;
      border-radius:16px;
      border:1px solid rgba(173, 190, 215, .18);
      background:rgba(8, 14, 24, .94);
      color:var(--text);
      box-shadow:0 18px 40px rgba(2, 6, 14, .35);
      opacity:0;
      pointer-events:none;
      transform:translateY(12px);
      transition:opacity .18s ease, transform .18s ease;
      z-index:40;
    }
    .toast.visible{
      opacity:1;
      transform:translateY(0);
    }
    .toast-success{border-color:rgba(112, 223, 159, .24)}
    .toast-warning{border-color:rgba(255, 191, 99, .28)}
    .toast-danger{border-color:rgba(255, 117, 143, .28)}
    .toast-info{border-color:rgba(111, 211, 255, .24)}
    .skeleton{
      min-height:320px;
      padding:26px;
      display:grid;
      gap:16px;
    }
    .skeleton-line,.skeleton-card{
      border-radius:18px;
      background:linear-gradient(90deg, rgba(255,255,255,.04), rgba(255,255,255,.09), rgba(255,255,255,.04));
      background-size:220% 100%;
      animation:shimmer 1.6s linear infinite;
    }
    .skeleton-line{height:18px;max-width:260px}
    .skeleton-card{height:112px}
    @keyframes shimmer{
      from{background-position:200% 0}
      to{background-position:-40% 0}
    }
    @media (max-width:1120px){
      .hero,.meta-grid{
        grid-template-columns:1fr;
      }
    }
    @media (max-width:820px){
      .topbar{
        flex-direction:column;
      }
      .header-actions{
        justify-content:flex-start;
      }
      .metric-grid{
        grid-template-columns:repeat(2, minmax(0, 1fr));
      }
    }
    @media (max-width:560px){
      .shell{
        padding:18px 14px 28px;
      }
      h1{
        font-size:2rem;
      }
      .metric-grid{
        grid-template-columns:1fr;
      }
      .section,.hero{
        padding:18px;
      }
      .button{
        width:100%;
      }
      .header-actions{
        width:100%;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="brand">
        <span class="eyebrow">Local bridge operations</span>
        <h1>Agent Bridge Dashboard</h1>
        <p class="lead">Operational view for sessions, pairing health, wake safety, and next actions. Refreshes every 5s while live mode is on.</p>
      </div>
      <div class="header-actions">
        <button type="button" class="button button-secondary" id="refresh-toggle-button" data-action="toggle-refresh" data-focus-key="toggle-refresh">Pause live refresh</button>
        <button type="button" class="button button-primary" id="refresh-button" data-action="refresh" data-focus-key="manual-refresh">Refresh now</button>
        <button type="button" class="button button-secondary" id="copy-recovery-button" data-action="copy-recovery" data-focus-key="copy-recovery">Copy recovery hint</button>
        <button type="button" class="button button-danger" id="shutdown-button" data-action="shutdown" data-focus-key="shutdown-dashboard">Stop dashboard server</button>
      </div>
    </header>
    <div class="status-banner" id="status-banner">Loading live bridge snapshot…</div>
    <main class="dashboard-root" id="dashboard-root">
      <section class="panel skeleton">
        <div class="skeleton-line"></div>
        <div class="skeleton-line" style="max-width:520px"></div>
        <div class="metric-grid">
          <div class="skeleton-card"></div>
          <div class="skeleton-card"></div>
          <div class="skeleton-card"></div>
          <div class="skeleton-card"></div>
        </div>
      </section>
    </main>
  </div>
  <div class="modal-root" id="modal-root"></div>
  <div class="toast" id="toast" role="status" aria-live="polite"></div>
  <noscript>This dashboard needs JavaScript enabled to render the live bridge surface.</noscript>
  <script>
    const TOKEN=__DASHBOARD_TOKEN_JSON__;
    const CSRF=__DASHBOARD_CSRF_JSON__;
    const PROJECT=__DASHBOARD_PROJECT_JSON__;
    const INITIAL_PAYLOAD=__DASHBOARD_INITIAL_JSON__;
    const REFRESH_MS=5000;
    let latestPayload=INITIAL_PAYLOAD;
    let lastRenderSignature=null;
    let toastTimer=null;
    let modalResolver=null;
    let autoRefreshEnabled=true;
    let refreshTimer=null;
    let modalReturnFocus=null;
    const DIRECT_ACTION_LABELS={
      restart_watcher:"Start watcher now",
      compact_stale_server_markers:"Run cleanup now",
      backfill_read_receipts:"Backfill now"
    };

    function escapeHtml(value){
      return String(value == null ? "" : value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function overviewUrl(){
      let url="/api/overview?format=json&token="+encodeURIComponent(TOKEN);
      if(PROJECT){
        url+="&project="+encodeURIComponent(PROJECT);
      }
      return url;
    }

    function byId(id){
      return document.getElementById(id);
    }

    function toArray(value){
      return Array.isArray(value) ? value : [];
    }

    function titleCase(value){
      return String(value || "unknown")
        .replace(/[_-]+/g, " ")
        .replace(/\\b\\w/g, function(char){ return char.toUpperCase(); });
    }

    function toneForStatus(status){
      const value=String(status || "unknown").toLowerCase();
      if(["ok","active","current","clean","dashboard_overview","already_active","verified"].includes(value)){
        return "success";
      }
      if(["warning","degraded","partial","rearmed","attention_required","pending","expiring_soon","unknown"].includes(value)){
        return value === "unknown" ? "info" : "warning";
      }
      if(["broken","blocked","error","action_required","rejected","expired","revoked"].includes(value)){
        return "danger";
      }
      return "info";
    }

    function toneForSeverity(severity){
      const value=String(severity || "normal").toLowerCase();
      if(value === "high" || value === "critical"){
        return "danger";
      }
      if(value === "normal" || value === "medium"){
        return "warning";
      }
      return "info";
    }

    function toneClass(tone){
      return "tone-"+tone;
    }

    function shortId(value){
      if(!value){
        return "—";
      }
      return String(value).slice(0, 8);
    }

    function pluralize(count, singular, plural){
      return count+" "+(count === 1 ? singular : (plural || singular+"s"));
    }

    function formatCount(value){
      const number=Number(value);
      if(Number.isFinite(number)){
        return new Intl.NumberFormat().format(number);
      }
      return "0";
    }

    function formatDate(value){
      if(!value){
        return "—";
      }
      const date=new Date(value);
      if(Number.isNaN(date.getTime())){
        return escapeHtml(value);
      }
      return escapeHtml(date.toLocaleString());
    }

    function formatRelative(value){
      if(!value){
        return "—";
      }
      const date=new Date(value);
      if(Number.isNaN(date.getTime())){
        return escapeHtml(value);
      }
      const delta=Math.round((Date.now() - date.getTime()) / 1000);
      const abs=Math.abs(delta);
      let unit="second";
      let amount=abs;
      if(abs >= 86400){
        unit="day";
        amount=Math.round(abs / 86400);
      }else if(abs >= 3600){
        unit="hour";
        amount=Math.round(abs / 3600);
      }else if(abs >= 60){
        unit="minute";
        amount=Math.round(abs / 60);
      }
      const label=amount+" "+unit+(amount === 1 ? "" : "s");
      return escapeHtml(delta >= 0 ? label+" ago" : "in "+label);
    }

    function formatDurationSeconds(value){
      const seconds=Number(value);
      if(!Number.isFinite(seconds)){
        return "—";
      }
      const abs=Math.max(0, Math.round(seconds));
      if(abs >= 86400){
        return Math.round(abs / 86400)+"d";
      }
      if(abs >= 3600){
        return Math.round(abs / 3600)+"h";
      }
      if(abs >= 60){
        return Math.round(abs / 60)+"m";
      }
      return abs+"s";
    }

    async function copyText(text, label){
      const value=String(text || "");
      if(!value){
        showToast("Nothing to copy for "+label+".", "warning");
        return;
      }
      try{
        if(navigator.clipboard && navigator.clipboard.writeText){
          await navigator.clipboard.writeText(value);
        }else{
          const area=document.createElement("textarea");
          area.value=value;
          document.body.appendChild(area);
          area.select();
          document.execCommand("copy");
          document.body.removeChild(area);
        }
        showToast(label+" copied.", "success");
      }catch(error){
        showToast("Copy failed: "+String(error), "danger");
      }
    }

    function showToast(message, tone){
      const toast=byId("toast");
      toast.textContent=String(message || "");
      toast.className="toast visible toast-"+(tone || "info");
      if(toastTimer){
        clearTimeout(toastTimer);
      }
      toastTimer=setTimeout(function(){
        toast.className="toast";
      }, 2400);
    }

    function setStatus(message, tone){
      const banner=byId("status-banner");
      banner.textContent=String(message || "");
      banner.className="status-banner";
      if(tone){
        banner.classList.add(toneClass(tone));
      }
    }

    function setBusy(isBusy){
      const refreshButton=byId("refresh-button");
      if(refreshButton){
        refreshButton.dataset.busy=isBusy ? "true" : "false";
        refreshButton.setAttribute("aria-busy", isBusy ? "true" : "false");
      }
    }

    function updateRefreshToggle(){
      const button=byId("refresh-toggle-button");
      if(!button){
        return;
      }
      button.textContent=autoRefreshEnabled ? "Pause live refresh" : "Resume live refresh";
      button.className="button "+(autoRefreshEnabled ? "button-secondary" : "button-primary");
    }

    function captureOpenDetails(){
      return Array.from(document.querySelectorAll("details[data-detail-key][open]")).map(function(node){
        return node.getAttribute("data-detail-key");
      }).filter(Boolean);
    }

    function captureViewportState(){
      const active=document.activeElement;
      return {
        scrollY:window.scrollY || window.pageYOffset || 0,
        focusKey:active && active.getAttribute ? active.getAttribute("data-focus-key") : null
      };
    }

    function restoreViewportState(state){
      if(state && state.focusKey){
        const focusTarget=Array.from(document.querySelectorAll("[data-focus-key]")).find(function(node){
          return node.getAttribute("data-focus-key") === state.focusKey;
        });
        if(focusTarget && focusTarget.focus){
          focusTarget.focus();
        }
      }
      if(state && typeof state.scrollY === "number"){
        window.scrollTo(0, state.scrollY);
      }
    }

    function payloadSignature(payload){
      const overview=((payload || {}).data || {}).overview;
      function stripVolatile(value){
        if(Array.isArray(value)){
          return value.map(stripVolatile);
        }
        if(value && typeof value === "object"){
          const result={};
          Object.keys(value).forEach(function(key){
            if(
              key === "generated_at"
              || key === "snapshot_ts"
              || key === "snapshot_duration_ms"
              || key === "age_seconds"
              || key.endsWith("_at")
            ){
              return;
            }
            result[key]=stripVolatile(value[key]);
          });
          return result;
        }
        return value;
      }
      return JSON.stringify(stripVolatile(overview || payload || {}));
    }

    function restoreOpenDetails(keys){
      if(!Array.isArray(keys) || !keys.length){
        return;
      }
      const nodes=Array.from(document.querySelectorAll("details[data-detail-key]"));
      keys.forEach(function(key){
        const match=nodes.find(function(node){ return node.getAttribute("data-detail-key") === key; });
        if(match){
          match.open=true;
        }
      });
    }

    function pairingTitle(pairing){
      const status=String((pairing || {}).status || "unknown");
      return ((pairing && pairing.project) ? pairing.project : "project")
        +" / "
        +titleCase(status === "active" ? ((pairing || {}).role || "active") : status)
        +" / "
        +titleCase((pairing || {}).agent || "agent")
        +" "
        +shortId((pairing || {}).session_id || "");
    }

    function openModal(options){
      return new Promise(function(resolve){
        modalResolver=resolve;
        modalReturnFocus=document.activeElement && document.activeElement.focus ? document.activeElement : null;
        const needsInput=Boolean(options && options.inputLabel);
        byId("modal-root").innerHTML=
          '<div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="modal-title">'
          + '<div class="section-kicker">'+escapeHtml((options && options.kicker) || "Confirm action")+'</div>'
          + '<h3 class="modal-title" id="modal-title">'+escapeHtml((options && options.title) || "Confirm")+'</h3>'
          + '<p class="modal-copy">'+escapeHtml((options && options.body) || "")+'</p>'
          + (needsInput ? '<label class="modal-field"><span>'+escapeHtml(options.inputLabel)+'</span><input id="modal-input" type="'+escapeHtml((options.inputType) || "text")+'" value="'+escapeHtml((options.inputValue) || "")+'" /></label>' : '')
          + '<div class="modal-actions">'
          + '<button type="button" class="button button-secondary" data-modal-action="cancel">'+escapeHtml((options && options.cancelLabel) || "Cancel")+'</button>'
          + '<button type="button" class="button '+(((options && options.confirmTone) === "danger") ? "button-danger" : "button-primary")+'" data-modal-action="confirm">'+escapeHtml((options && options.confirmLabel) || "Confirm")+'</button>'
          + '</div>'
          + '</div>';
        const input=byId("modal-input");
        if(input){
          input.focus();
          input.select();
        }else{
          const confirmButton=Array.from(document.querySelectorAll("#modal-root button[data-modal-action='confirm']")).shift();
          if(confirmButton && confirmButton.focus){
            confirmButton.focus();
          }
        }
      });
    }

    function closeModal(result){
      byId("modal-root").innerHTML="";
      const resolver=modalResolver;
      modalResolver=null;
      if(resolver){
        resolver(result || { confirmed:false, value:null });
      }
      if(modalReturnFocus && document.contains(modalReturnFocus) && modalReturnFocus.focus){
        modalReturnFocus.focus();
      }
      modalReturnFocus=null;
    }

    async function confirmModal(title, body, confirmLabel, confirmTone){
      const result=await openModal({
        title:title,
        body:body,
        confirmLabel:confirmLabel || "Confirm",
        confirmTone:confirmTone || "primary"
      });
      return Boolean(result && result.confirmed);
    }

    async function promptModal(title, body, inputLabel, inputValue, confirmLabel, inputType){
      const result=await openModal({
        kicker:"Provide value",
        title:title,
        body:body,
        inputLabel:inputLabel,
        inputValue:inputValue || "",
        confirmLabel:confirmLabel || "Save",
        inputType:inputType || "text"
      });
      if(!result || !result.confirmed){
        return null;
      }
      return result.value;
    }

    async function apiPost(path, payload){
      const response=await fetch(path+"?token="+encodeURIComponent(TOKEN), {
        method:"POST",
        headers:{
          "X-CSRF-Token":CSRF,
          "Content-Type":"application/json"
        },
        body:JSON.stringify(payload || {})
      });
      const data=await response.json();
      if(!response.ok || !data.ok){
        throw new Error(data.message || data.error || "request failed");
      }
      return data;
    }

    async function shutdownDashboard(){
      const confirmed=await confirmModal(
        "Stop dashboard server?",
        "This closes the local admin surface until it is launched again.",
        "Stop server",
        "danger"
      );
      if(!confirmed){
        return;
      }
      const button=byId("shutdown-button");
      if(button){
        button.disabled=true;
      }
      try{
        const data=await apiPost("/api/shutdown", {});
        setStatus(data.message || "Dashboard shutdown requested.", "warning");
        showToast(data.message || "Dashboard shutdown requested.", "warning");
      }catch(error){
        setStatus("Shutdown error: "+String(error), "danger");
        showToast("Shutdown error: "+String(error), "danger");
        if(button){
          button.disabled=false;
        }
      }
    }

    function emptyIllustration(){
      return '<svg class="empty-art" viewBox="0 0 280 160" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
        + '<rect x="18" y="28" width="244" height="104" rx="24" fill="rgba(18,35,61,.72)" stroke="rgba(173,190,215,.18)"/>'
        + '<circle cx="72" cy="80" r="18" fill="rgba(111,211,255,.22)"/><circle cx="140" cy="80" r="18" fill="rgba(127,247,196,.2)"/><circle cx="208" cy="80" r="18" fill="rgba(255,191,99,.18)"/>'
        + '<path d="M90 80H122" stroke="rgba(111,211,255,.72)" stroke-width="4" stroke-linecap="round"/><path d="M158 80H190" stroke="rgba(127,247,196,.72)" stroke-width="4" stroke-linecap="round"/>'
        + '<rect x="54" y="118" width="172" height="8" rx="4" fill="rgba(173,190,215,.14)"/>'
        + '</svg>';
    }

    function heroVisual(overview){
      const pairings=toArray(overview.pairings);
      const activePairings=pairings.filter(function(item){ return String(item.status || "").toLowerCase() === "active"; }).length;
      const pendingActions=toArray(overview.pending_actions).length;
      const contracts=toArray(overview.contracts).length;
      const unread=((overview.status_surfaces || {}).backpressure || {}).unread_work_count || 0;
      return '<div class="hero-visual-frame">'
        + '<svg viewBox="0 0 340 300" width="100%" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
        + '<defs>'
        + '<linearGradient id="orbitStroke" x1="64" y1="32" x2="276" y2="268" gradientUnits="userSpaceOnUse">'
        + '<stop stop-color="#6fd3ff"/><stop offset="1" stop-color="#7ff7c4"/>'
        + '</linearGradient>'
        + '<linearGradient id="coreGlow" x1="170" y1="76" x2="170" y2="224" gradientUnits="userSpaceOnUse">'
        + '<stop stop-color="#2cc4ff"/><stop offset="1" stop-color="#7ff7c4"/>'
        + '</linearGradient>'
        + '</defs>'
        + '<circle cx="170" cy="150" r="108" stroke="rgba(173,190,215,.18)" stroke-width="1.5" stroke-dasharray="7 8"/>'
        + '<circle cx="170" cy="150" r="76" stroke="url(#orbitStroke)" stroke-width="2.5" opacity=".82"/>'
        + '<circle cx="170" cy="150" r="54" fill="rgba(8,17,30,.88)" stroke="rgba(255,255,255,.08)"/>'
        + '<circle cx="170" cy="150" r="38" fill="url(#coreGlow)" opacity=".92"/>'
        + '<text x="170" y="145" text-anchor="middle" fill="#04101b" font-family="var(--sans)" font-size="13" font-weight="700" letter-spacing=".18em">BRIDGE</text>'
        + '<text x="170" y="163" text-anchor="middle" fill="#04101b" font-family="var(--mono)" font-size="11">LIVE</text>'
        + '<g transform="translate(54 56)"><circle cx="0" cy="0" r="28" fill="rgba(111,211,255,.18)" stroke="rgba(111,211,255,.55)"/><text x="0" y="-3" text-anchor="middle" fill="#dff4ff" font-size="12" font-family="var(--mono)">PAIR</text><text x="0" y="13" text-anchor="middle" fill="#6fd3ff" font-size="18" font-weight="700">'+escapeHtml(String(activePairings))+'</text></g>'
        + '<g transform="translate(284 86)"><circle cx="0" cy="0" r="28" fill="rgba(127,247,196,.18)" stroke="rgba(127,247,196,.55)"/><text x="0" y="-3" text-anchor="middle" fill="#e7fff6" font-size="12" font-family="var(--mono)">NEXT</text><text x="0" y="13" text-anchor="middle" fill="#7ff7c4" font-size="18" font-weight="700">'+escapeHtml(String(pendingActions))+'</text></g>'
        + '<g transform="translate(256 238)"><circle cx="0" cy="0" r="26" fill="rgba(255,191,99,.16)" stroke="rgba(255,191,99,.55)"/><text x="0" y="-2" text-anchor="middle" fill="#fff1d1" font-size="11" font-family="var(--mono)">LINK</text><text x="0" y="13" text-anchor="middle" fill="#ffbf63" font-size="17" font-weight="700">'+escapeHtml(String(contracts))+'</text></g>'
        + '<g transform="translate(70 226)"><circle cx="0" cy="0" r="24" fill="rgba(255,117,143,.15)" stroke="rgba(255,117,143,.55)"/><text x="0" y="-2" text-anchor="middle" fill="#ffe3ea" font-size="11" font-family="var(--mono)">UNREAD</text><text x="0" y="13" text-anchor="middle" fill="#ff758f" font-size="16" font-weight="700">'+escapeHtml(String(unread))+'</text></g>'
        + '<path d="M82 69C107 88 123 104 133 121" stroke="rgba(111,211,255,.48)" stroke-width="3" stroke-linecap="round"/>'
        + '<path d="M257 101C228 109 209 119 195 130" stroke="rgba(127,247,196,.46)" stroke-width="3" stroke-linecap="round"/>'
        + '<path d="M235 221C214 201 201 186 191 173" stroke="rgba(255,191,99,.44)" stroke-width="3" stroke-linecap="round"/>'
        + '<path d="M92 210C112 192 126 181 140 170" stroke="rgba(255,117,143,.42)" stroke-width="3" stroke-linecap="round"/>'
        + '</svg>'
        + '</div>';
    }

    function metricCard(label, value, hint){
      return '<div class="metric-card">'
        + '<div class="metric-label">'+escapeHtml(label)+'</div>'
        + '<div class="metric-value">'+escapeHtml(String(value))+'</div>'
        + '<div class="metric-hint">'+escapeHtml(hint)+'</div>'
        + '</div>';
    }

    function detailRow(label, value){
      return '<div class="detail-row"><dt>'+escapeHtml(label)+'</dt><dd>'+escapeHtml(value)+'</dd></div>';
    }

    function renderSurfaceCard(key, surface){
      const status=String((surface || {}).status || "unknown");
      const tone=toneForStatus(status);
      const titleByKey={
        dashboard_reads:"Read integrity",
        backpressure:"Inbox backpressure",
        claude_monitor:"Claude monitor",
        stale_unread_watchdog:"Unread watchdog",
        catchup:"Catch-up debt",
        contracts:"Cross-project contracts",
        policy_drift:"Policy drift",
        guardrail_debt:"Guardrail debt"
      };
      const title=titleByKey[key] || titleCase(key);
      let stat="0";
      let copy="Everything looks calm.";
      let details="";
      let command="";
      if(key === "dashboard_reads"){
        const degraded=Number(surface.degraded_component_count || 0);
        stat=String(degraded);
        copy=degraded ? degraded+" component(s) need cleanup before the dashboard can fully trust every read." : "Every core dashboard source is readable right now.";
        if(surface.components){
          details=detailRow("Degraded components", (surface.degraded_components || []).join(", ") || "None")
            + detailRow("Audit file", ((surface.components.audit || {}).path) || "—")
            + detailRow("Audit bad lines", String(((surface.components.audit || {}).bad_lines) || 0));
        }
      }else if(key === "backpressure"){
        stat=String(surface.unread_work_count || 0);
        copy=(surface.unread_work_count || 0) ? "Unread bridge work is accumulating and should be drained before it blocks the next handoff." : "No unread work is clogging the bridge right now.";
        details=detailRow("Blocked buckets", String(surface.blocked_bucket_count || 0))
          + detailRow("Warning buckets", String(surface.warning_bucket_count || 0))
          + detailRow("Blocked senders", String(surface.blocked_sender_count || 0));
        command=(((surface.items || [])[0] || {}).remediation_command) || "";
      }else if(key === "claude_monitor"){
        stat=String(surface.session_count || 0);
        copy=(surface.problem_count || 0) ? "One or more Claude monitor sessions need attention." : "Claude monitor coverage is healthy and current.";
        details=detailRow("Problems", String(surface.problem_count || 0))
          + detailRow("Unread messages", String((((surface.sessions || [])[0] || {}).unread_count) || 0))
          + detailRow("Fresh heartbeat", (((surface.sessions || [])[0] || {}).runtime || {}).fresh ? "Yes" : "No");
        command=((((surface.sessions || [])[0] || {}).remediation_command) || "");
      }else if(key === "stale_unread_watchdog"){
        stat=String(surface.rearm_count || 0);
        copy=(surface.rearm_count || 0) ? "The watchdog had to rescue stale unread suppression for at least one message." : "No stale unread suppression needed rescue.";
        details=detailRow("Last rearm", formatDate((((surface.items || [])[0] || {}).event_ts) || ""))
          + detailRow("Runtime status", (((surface.items || [])[0] || {}).runtime_status) || "ok")
          + detailRow("Agent", (((surface.items || [])[0] || {}).agent) || "—");
        command=(((surface.items || [])[0] || {}).remediation_command) || "";
      }else if(key === "catchup"){
        stat=String(surface.pending_event_count || 0);
        copy=(surface.pending_event_count || 0) ? "Journal events still need acknowledgement across at least one peer state." : "Catch-up is fully drained for this project scope.";
        details=detailRow("Pending events", String(surface.pending_event_count || 0))
          + detailRow("Pending pairs", String(surface.pending_pair_count || 0))
          + detailRow("Scope", titleCase(surface.scope || "project"));
      }else if(key === "contracts"){
        stat=String(surface.active_count || 0);
        copy=(surface.reauthorization_required_count || 0) ? "Some cross-project links need reauthorization before they can be trusted again." : "Cross-project contracts are either clear or absent.";
        details=detailRow("Reauth required", String(surface.reauthorization_required_count || 0))
          + detailRow("Expiring soon", String(surface.expiring_soon_count || 0))
          + detailRow("Revoked", String(surface.revoked_count || 0));
      }else if(key === "policy_drift"){
        const drift=(surface.doc_drift_count || 0) + (surface.missing_doc_count || 0);
        stat=String(drift);
        copy=drift ? "Runtime policy claims are drifting away from protected docs and should be reconciled." : "Protected docs and runtime policy are aligned.";
        details=detailRow("Missing docs", String(surface.missing_doc_count || 0))
          + detailRow("Contradictions", String(surface.doc_drift_count || 0))
          + detailRow("Protected docs", String(surface.protected_doc_count || 0));
      }else if(key === "guardrail_debt"){
        stat=String(surface.active_debt_count || 0);
        copy=(surface.active_debt_count || 0) ? "Guardrail debt is open and should be resolved before it compounds." : "No active guardrail debt is open on this project.";
        details=detailRow("Scope", titleCase(surface.scope || "project"))
          + detailRow("Project", surface.project || "—")
          + detailRow("Enforcement tiers", String(Object.keys(surface.by_enforcement_tier || {}).length));
      }
      return '<article class="status-card">'
        + '<div class="surface-header"><div><div class="section-kicker">Status surface</div><h3 class="surface-title">'+escapeHtml(title)+'</h3></div>'
        + '<span class="pill '+toneClass(tone)+'">'+escapeHtml(titleCase(status))+'</span></div>'
        + '<div class="surface-stat">'+escapeHtml(stat)+'</div>'
        + '<p class="surface-copy">'+escapeHtml(copy)+'</p>'
        + '<dl class="detail-list">'+details+'</dl>'
        + (command ? '<div class="button-row"><button type="button" class="button button-secondary" data-action="copy-text" data-copy="'+escapeHtml(command)+'" data-copy-label="Remediation command">Copy remediation</button></div>' : "")
        + '</article>';
    }

    function surfaceNeedsAttention(key, surface){
      const status=String((surface || {}).status || "unknown").toLowerCase();
      if(!["ok","active","current","clean"].includes(status)){
        return true;
      }
      if(key === "dashboard_reads"){
        return Number(surface.degraded_component_count || 0) > 0;
      }
      if(key === "backpressure"){
        return Number(surface.unread_work_count || 0) > 0 || Number(surface.blocked_bucket_count || 0) > 0 || Number(surface.warning_bucket_count || 0) > 0;
      }
      if(key === "claude_monitor"){
        return Number(surface.problem_count || 0) > 0;
      }
      if(key === "stale_unread_watchdog"){
        return Number(surface.rearm_count || 0) > 0;
      }
      if(key === "catchup"){
        return Number(surface.pending_event_count || 0) > 0 || Number(surface.pending_pair_count || 0) > 0;
      }
      if(key === "contracts"){
        return Number(surface.reauthorization_required_count || 0) > 0 || Number(surface.expiring_soon_count || 0) > 0 || Number(surface.revoked_count || 0) > 0;
      }
      if(key === "policy_drift"){
        return Number(surface.doc_drift_count || 0) > 0 || Number(surface.missing_doc_count || 0) > 0;
      }
      if(key === "guardrail_debt"){
        return Number(surface.active_debt_count || 0) > 0;
      }
      return false;
    }

    function renderStableSurfaceChip(key, surface){
      const titles={
        dashboard_reads:"Reads",
        backpressure:"Inbox",
        claude_monitor:"Monitor",
        stale_unread_watchdog:"Watchdog",
        catchup:"Catch-up",
        contracts:"Contracts",
        policy_drift:"Policy",
        guardrail_debt:"Guardrails"
      };
      const label=titles[key] || titleCase(key);
      return '<span class="pill '+toneClass("success")+'">'+escapeHtml(label)+" stable"+'</span>';
    }

    function findRecommendedAction(actions, id){
      return toArray(actions).find(function(action){
        return String((action || {}).id || "") === id;
      }) || null;
    }

    function renderCoreCauseCard(config){
      const action=config.action || null;
      const actionId=String((action || {}).id || config.actionId || "");
      const command=String(config.command || ((action || {}).command) || "");
      const directActionLabel=DIRECT_ACTION_LABELS[actionId] || "";
      const details=toArray(config.details).map(function(row){
        return detailRow(row.label, row.value);
      }).join("");
      return '<article class="status-card">'
        + '<div class="surface-header"><div><div class="section-kicker">Core health cause</div><h3 class="surface-title">'+escapeHtml(config.title || "Core signal")+'</h3></div>'
        + '<span class="pill '+toneClass(config.tone || "warning")+'">'+escapeHtml(config.status || "Needs attention")+'</span></div>'
        + '<div class="surface-stat">'+escapeHtml(String(config.stat == null ? "!" : config.stat))+'</div>'
        + '<p class="surface-copy">'+escapeHtml(config.copy || "This core bridge signal needs operator attention.")+'</p>'
        + (details ? '<dl class="detail-list">'+details+'</dl>' : "")
        + (command ? '<div class="button-row">'
          + (directActionLabel ? '<button type="button" class="button button-primary" data-action="apply-recommended-action" data-action-id="'+escapeHtml(actionId)+'" data-focus-key="core-run-'+escapeHtml(actionId)+'">'+escapeHtml(directActionLabel)+'</button>' : '')
          + '<button type="button" class="button button-secondary" data-action="copy-text" data-copy="'+escapeHtml(command)+'" data-copy-label="Core remediation command" data-focus-key="core-copy-'+escapeHtml(actionId || config.title || "command")+'">Copy remediation</button>'
          + '</div>' : "")
        + '</article>';
    }

    function renderCoreCauseCards(health, recommended){
      const core=((health || {}).core) || {};
      const cards=[];
      const watcher=core.watcher || {};
      if(watcher.running === false || watcher.stale || watcher.root_mismatch){
        const action=findRecommendedAction(recommended, "restart_watcher");
        cards.push(renderCoreCauseCard({
          title:"Watcher delivery loop",
          status:watcher.stale ? "Stale" : "Not running",
          tone:watcher.stale ? "danger" : "warning",
          stat:watcher.running === false ? "Off" : "!",
          copy:"Direct bridge tools can still communicate, but passive wake/toast delivery is not armed until the watcher is running for this bridge root.",
          action:action,
          details:[
            {label:"Expected", value:watcher.expected === false ? "not armed" : "armed"},
            {label:"PID", value:watcher.pid || watcher.pid_marker || "none"},
            {label:"Runtime", value:watcher.runtime ? "present" : "missing"}
          ]
        }));
      }
      const server=core.server || {};
      const staleMarkerCount=Number(server.stale_server_marker_count || 0);
      if(staleMarkerCount > 0){
        const action=findRecommendedAction(recommended, "compact_stale_server_markers");
        cards.push(renderCoreCauseCard({
          title:"MCP server markers",
          status:"Stale markers",
          tone:"danger",
          stat:staleMarkerCount,
          copy:"Dead MCP server PID/runtime marker files are still present, so process health looks worse than the live process set.",
          action:action,
          details:[
            {label:"Total markers", value:server.mcp_server_marker_count || staleMarkerCount},
            {label:"Stale markers", value:staleMarkerCount},
            {label:"Cleanup", value:action ? "allowlisted direct action" : "copy remediation"}
          ]
        }));
      }
      const reconnect=server.mcp_reconnect || {};
      if(["tool_access_risk","client_reconnect_likely_required"].includes(String(reconnect.impact_class || "")) || reconnect.reconnect_required){
        const action=findRecommendedAction(recommended, "reconnect_mcp_host");
        cards.push(renderCoreCauseCard({
          title:"MCP reconnect proof",
          status:"Unproven",
          tone:"warning",
          stat:reconnect.wrapper_launches_last_5m || reconnect.wrapper_launch_count_today || "?",
          copy:"The wrapper relaunched, but the dashboard has not seen fresh MCP tool activity proving the host reconnected to the new server.",
          action:action,
          details:[
            {label:"Impact", value:reconnect.impact_class || "reconnect required"},
            {label:"Wrapper PID", value:reconnect.wrapper_pid || "unknown"},
            {label:"Tool activity", value:reconnect.last_tool_activity_at || "not observed"}
          ]
        }));
      }
      const inboxes=core.inboxes || {};
      const receiptTotals=inboxes.totals || {};
      const handledNotSeen=Number(receiptTotals.handled_not_seen_count || 0);
      if(handledNotSeen > 0){
        const action=findRecommendedAction(recommended, "backfill_read_receipts");
        cards.push(renderCoreCauseCard({
          title:"Receipt metadata debt",
          status:"Backfill available",
          tone:"warning",
          stat:handledNotSeen,
          copy:"Some rows are already read or handled but predate reliable seen_at receipts. Backfill repairs metadata without marking unread work read.",
          action:action,
          details:[
            {label:"Affected rows", value:handledNotSeen},
            {label:"Unread work", value:receiptTotals.unread_work_count || 0},
            {label:"Buckets scanned", value:inboxes.bucket_count || "unknown"}
          ]
        }));
      }
      const stuckWakes=core.stuck_wakes || {};
      if(Number(stuckWakes.count || 0) > 0){
        const action=findRecommendedAction(recommended, "inspect_stuck_wakes");
        cards.push(renderCoreCauseCard({
          title:"Wake verification",
          status:"Stuck",
          tone:"danger",
          stat:stuckWakes.count,
          copy:"At least one wake attempt has not reached a terminal verification state.",
          action:action,
          details:[
            {label:"Stuck wakes", value:stuckWakes.count},
            {label:"Source", value:"wake verification ledger"}
          ]
        }));
      }
      return cards.join("");
    }

    function renderActionCard(action, index){
      const severity=String((action || {}).severity || "normal");
      const tone=toneForSeverity(severity);
      const directActionLabel=DIRECT_ACTION_LABELS[(action || {}).id || ""];
      return '<article class="action-card">'
        + '<div class="action-header"><div><div class="section-kicker">Recommended action</div><h3 class="action-title">'+escapeHtml(titleCase((action || {}).id || ("action "+(index + 1))))+'</h3></div>'
        + '<span class="pill '+toneClass(tone)+'">'+escapeHtml(titleCase(severity))+'</span></div>'
        + '<p class="action-copy">'+escapeHtml((action || {}).reason || "No rationale provided.")+'</p>'
        + '<div class="code-block">'+escapeHtml((action || {}).command || "No command attached.")+'</div>'
        + '<div class="action-meta pair-meta">'
        + '<span class="pill '+toneClass((action || {}).safe_to_run ? "success" : "neutral")+'">'+escapeHtml((action || {}).safe_to_run ? "Safe to run" : "Manual follow-up")+'</span>'
        + '<span class="pill '+toneClass((action || {}).mutates_state ? "warning" : "info")+'">'+escapeHtml((action || {}).mutates_state ? "Mutates state" : "Read-only")+'</span>'
        + '</div>'
        + '<div class="button-row">'
        + (directActionLabel ? '<button type="button" class="button button-primary" data-action="apply-recommended-action" data-action-id="'+escapeHtml((action || {}).id || "")+'" data-focus-key="action-run-'+escapeHtml((action || {}).id || "")+'">'+escapeHtml(directActionLabel)+'</button>' : '')
        + '<button type="button" class="button button-secondary" data-action="copy-text" data-copy="'+escapeHtml((action || {}).command || "")+'" data-copy-label="Recommended command" data-focus-key="action-copy-'+escapeHtml((action || {}).id || "")+'">Copy command</button>'
        + '</div>'
        + '</article>';
    }

    function renderPairCard(pairing){
      const agent=String((pairing || {}).agent || "other").toLowerCase();
      const avatarClass=agent === "codex" ? "avatar-codex" : (agent === "claude" ? "avatar-claude" : "avatar-other");
      const status=String((pairing || {}).status || "unknown");
      const threadMatch=(pairing || {}).desktop_thread_title_project_match;
      let threadProof="Thread proof unknown";
      if(threadMatch === true){
        threadProof="Thread title matched project";
      }else if(threadMatch === false && (pairing || {}).desktop_thread_id){
        threadProof="Thread linked, title unproven";
      }else if((pairing || {}).desktop_thread_id){
        threadProof="Thread linked";
      }
      const threadTone=threadMatch == null ? "info" : (threadMatch ? "success" : "warning");
      const wakeReason=(pairing || {}).last_wake_postflight_reason ? titleCase((pairing || {}).last_wake_postflight_reason) : "No wake postflight yet";
      const actionPills=toArray((pairing || {}).available_actions).filter(function(item){
        return Boolean(item && item.enabled && item.effect !== "already_active");
      }).map(function(item){
        return '<span class="pill '+toneClass("success")+'">'+escapeHtml(item && item.label ? item.label : "Action")+'</span>';
      }).join("");
      return '<article class="pair-card">'
        + '<div class="pair-identity">'
        + '<div class="avatar '+avatarClass+'">'+escapeHtml(shortId((pairing || {}).agent || "?").toUpperCase())+'</div>'
        + '<div>'
        + '<div class="pair-header"><div><div class="section-kicker">Pairing</div><h3 class="pair-title">'+escapeHtml(pairingTitle(pairing))+'</h3></div>'
        + '<span class="pill '+toneClass(toneForStatus(status))+'">'+escapeHtml(titleCase(status))+'</span></div>'
        + '<p class="pair-copy">'+escapeHtml("Connected to "+(((pairing || {}).peer_agent) || "peer")+" "+shortId((pairing || {}).peer_session_id || "")+" in a "+(((pairing || {}).relationship) || "paired")+" relationship.")+'</p>'
        + '</div></div>'
        + '<div class="pair-chips">'
        + '<span class="pill '+toneClass("info")+'">'+escapeHtml(titleCase((pairing || {}).role || "unknown"))+'</span>'
        + '<span class="pill '+toneClass(threadTone)+'">'+escapeHtml(threadProof)+'</span>'
        + '<span class="pill '+toneClass((pairing || {}).last_wake_postflight_action ? "success" : "neutral")+'">'+escapeHtml(wakeReason)+'</span>'
        + '</div>'
        + '<dl class="detail-list">'
        + detailRow("Session", shortId((pairing || {}).session_id || ""))
        + detailRow("Peer session", shortId((pairing || {}).peer_session_id || ""))
        + detailRow("Desktop thread", (pairing || {}).desktop_thread_id ? shortId((pairing || {}).desktop_thread_id || "") : "—")
        + detailRow("Bootstrap origin", (pairing || {}).bootstrap_origin || "—")
        + '</dl>'
        + '<details data-detail-key="pair-ids-'+escapeHtml((pairing || {}).session_id || "")+'"><summary>Identifiers</summary><div class="code-block">'
        + escapeHtml("session_id: "+(((pairing || {}).session_id) || "—")+"\\npeer_session_id: "+(((pairing || {}).peer_session_id) || "—")+"\\ndesktop_thread_id: "+(((pairing || {}).desktop_thread_id) || "—"))
        + '</div></details>'
        + (actionPills ? '<div class="pair-meta">'+actionPills+'</div>' : "")
        + '<div class="button-row">'
        + '<button type="button" class="button button-secondary" data-action="copy-text" data-copy="'+escapeHtml((pairing || {}).session_id || "")+'" data-copy-label="Session ID" data-focus-key="pair-session-'+escapeHtml((pairing || {}).session_id || "")+'">Copy session ID</button>'
        + ((pairing || {}).desktop_thread_id ? '<button type="button" class="button button-secondary" data-action="copy-text" data-copy="'+escapeHtml((pairing || {}).desktop_thread_id || "")+'" data-copy-label="Desktop thread ID" data-focus-key="pair-thread-'+escapeHtml((pairing || {}).session_id || "")+'">Copy thread ID</button>' : "")
        + '</div>'
        + '</article>';
    }

    function renderPairQueueCard(items){
      const rows=items.slice(0, 6).map(function(pairing){
        const status=String((pairing || {}).status || "unknown");
        return '<div class="summary-item">'
          + '<div><div class="summary-item-title">'+escapeHtml(pairingTitle(pairing))+'</div><div class="summary-item-copy">'+escapeHtml("Peer "+(((pairing || {}).peer_agent) || "peer")+" "+shortId((pairing || {}).peer_session_id || "")+" · "+titleCase((pairing || {}).relationship || "paired"))+'</div></div>'
          + '<div class="summary-item-actions">'
          + '<span class="pill '+toneClass(toneForStatus(status))+'">'+escapeHtml(titleCase(status))+'</span>'
          + '<button type="button" class="button button-secondary" data-action="copy-text" data-copy="'+escapeHtml((pairing || {}).session_id || "")+'" data-copy-label="Pending pair session ID" data-focus-key="queue-session-'+escapeHtml((pairing || {}).session_id || "")+'">Copy session</button>'
          + '</div>'
          + '</div>';
      }).join("");
      return '<article class="pending-card">'
        + '<div class="pending-header"><div><div class="section-kicker">Pair queue</div><h3 class="pending-title">Pending and secondary routes</h3></div><span class="pill '+toneClass("warning")+'">'+escapeHtml(pluralize(items.length, "route"))+'</span></div>'
        + '<p class="pending-copy">Expanded route cards are reserved for active primaries. Everything else stays compact here so the operator can scan the backlog instead of rereading nearly identical cards.</p>'
        + '<div class="summary-list">'+rows+'</div>'
        + (items.length > 6 ? '<p class="footer-note">'+escapeHtml(String(items.length - 6))+' more secondary route(s) are still hidden from the expanded grid.</p>' : "")
        + '</article>';
    }

    function renderPendingCard(action){
      const status=String((action || {}).status || "pending");
      const priority=String((action || {}).priority || "normal");
      const tone=priority === "high" ? "danger" : (priority === "low" ? "info" : "warning");
      return '<article class="pending-card">'
        + '<div class="pending-header"><div><div class="section-kicker">Pending action</div><h3 class="pending-title">'+escapeHtml((action || {}).summary || "Untitled work item")+'</h3></div>'
        + '<span class="pill '+toneClass(tone)+'">'+escapeHtml(titleCase(priority))+'</span></div>'
        + '<p class="pending-copy">'+escapeHtml((action || {}).details || "No details recorded.")+'</p>'
        + '<div class="pending-chips">'
        + '<span class="pill '+toneClass(toneForStatus(status))+'">'+escapeHtml(titleCase(status))+'</span>'
        + '<span class="pill '+toneClass((action || {}).execution_state === "parked" ? "warning" : "info")+'">'+escapeHtml(titleCase((action || {}).execution_state || "untracked"))+'</span>'
        + '<span class="pill '+toneClass("neutral")+'">'+escapeHtml(((action || {}).owner_agent) || "unknown owner")+'</span>'
        + '</div>'
        + '<dl class="detail-list">'
        + detailRow("Created", formatDate((action || {}).created_at || ""))
        + detailRow("Updated", formatDate((action || {}).updated_at || ""))
        + detailRow("Related session", (action || {}).related_session_id ? shortId((action || {}).related_session_id || "") : "—")
        + detailRow("Message ID", (action || {}).message_id ? shortId((action || {}).message_id || "") : "—")
        + '</dl>'
        + '<details data-detail-key="pending-'+escapeHtml(String((action || {}).id || (action || {}).message_id || (action || {}).summary || ""))+'"><summary>Full details</summary><div class="code-block">'+escapeHtml("related_session_id: "+(((action || {}).related_session_id) || "—")+"\\nmessage_id: "+(((action || {}).message_id) || "—")+"\\n\\n"+(((action || {}).details) || "No details recorded."))+'</div></details>'
        + '<div class="button-row">'
        + '<button type="button" class="button button-secondary" data-action="copy-text" data-copy="'+escapeHtml((action || {}).details || "")+'" data-copy-label="Pending action details" data-focus-key="pending-details-'+escapeHtml(String((action || {}).id || (action || {}).message_id || ""))+'">Copy details</button>'
        + ((action || {}).message_id ? '<button type="button" class="button button-secondary" data-action="copy-text" data-copy="'+escapeHtml((action || {}).message_id || "")+'" data-copy-label="Pending action message ID" data-focus-key="pending-message-'+escapeHtml(String((action || {}).message_id || ""))+'">Copy message ID</button>' : "")
        + '</div>'
        + '</article>';
    }

    function renderContractCard(contract){
      const status=String((contract || {}).derived_status || "unknown");
      return '<article class="contract-card">'
        + '<div class="contract-header"><div><div class="section-kicker">Contract</div><h3 class="contract-title">'+escapeHtml((contract || {}).friendly_name || ((contract || {}).short_link_id || "Unnamed contract"))+'</h3></div>'
        + '<span class="pill '+toneClass(toneForStatus(status))+'">'+escapeHtml(titleCase(status))+'</span></div>'
        + '<p class="contract-copy">'+escapeHtml("Permission tier: "+(((contract || {}).permission_tier) || "unknown")+". Local role: "+(((contract || {}).local_role) || "n/a")+".")+'</p>'
        + '<dl class="detail-list">'
        + detailRow("Link ID", (contract || {}).link_id || "—")
        + detailRow("Advisor", (((contract || {}).advisor || {}).project) || "—")
        + detailRow("Expiration", formatDurationSeconds((contract || {}).seconds_until_expiration))
        + '</dl>'
        + '<div class="contract-chips">'
        + '<span class="pill '+toneClass("info")+'">'+escapeHtml(titleCase((contract || {}).permission_tier || "unknown"))+'</span>'
        + ((contract || {}).local_alias ? '<span class="pill '+toneClass("neutral")+'">'+escapeHtml((contract || {}).local_alias || "")+'</span>' : "")
        + '</div>'
        + '<div class="button-row">'
        + '<button type="button" class="button button-secondary" data-action="alias-contract" data-link-id="'+escapeHtml((contract || {}).link_id || "")+'" data-alias="'+escapeHtml((contract || {}).local_alias || "")+'" data-focus-key="contract-alias-'+escapeHtml((contract || {}).link_id || "")+'">Rename alias</button>'
        + '<button type="button" class="button button-secondary" data-action="renew-contract" data-link-id="'+escapeHtml((contract || {}).link_id || "")+'" data-focus-key="contract-renew-'+escapeHtml((contract || {}).link_id || "")+'">Renew</button>'
        + '<button type="button" class="button button-danger" data-action="revoke-contract" data-link-id="'+escapeHtml((contract || {}).link_id || "")+'" data-focus-key="contract-revoke-'+escapeHtml((contract || {}).link_id || "")+'">Revoke</button>'
        + '</div>'
        + '</article>';
    }

    function renderRejectionCard(item){
      const scope=(item || {}).project || (item || {}).session_id || "unknown scope";
      return '<article class="rejection-card">'
        + '<div class="rejection-header"><div><div class="section-kicker">Remote authority rejection</div><h3 class="rejection-title">'+escapeHtml(titleCase((item || {}).action || "request rejected"))+'</h3></div>'
        + '<span class="pill '+toneClass("danger")+'">Rejected</span></div>'
        + '<p class="rejection-copy">'+escapeHtml((item || {}).reason || (item || {}).message || "A remote authority request was rejected.")+'</p>'
        + '<dl class="detail-list">'
        + detailRow("When", formatDate((item || {}).timestamp || ""))
        + detailRow("Scope", scope)
        + detailRow("Agent", (item || {}).agent || "—")
        + '</dl>'
        + '<details><summary>Audit payload</summary><div class="code-block">'+escapeHtml(JSON.stringify(item || {}, null, 2))+'</div></details>'
        + '</article>';
    }

    function emptyState(title, copy){
      return '<article class="empty-state">'
        + emptyIllustration()
        + '<div><h3 class="surface-title">'+escapeHtml(title)+'</h3><p class="empty-copy">'+escapeHtml(copy)+'</p></div>'
        + '</article>';
    }

    function render(payload){
      latestPayload=payload || latestPayload || {};
      const overview=((latestPayload.data || {}).overview) || {};
      const health=overview.health || {};
      const surfaces=overview.status_surfaces || {};
      const readStatus=overview.read_status || {};
      const recommended=toArray(overview.recommended_actions);
      const pairings=toArray(overview.pairings);
      const contracts=toArray(overview.contracts);
      const pending=toArray(overview.pending_actions);
      const rejections=toArray(overview.remote_authority_rejections);
      const activePairings=pairings.filter(function(item){ return String(item.status || "").toLowerCase() === "active"; }).length;
      const activePairingsList=pairings.filter(function(item){ return String(item.status || "").toLowerCase() === "active"; });
      const queuedPairings=pairings.filter(function(item){ return String(item.status || "").toLowerCase() !== "active"; });
      const unread=(surfaces.backpressure || {}).unread_work_count || 0;
      const healthTone=toneForStatus(health.overall_status || latestPayload.status || "unknown");
      const generatedAt=overview.generated_at || (health.snapshot_ts) || "";
      const spotlightCommand=(health.recovery_hint) || ((recommended[0] || {}).command) || "";
      const spotlightReason=(recommended[0] && recommended[0].reason) ? recommended[0].reason : "Start with the health-published recovery path before scanning the lower sections.";
      const heroCopy=(latestPayload.ok === false)
        ? escapeHtml(latestPayload.message || "The dashboard could not load a complete overview.")
        : escapeHtml("The bridge is scoped to "+(overview.caller && overview.caller.project ? overview.caller.project : "the active context")+". Use the cards below to see what is healthy, what is drifting, and which commands are safe to run next.");
      const metrics=[
        metricCard("Overall health", titleCase(health.overall_status || latestPayload.status || "unknown"), (health.errors || []).length ? "Errors are present in the health snapshot." : "Health rollup from bridge state, monitors, and read surfaces."),
        metricCard("Active pairings", formatCount(activePairings), activePairings ? "Primary bridge routes are live." : "No active same-project pairings are visible."),
        metricCard("Unread work", formatCount(unread), unread ? "Some unread work still needs attention." : "No inbox backlog is blocking the bridge."),
        metricCard("Pending actions", formatCount(pending.length), pending.length ? "There is still parked or pending work to resolve." : "The pending action queue is clear.")
      ].join("");
      const spotlightActionId=(recommended[0] || {}).id || "";
      const spotlightDirectLabel=DIRECT_ACTION_LABELS[spotlightActionId] || "";
      const recommendedForGrid=(spotlightActionId && recommended.length > 1) ? recommended.slice(1, 8) : recommended.slice(0, 8);
      const surfaceOrder=["dashboard_reads","backpressure","claude_monitor","stale_unread_watchdog","catchup","contracts","policy_drift","guardrail_debt"];
      const attentionSurfaces=surfaceOrder.filter(function(key){ return surfaces[key] && surfaceNeedsAttention(key, surfaces[key]); });
      const stableSurfaces=surfaceOrder.filter(function(key){ return surfaces[key] && !surfaceNeedsAttention(key, surfaces[key]); });
      const coreCauseCards=renderCoreCauseCards(health, recommended);
      const secondarySurfaceCards=attentionSurfaces.map(function(key){ return renderSurfaceCard(key, surfaces[key]); }).join("");
      const fallbackSurfaceCards=(coreCauseCards || secondarySurfaceCards) ? "" : (
        healthTone === "danger"
          ? '<article class="status-card"><div class="surface-header"><div><div class="section-kicker">Health alert</div><h3 class="surface-title">Top-level health is still broken</h3></div><span class="pill '+toneClass("danger")+'">Broken</span></div><div class="surface-stat">'+escapeHtml(String(recommended.length || 1))+'</div><p class="surface-copy">'+escapeHtml("The primary recovery path above addresses the current broken-health signal. Use the follow-up sections for secondary work only.")+'</p></article>'
          : ((recommendedForGrid.length || pending.length)
            ? '<article class="status-card"><div class="surface-header"><div><div class="section-kicker">Status surface</div><h3 class="surface-title">Core surfaces are stable, but follow-up work remains</h3></div><span class="pill '+toneClass("warning")+'">Follow-up</span></div><div class="surface-stat">'+escapeHtml(String(recommendedForGrid.length + pending.length))+'</div><p class="surface-copy">The bridge is calm at the surface level, but there are still queued actions below that need operator attention.</p></article>'
            : '<article class="status-card"><div class="surface-header"><div><div class="section-kicker">Status surface</div><h3 class="surface-title">All monitored surfaces stable</h3></div><span class="pill '+toneClass("success")+'">Stable</span></div><div class="surface-stat">0</div><p class="surface-copy">Nothing across reads, inbox pressure, policy drift, contracts, or guardrail debt currently needs escalation.</p></article>')
      );
      const surfaceCards=coreCauseCards+secondarySurfaceCards+fallbackSurfaceCards;
      const stableSurfaceStrip=stableSurfaces.length ? '<div class="stable-strip">'+stableSurfaces.map(function(key){ return renderStableSurfaceChip(key, surfaces[key]); }).join("")+'</div>' : "";
      const actionCards=recommendedForGrid.length ? recommendedForGrid.map(renderActionCard).join("") : emptyState("Primary recovery already pinned above", "No secondary follow-up actions remain beyond the spotlighted recovery step.");
      const pairCards=activePairingsList.length ? '<div class="cards-grid">'+activePairingsList.map(renderPairCard).join("")+'</div>' : emptyState("No active primary pairings", "There are no active same-project routes in this scope, so nothing is expanded as a live primary.");
      const pairQueue=queuedPairings.length ? '<div class="cards-grid">'+renderPairQueueCard(queuedPairings)+'</div>' : "";
      const pendingCards=pending.length ? pending.map(renderPendingCard).join("") : emptyState("No pending actions", "There is no parked or unresolved action debt in the current scope.");
      const contractCards=contracts.length ? contracts.map(renderContractCard).join("") : emptyState("No cross-project contracts", "Nothing is currently linked across projects, so there are no renew or revoke actions to take.");
      const rejectionCards=rejections.length ? rejections.slice(-6).reverse().map(renderRejectionCard).join("") : emptyState("No remote rejections", "No remote authority requests have been rejected in this tenant recently.");
      const debtSummary=[];
      if(unread){
        debtSummary.push(pluralize(unread, "unread item"));
      }
      if(pending.length){
        debtSummary.push(pluralize(pending.length, "pending action"));
      }
      if(recommended.length){
        debtSummary.push(pluralize(recommended.length, "recommended action"));
      }
      const overallMessage=latestPayload.ok === false
        ? (latestPayload.message || "Dashboard refresh failed.")
        : titleCase(health.overall_status || latestPayload.status || "unknown")+" health · "+(debtSummary.length ? debtSummary.join(" • ") : "No immediate operator debt")+" · refreshed "+formatRelative(generatedAt)+".";
      const heroTitle=latestPayload.ok === false
        ? "Dashboard refresh needs attention."
        : (healthTone === "danger"
          ? titleCase(health.overall_status || "broken")+" health needs decisive recovery."
          : (debtSummary.length ? "Bridge health is stable, but queued work still needs decisions." : "Bridge health is steady and ready."));
      const recommendedSectionCopy=spotlightActionId
        ? "The primary recovery path stays pinned in the hero. The actions here are the remaining follow-up moves."
        : "Commands are promoted into action cards with severity, safety hints, and one-click copy affordances.";
      setStatus(overallMessage, healthTone);
      const signature=payloadSignature(latestPayload);
      if(signature === lastRenderSignature){
        updateRefreshToggle();
        return;
      }
      lastRenderSignature=signature;
      const openDetailKeys=captureOpenDetails();
      const viewportState=captureViewportState();
      byId("dashboard-root").innerHTML=
        '<section class="panel hero">'
        + '<div class="hero-copy">'
        + '<div class="hero-chips">'
        + '<span class="pill '+toneClass(healthTone)+'">'+escapeHtml(titleCase(health.overall_status || latestPayload.status || "unknown"))+'</span>'
        + '<span class="pill '+toneClass(toneForStatus(readStatus.status || "unknown"))+'">'+escapeHtml(titleCase(readStatus.status || "unknown"))+' reads</span>'
        + '<span class="pill '+toneClass("info")+'">'+escapeHtml((overview.caller || {}).project || "global scope")+'</span>'
        + '<span class="pill '+toneClass("neutral")+'">'+escapeHtml(formatDate(generatedAt))+'</span>'
        + '</div>'
        + '<h2 class="hero-title">'+escapeHtml(heroTitle)+'</h2>'
        + '<p class="hero-subtitle">'+heroCopy+'</p>'
        + (spotlightCommand ? '<div class="spotlight-card"><div class="section-kicker">Primary recovery path</div><h3 class="spotlight-title">'+escapeHtml(titleCase(health.overall_status || "needs attention"))+' needs one decisive next move.</h3><p class="spotlight-copy">'+escapeHtml(spotlightReason)+'</p><div class="code-block">'+escapeHtml(spotlightCommand)+'</div><div class="button-row">'+(spotlightDirectLabel ? '<button type="button" class="button button-primary" data-action="apply-recommended-action" data-action-id="'+escapeHtml(spotlightActionId)+'" data-focus-key="spotlight-run-primary">'+escapeHtml(spotlightDirectLabel)+'</button>' : '')+'<button type="button" class="button '+(spotlightDirectLabel ? 'button-secondary' : 'button-primary')+'" data-action="copy-text" data-copy="'+escapeHtml(spotlightCommand)+'" data-copy-label="Primary recovery command" data-focus-key="spotlight-copy-primary">Copy primary action</button></div></div>' : '')
        + '<div class="metric-grid">'+metrics+'</div>'
        + '<p class="footer-note">'+escapeHtml((health.recovery_hint) ? "The current health snapshot includes a recovery hint, and the top-bar copy button always mirrors it." : "No recovery hint is published for the current state.")+'</p>'
        + '</div>'
        + '<div class="hero-visual">'+heroVisual(overview)+'</div>'
        + '</section>'
        + '<section class="panel section"><div class="section-head"><div><span class="section-kicker">At a glance</span><h2 class="section-title">Operational signals</h2><p class="section-copy">Core health causes stay expanded first, then secondary surfaces that need attention. Stable systems collapse into a lightweight readiness strip so the first scan stays sharp.</p></div></div><div class="status-grid">'+surfaceCards+'</div>'+stableSurfaceStrip+'</section>'
        + '<section class="panel section"><div class="section-head"><div><span class="section-kicker">Do next</span><h2 class="section-title">Recommended actions</h2><p class="section-copy">'+escapeHtml(recommendedSectionCopy)+'</p></div></div><div class="action-grid">'+actionCards+'</div></section>'
        + '<section class="meta-grid">'
        + '<section class="panel section"><div class="section-head"><div><span class="section-kicker">Live routes</span><h2 class="section-title">Pairings</h2><p class="section-copy">Active primaries stay expanded. Secondary and pending routes are summarized separately so the operator can scan what matters now first.</p></div></div><div class="section-stack">'+pairCards+pairQueue+'</div></section>'
        + '<section class="panel section"><div class="section-head"><div><span class="section-kicker">Work queue</span><h2 class="section-title">Pending actions</h2><p class="section-copy">Parked work is visible, prioritized, and copyable so nothing important disappears into the ledger.</p></div></div><div class="cards-grid">'+pendingCards+'</div></section>'
        + '</section>'
        + '<section class="meta-grid">'
        + '<section class="panel section"><div class="section-head"><div><span class="section-kicker">Trust boundaries</span><h2 class="section-title">Contracts</h2><p class="section-copy">Cross-project contracts get first-class controls with renew, revoke, and alias actions whenever links are present.</p></div></div><div class="cards-grid">'+contractCards+'</div></section>'
        + '<section class="panel section"><div class="section-head"><div><span class="section-kicker">Audit trail</span><h2 class="section-title">Remote rejections</h2><p class="section-copy">Remote authority failures stay visible so policy disagreements are obvious instead of buried in logs.</p></div></div><div class="cards-grid">'+rejectionCards+'</div></section>'
        + '</section>';
      restoreOpenDetails(openDetailKeys);
      restoreViewportState(viewportState);
      updateRefreshToggle();
    }

    async function refresh(force){
      if((!force && !autoRefreshEnabled) || modalResolver){
        return;
      }
      setBusy(true);
      try{
        const response=await fetch(overviewUrl());
        const payload=await response.json();
        if(!response.ok){
          throw new Error(payload.message || payload.error || "dashboard refresh failed");
        }
        render(payload);
      }catch(error){
        setStatus("Refresh error: "+String(error), "danger");
        showToast("Refresh error: "+String(error), "danger");
      }finally{
        setBusy(false);
      }
    }

    async function mutateContract(button, path, payload, successTone){
      button.disabled=true;
      try{
        const data=await apiPost(path, payload);
        showToast(data.message || "Contract updated.", successTone || "success");
        await refresh(true);
      }catch(error){
        showToast(String(error), "danger");
        setStatus(String(error), "danger");
      }finally{
        button.disabled=false;
      }
    }

    async function runRecommendedAction(button){
      button.disabled=true;
      try{
        const data=await apiPost("/api/recommended-action", {
          action_id:button.getAttribute("data-action-id") || "",
          project:PROJECT
        });
        showToast(data.message || "Recommended action applied.", "success");
        await refresh(true);
      }catch(error){
        showToast(String(error), "danger");
        setStatus(String(error), "danger");
      }finally{
        button.disabled=false;
      }
    }

    document.addEventListener("click", async function(event){
      const modalButton=event.target.closest("button[data-modal-action]");
      if(modalButton){
        if(modalButton.getAttribute("data-modal-action") === "cancel"){
          closeModal({ confirmed:false, value:null });
          return;
        }
        const input=byId("modal-input");
        closeModal({ confirmed:true, value:input ? input.value : null });
        return;
      }
      const button=event.target.closest("button[data-action]");
      if(!button){
        return;
      }
      const action=button.getAttribute("data-action");
      if(action === "refresh"){
        await refresh(true);
        return;
      }
      if(action === "toggle-refresh"){
        autoRefreshEnabled=!autoRefreshEnabled;
        updateRefreshToggle();
        showToast(autoRefreshEnabled ? "Live refresh resumed." : "Live refresh paused.", autoRefreshEnabled ? "info" : "warning");
        if(autoRefreshEnabled){
          await refresh(true);
        }
        return;
      }
      if(action === "copy-recovery"){
        const overview=((latestPayload.data || {}).overview) || {};
        const health=overview.health || {};
        await copyText(health.recovery_hint || "", "Recovery hint");
        return;
      }
      if(action === "shutdown"){
        await shutdownDashboard();
        return;
      }
      if(action === "copy-text"){
        await copyText(button.getAttribute("data-copy") || "", button.getAttribute("data-copy-label") || "Text");
        return;
      }
      if(action === "apply-recommended-action"){
        await runRecommendedAction(button);
        return;
      }
      if(action === "renew-contract"){
        const ttl=await promptModal(
          "Renew contract",
          "Choose how long this cross-project contract should stay active.",
          "TTL in minutes",
          "120",
          "Renew",
          "number"
        );
        if(ttl == null){
          return;
        }
        const ttlMinutes=parseInt(ttl, 10);
        if(!Number.isFinite(ttlMinutes) || ttlMinutes <= 0){
          showToast("Enter a positive TTL in minutes.", "warning");
          return;
        }
        await mutateContract(button, "/api/renew", {
          link_id:button.getAttribute("data-link-id") || "",
          project:PROJECT,
          ttl_minutes:ttlMinutes,
          confirm_renew:true
        }, "success");
        return;
      }
      if(action === "revoke-contract"){
        const confirmed=await confirmModal(
          "Revoke contract?",
          "This will revoke the selected cross-project contract and record the action in bridge state.",
          "Revoke",
          "danger"
        );
        if(!confirmed){
          return;
        }
        await mutateContract(button, "/api/revoke", {
          link_id:button.getAttribute("data-link-id") || "",
          project:PROJECT,
          confirm_revoke:true,
          reason:"Revoked from Agent Bridge dashboard"
        }, "warning");
        return;
      }
      if(action === "alias-contract"){
        const alias=await promptModal(
          "Rename contract alias",
          "Set a clearer local alias for this contract so it reads well in the dashboard.",
          "Alias",
          button.getAttribute("data-alias") || "",
          "Save alias",
          "text"
        );
        if(alias == null){
          return;
        }
        await mutateContract(button, "/api/alias", {
          link_id:button.getAttribute("data-link-id") || "",
          project:PROJECT,
          alias:alias
        }, "info");
      }
    });

    document.addEventListener("keydown", function(event){
      if(event.key === "Escape" && modalResolver){
        closeModal({ confirmed:false, value:null });
      }
      if(event.key === "Tab" && modalResolver){
        const focusables=Array.from(document.querySelectorAll("#modal-root button, #modal-root input")).filter(function(node){
          return node instanceof HTMLElement && !node.disabled;
        });
        if(focusables.length){
          const first=focusables[0];
          const last=focusables[focusables.length - 1];
          if(event.shiftKey && document.activeElement === first){
            event.preventDefault();
            last.focus();
          }else if(!event.shiftKey && document.activeElement === last){
            event.preventDefault();
            first.focus();
          }
        }
      }
      if(event.key === "Enter" && modalResolver && event.target && event.target.id === "modal-input"){
        closeModal({ confirmed:true, value:event.target.value });
      }
    });

    render(INITIAL_PAYLOAD);
    refresh();
    updateRefreshToggle();
    refreshTimer=setInterval(refresh, 5000);
  </script>
</body>
</html>
"""
    return (
        template
        .replace("__DASHBOARD_CSRF_META__", _html_escape(csrf_token))
        .replace("__DASHBOARD_TOKEN_JSON__", _script_json(token))
        .replace("__DASHBOARD_CSRF_JSON__", _script_json(csrf_token))
        .replace("__DASHBOARD_PROJECT_JSON__", _script_json(project or ""))
        .replace("__DASHBOARD_INITIAL_JSON__", _script_json(initial_payload))
    )


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
        repo_root: Optional[str],
    ) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.bridge = bridge
        self.token = token
        self.csrf_token = csrf_token
        self.default_agent = default_agent
        self.default_project = default_project
        self.repo_root = repo_root
        self.shutdown_requested = threading.Event()


class BridgeDashboardHandler(BaseHTTPRequestHandler):
    server: BridgeDashboardHTTPServer

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _recovery_cwd(self) -> str:
        if self.server.repo_root:
            return self.server.repo_root
        watcher_config = watcher_config_path_for_state_dir(self.server.bridge.state_dir)
        try:
            payload = json.loads(watcher_config.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("repo_root"):
                return str(payload["repo_root"])
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return str(Path.cwd())

    def _restart_watcher(self, *, agent: str, project: Optional[str]) -> BridgeResult:
        watcher_config = watcher_config_path_for_state_dir(self.server.bridge.state_dir)
        cwd = self._recovery_cwd()
        before = inspect_bridge_runtime(
            state_dir=self.server.bridge.state_dir,
            agent=agent,
            cwd=cwd,
            project=project,
            watcher_config=watcher_config,
        )
        if before.get("bridge_state") in {"UNBOOTSTRAPPED", "SUPERSEDED"}:
            return BridgeResult(
                False,
                "needs_user_attention",
                "Watcher restart needs a live bootstrapped session before it can safely self-heal.",
                {"action_id": "restart_watcher", "before": before},
            )
        result = recover_bridge_session(
            state_dir=self.server.bridge.state_dir,
            agent=agent,
            cwd=cwd,
            project=project,
            watcher_config=watcher_config,
            start_watcher=True,
        )
        after = result.get("after", {}) if isinstance(result, dict) else {}
        recovered = after.get("bridge_state") == "WATCHING"
        return BridgeResult(
            recovered,
            "action_applied" if recovered else "needs_user_attention",
            str(result.get("message") or "Watcher recovery finished.")
            if isinstance(result, dict)
            else "Watcher recovery finished.",
            {"action_id": "restart_watcher", "result": result},
        )

    def _run_safe_recommended_action(self, payload: Dict[str, Any]) -> BridgeResult:
        action_id = str(payload.get("action_id") or "").strip()
        agent = str(payload.get("agent") or self.server.default_agent)
        project_value = payload.get("project") or self.server.default_project
        project = str(project_value) if project_value else None
        if action_id == "restart_watcher":
            return self._restart_watcher(agent=agent, project=project)
        if action_id == "compact_stale_server_markers":
            result = reap_stale_server_pids(self.server.bridge.state_dir, max_age_hours=0, dry_run=False)
            return BridgeResult(
                True,
                "action_applied",
                "Reaped %d stale server marker(s) after checking %d marker(s)."
                % (int(result.get("removed") or 0), int(result.get("checked") or 0)),
                {"action_id": action_id, "result": result},
            )
        if action_id == "backfill_read_receipts":
            result = self.server.bridge.receipt_debt_cleanup(agent=agent, apply=True)
            return BridgeResult(
                result.ok,
                result.status,
                result.message,
                {"action_id": action_id, **(result.data or {})},
            )
        return BridgeResult(False, "rejected", "recommended action %s cannot be executed directly" % action_id)

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
            output_format = str(self._query().get("format") or "json")
            result = self.server.bridge.dashboard_overview(agent=agent, project=project, format=output_format)
            _json_response(self, HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST, {
                "ok": result.ok,
                "status": result.status,
                "message": result.message,
                "data": result.data,
                "csrf_token": self.server.csrf_token,
            })
            return
        if parsed.path in {"", "/"}:
            result = self.server.bridge.dashboard_overview(agent=agent, project=project, format="json")
            initial_payload = {
                "ok": result.ok,
                "status": result.status,
                "message": result.message,
                "data": result.data,
                "csrf_token": self.server.csrf_token,
            }
            body = _dashboard_html(
                token=self.server.token,
                csrf_token=self.server.csrf_token,
                project=project,
                initial_payload=initial_payload,
            )
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
        elif parsed.path == "/api/recommended-action":
            result = self._run_safe_recommended_action(payload)
        elif parsed.path == "/api/shutdown":
            self.server.shutdown_requested.set()
            _json_response(
                self,
                HTTPStatus.OK,
                {"ok": True, "status": "shutdown_requested", "message": "Dashboard shutdown requested."},
            )
            threading.Thread(target=self.server.shutdown, name="agent-bridge-dashboard-shutdown", daemon=True).start()
            return
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
    repo_root: Optional[str] = None,
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
        repo_root=repo_root,
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
