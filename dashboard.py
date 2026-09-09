import asyncio
import json
import uuid
import os
from datetime import datetime, date, timedelta
from aiohttp import web
from aiohttp.web import WebSocketResponse
from dotenv import load_dotenv
import weakref

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

load_dotenv()
import appointments_db
import config

# ─────────────────────────────────────────────
#  WebSocket broadcast registry
# ─────────────────────────────────────────────
_ws_clients: weakref.WeakSet = weakref.WeakSet()

async def broadcast(payload: dict):
    """Broadcast a JSON payload to every connected WebSocket dashboard client."""
    msg = json.dumps(payload)
    for ws in list(_ws_clients):
        try:
            if not ws.closed:
                await ws.send_str(msg)
        except Exception:
            pass

# ─────────────────────────────────────────────
#  Active-Call registry  (in-memory, keyed by session_id)
# ─────────────────────────────────────────────
_active_calls: dict = {}   # session_id -> { caller, started_at, transcript_lines }


# ─────────────────────────────────────────────
#  Public helpers used by agent.py / router.py
# ─────────────────────────────────────────────
async def notify_call_started(session_id: str, caller_phone: str | None = None):
    _active_calls[session_id] = {
        "session_id": session_id,
        "caller": caller_phone or "Web Caller",
        "started_at": datetime.now().isoformat(),
        "transcript_lines": []
    }
    await broadcast({"type": "call_started", "session_id": session_id,
                     "caller": caller_phone or "Web Caller",
                     "started_at": _active_calls[session_id]["started_at"]})


async def notify_transcript_update(session_id: str, role: str, text: str):
    if session_id in _active_calls:
        _active_calls[session_id]["transcript_lines"].append({"role": role, "text": text})
    await broadcast({"type": "transcript", "session_id": session_id, "role": role, "text": text})


async def notify_call_ended(session_id: str):
    _active_calls.pop(session_id, None)
    await broadcast({"type": "call_ended", "session_id": session_id})


# ─────────────────────────────────────────────
#  HTML  (Production Dashboard with Lucide Icons)
# ─────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Clinigo Command Center — AI Reception Dashboard</title>
    <meta name="description" content="Professional AI-powered healthcare reception management system with live call monitoring and appointment analytics.">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:ital,wght@0,300;0,400;0,500;0,600;0,700;0,800;1,400&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <script src="https://unpkg.com/lucide@latest"></script>
    <style>
        :root{
            --c-bg:#050b18;
            --c-surface:#0c1526;
            --c-panel:rgba(10,18,38,0.8);
            --c-border:rgba(255,255,255,0.07);
            --c-border-h:rgba(255,255,255,0.16);
            --c-primary:#4f83ff;
            --c-primary-glow:rgba(79,131,255,0.35);
            --c-cyan:#22d3ee;
            --c-cyan-glow:rgba(34,211,238,0.3);
            --c-emerald:#10b981;
            --c-emerald-bg:rgba(16,185,129,0.1);
            --c-amber:#f59e0b;
            --c-amber-bg:rgba(245,158,11,0.1);
            --c-rose:#f43f5e;
            --c-rose-bg:rgba(244,63,94,0.1);
            --c-violet:#8b5cf6;
            --c-violet-bg:rgba(139,92,246,0.1);
            --c-text:#f1f5f9;
            --c-sub:#94a3b8;
            --c-muted:#475569;
            --radius-lg:18px;
            --radius-md:12px;
            --radius-sm:8px;
            --shadow-lg:0 25px 50px -12px rgba(0,0,0,0.75);
            --shadow-card:0 8px 24px -8px rgba(0,0,0,0.6);
        }
        *{box-sizing:border-box;margin:0;padding:0}
        html,body{height:100%;overflow:hidden}
        body{
            font-family:'Plus Jakarta Sans',system-ui,sans-serif;
            background:var(--c-bg);
            background-image:
                radial-gradient(ellipse 80% 60% at 50% -20%,rgba(79,131,255,0.12),transparent),
                radial-gradient(ellipse 60% 50% at 90% 80%,rgba(139,92,246,0.08),transparent),
                radial-gradient(ellipse 40% 40% at 0% 50%,rgba(34,211,238,0.06),transparent);
            color:var(--c-text);
            display:flex;
            flex-direction:column;
        }
        .shell{display:flex;flex:1;overflow:hidden}

        /* ── Sidebar ── */
        .sidebar{
            width:240px;flex-shrink:0;
            background:rgba(8,15,30,0.9);
            border-right:1px solid var(--c-border);
            display:flex;flex-direction:column;
            padding:1.25rem 0;
            backdrop-filter:blur(20px);
        }
        .brand{
            display:flex;align-items:center;gap:0.75rem;
            padding:0 1.25rem 1.5rem;
            border-bottom:1px solid var(--c-border);
            margin-bottom:1rem;
        }
        .brand-icon{
            width:40px;height:40px;border-radius:12px;
            background:linear-gradient(135deg,var(--c-primary),var(--c-cyan));
            display:flex;align-items:center;justify-content:center;
            color:#fff;
            box-shadow:0 0 20px var(--c-primary-glow);
            flex-shrink:0;
        }
        .brand-title{font-size:1.1rem;font-weight:800;letter-spacing:-0.02em;line-height:1.1}
        .brand-sub{font-size:0.7rem;color:var(--c-sub);font-weight:500;letter-spacing:0.04em;text-transform:uppercase}

        .nav-section-label{
            font-size:0.65rem;font-weight:700;color:var(--c-muted);
            text-transform:uppercase;letter-spacing:0.1em;
            padding:0.5rem 1.25rem 0.35rem;
        }
        .nav-item{
            display:flex;align-items:center;gap:0.75rem;
            padding:0.65rem 1.25rem;margin:0.1rem 0.6rem;
            border-radius:var(--radius-sm);
            font-size:0.9rem;font-weight:500;color:var(--c-sub);
            cursor:pointer;transition:all 0.2s ease;
            border:none;background:none;width:calc(100% - 1.2rem);text-align:left;
        }
        .nav-item:hover{background:rgba(255,255,255,0.04);color:var(--c-text)}
        .nav-item.active{
            background:rgba(79,131,255,0.12);color:#93c5fd;
            border:1px solid rgba(79,131,255,0.2);
        }
        .nav-item.active .nav-icon{color:var(--c-primary)}
        .nav-icon{width:18px;height:18px;flex-shrink:0;display:inline-flex;align-items:center;justify-content:center}
        .nav-badge{
            margin-left:auto;background:var(--c-rose);color:#fff;
            font-size:0.7rem;font-weight:700;padding:0.15rem 0.45rem;border-radius:999px;
            min-width:20px;text-align:center;
        }
        .live-badge{
            margin-left:auto;background:rgba(16,185,129,0.2);color:var(--c-emerald);
            border:1px solid rgba(16,185,129,0.4);
            font-size:0.65rem;font-weight:700;padding:0.15rem 0.5rem;border-radius:999px;
            animation:pulse-text 2s infinite;
        }
        @keyframes pulse-text{0%,100%{opacity:1}50%{opacity:0.6}}

        .sidebar-footer{
            margin-top:auto;padding:1rem 1.25rem;
            border-top:1px solid var(--c-border);
        }
        .system-status{
            display:flex;align-items:center;gap:0.6rem;
            font-size:0.8rem;color:var(--c-sub);
        }
        .status-dot{
            width:8px;height:8px;border-radius:50%;
            background:var(--c-amber);
            box-shadow:0 0 6px var(--c-amber);
            transition:all 0.5s;
        }
        .status-dot.connected{
            background:var(--c-emerald);
            box-shadow:0 0 6px var(--c-emerald);
            animation:pulse-dot 2s infinite;
        }
        @keyframes pulse-dot{0%,100%{opacity:1;transform:scale(1)}50%{opacity:0.7;transform:scale(0.85)}}

        /* ── Main Content ── */
        .main{flex:1;display:flex;flex-direction:column;overflow:hidden}

        /* ── Topbar ── */
        .topbar{
            display:flex;align-items:center;justify-content:space-between;
            padding:0.9rem 2rem;
            background:rgba(8,15,30,0.7);
            border-bottom:1px solid var(--c-border);
            backdrop-filter:blur(16px);
            flex-shrink:0;
        }
        .topbar-left{display:flex;align-items:center;gap:1rem}
        .page-title{font-size:1.25rem;font-weight:700;letter-spacing:-0.02em}
        .page-sub{font-size:0.8rem;color:var(--c-sub);margin-top:1px}
        .topbar-right{display:flex;align-items:center;gap:0.75rem}
        .clock{
            font-family:'JetBrains Mono',monospace;font-size:0.85rem;color:var(--c-sub);
            background:rgba(255,255,255,0.04);border:1px solid var(--c-border);
            padding:0.4rem 0.8rem;border-radius:var(--radius-sm);
        }
        .btn{
            display:inline-flex;align-items:center;gap:0.5rem;
            padding:0.55rem 1.1rem;border-radius:var(--radius-sm);
            font-size:0.88rem;font-weight:600;cursor:pointer;border:none;
            transition:all 0.2s;font-family:'Plus Jakarta Sans',sans-serif;
        }
        .btn-primary{
            background:linear-gradient(135deg,var(--c-primary),#3b6fe0);color:#fff;
            box-shadow:0 4px 15px var(--c-primary-glow);
        }
        .btn-primary:hover{transform:translateY(-1px);box-shadow:0 6px 20px var(--c-primary-glow)}
        .btn-ghost{background:rgba(255,255,255,0.05);color:var(--c-sub);border:1px solid var(--c-border)}
        .btn-ghost:hover{background:rgba(255,255,255,0.09);color:var(--c-text)}
        .btn-rose{background:rgba(244,63,94,0.1);color:#fb7185;border:1px solid rgba(244,63,94,0.2)}
        .btn-rose:hover{background:var(--c-rose);color:#fff}
        .btn-sm{padding:0.4rem 0.8rem;font-size:0.8rem}

        /* ── Page Content Area ── */
        .page-content{flex:1;overflow-y:auto;padding:1.75rem 2rem}

        /* ── Section visibility ── */
        .section{display:none}
        .section.active{display:block;animation:fadeSlide 0.25s ease-out}
        @keyframes fadeSlide{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}

        /* ── KPI Cards ── */
        .kpi-row{
            display:grid;grid-template-columns:repeat(5,1fr);
            gap:1rem;margin-bottom:1.5rem;
        }
        .kpi{
            background:var(--c-panel);
            border:1px solid var(--c-border);
            border-radius:var(--radius-md);
            padding:1.25rem;
            position:relative;overflow:hidden;
            transition:all 0.2s;backdrop-filter:blur(12px);
        }
        .kpi:hover{border-color:var(--c-border-h);transform:translateY(-2px)}
        .kpi::after{
            content:'';position:absolute;top:0;left:0;right:0;height:2px;
            background:var(--accent,var(--c-primary));
        }
        .kpi.emerald{--accent:var(--c-emerald)}
        .kpi.cyan{--accent:var(--c-cyan)}
        .kpi.violet{--accent:var(--c-violet)}
        .kpi.amber{--accent:var(--c-amber)}
        .kpi-label{font-size:0.72rem;font-weight:700;color:var(--c-sub);text-transform:uppercase;letter-spacing:0.07em}
        .kpi-val{font-size:2rem;font-weight:800;letter-spacing:-0.04em;line-height:1;margin:0.5rem 0 0.3rem;color:#fff}
        .kpi-sub{font-size:0.75rem;color:var(--c-sub)}
        .kpi-icon{
            position:absolute;right:1.25rem;top:1.25rem;
            width:34px;height:34px;border-radius:10px;
            background:rgba(255,255,255,0.05);
            display:flex;align-items:center;justify-content:center;color:var(--c-sub);
        }

        /* ── Two-column layout ── */
        .two-col{display:grid;grid-template-columns:1fr 400px;gap:1.25rem}

        /* ── Card ── */
        .card{
            background:var(--c-panel);
            border:1px solid var(--c-border);
            border-radius:var(--radius-lg);
            backdrop-filter:blur(12px);
            box-shadow:var(--shadow-card);
        }
        .card-header{
            display:flex;align-items:center;justify-content:space-between;
            padding:1.25rem 1.5rem;
            border-bottom:1px solid var(--c-border);
        }
        .card-title{font-size:1rem;font-weight:700;display:flex;align-items:center;gap:0.5rem}
        .card-sub{font-size:0.78rem;color:var(--c-sub);margin-top:2px}
        .card-body{padding:1.25rem 1.5rem}
        .card-body.no-pad{padding:0}

        /* ── Tables ── */
        .tbl-wrap{overflow-x:auto}
        table{width:100%;border-collapse:collapse;font-size:0.88rem}
        th{
            text-align:left;padding:0.75rem 1.5rem;
            font-size:0.7rem;font-weight:700;color:var(--c-muted);
            text-transform:uppercase;letter-spacing:0.08em;
            border-bottom:1px solid var(--c-border);
            background:rgba(0,0,0,0.2);
        }
        td{padding:0.85rem 1.5rem;border-bottom:1px solid var(--c-border);vertical-align:middle}
        tr:last-child td{border-bottom:none}
        tr:hover td{background:rgba(255,255,255,0.02)}

        /* ── Badges ── */
        .badge{
            display:inline-flex;align-items:center;gap:0.3rem;
            padding:0.25rem 0.65rem;border-radius:999px;
            font-size:0.72rem;font-weight:700;letter-spacing:0.02em;
        }
        .badge-green{background:var(--c-emerald-bg);color:#34d399;border:1px solid rgba(16,185,129,0.3)}
        .badge-blue{background:rgba(79,131,255,0.12);color:#93c5fd;border:1px solid rgba(79,131,255,0.25)}
        .badge-amber{background:var(--c-amber-bg);color:#fbbf24;border:1px solid rgba(245,158,11,0.3)}
        .badge-red{background:var(--c-rose-bg);color:#f87171;border:1px solid rgba(244,63,94,0.3)}
        .badge-violet{background:var(--c-violet-bg);color:#c084fc;border:1px solid rgba(139,92,246,0.3)}

        /* ── Patient Cell ── */
        .p-cell{display:flex;align-items:center;gap:0.75rem}
        .avatar{
            width:34px;height:34px;border-radius:50%;
            background:linear-gradient(135deg,rgba(79,131,255,0.3),rgba(34,211,238,0.2));
            border:1px solid rgba(79,131,255,0.3);
            display:flex;align-items:center;justify-content:center;
            font-weight:700;font-size:0.8rem;color:#93c5fd;flex-shrink:0;
        }
        .p-name{font-weight:600}
        .p-phone{font-size:0.75rem;color:var(--c-sub);font-family:'JetBrains Mono',monospace}

        /* ── Search & Controls ── */
        .controls{display:flex;align-items:center;gap:1rem;margin-bottom:1.25rem}
        .search-wrap{position:relative;flex:1}
        .search-icon{position:absolute;left:0.85rem;top:50%;transform:translateY(-50%);color:var(--c-sub);width:16px;height:16px}
        .form-input,.search-wrap input{
            width:100%;padding:0.6rem 0.85rem 0.6rem 2.4rem;
            background:rgba(0,0,0,0.3);border:1px solid var(--c-border);
            border-radius:var(--radius-sm);color:var(--c-text);
            font-size:0.88rem;font-family:'Plus Jakarta Sans',sans-serif;
            transition:all 0.2s;outline:none;
        }
        .form-input:focus,.search-wrap input:focus{border-color:var(--c-primary);box-shadow:0 0 0 3px var(--c-primary-glow)}
        .form-input{padding-left:0.85rem}

        /* ── Filter Chips ── */
        .chip-group{display:flex;gap:0.4rem}
        .chip{
            padding:0.4rem 0.85rem;border-radius:999px;
            font-size:0.78rem;font-weight:600;cursor:pointer;
            background:rgba(255,255,255,0.04);border:1px solid var(--c-border);
            color:var(--c-sub);transition:all 0.2s;
        }
        .chip:hover{color:var(--c-text);background:rgba(255,255,255,0.08)}
        .chip.active{background:rgba(79,131,255,0.2);color:#93c5fd;border-color:rgba(79,131,255,0.4)}

        /* ── Active Calls Card ── */
        .active-call-card{
            background:rgba(16,185,129,0.04);
            border:1px solid rgba(16,185,129,0.25);
            border-radius:var(--radius-md);
            padding:1rem;margin-bottom:0.85rem;
            animation:pulse-border 3s infinite;
        }
        @keyframes pulse-border{0%,100%{border-color:rgba(16,185,129,0.25)}50%{border-color:rgba(16,185,129,0.5)}}
        .acc-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:0.6rem}
        .acc-caller{font-weight:700;display:flex;align-items:center;gap:0.5rem;font-size:0.95rem}
        .live-pill{
            background:var(--c-emerald);color:#000;
            font-size:0.62rem;font-weight:800;padding:0.1rem 0.4rem;border-radius:4px;
            letter-spacing:0.05em;display:inline-flex;align-items:center;gap:0.3rem;
        }
        .live-dot{width:6px;height:6px;border-radius:50%;background:#000;animation:blink 1s infinite}
        @keyframes blink{0%,100%{opacity:1}50%{opacity:0.2}}
        .acc-dur{font-family:'JetBrains Mono',monospace;font-size:0.8rem;color:var(--c-emerald);font-weight:600}
        .waveform{width:100%;height:48px;border-radius:6px;background:rgba(0,0,0,0.3);margin-bottom:0.6rem}
        .acc-transcript{
            max-height:160px;overflow-y:auto;
            background:rgba(0,0,0,0.4);border:1px solid var(--c-border);
            border-radius:var(--radius-sm);padding:0.75rem;
            font-size:0.82rem;line-height:1.5;
        }
        .msg-agent{color:#93c5fd;margin-bottom:0.35rem}
        .msg-user{color:#86efac;margin-bottom:0.35rem}

        /* ── Empty state ── */
        .empty{text-align:center;padding:3rem 1rem;color:var(--c-sub)}
        .empty-icon{margin-bottom:0.75rem;display:flex;justify-content:center;align-items:center;color:var(--c-muted)}
        .empty-text{font-size:0.9rem;font-weight:500}

        /* ── Call History Drawer ── */
        .drawer{
            display:none;margin-top:0.75rem;padding:0.85rem;
            background:rgba(0,0,0,0.4);border:1px solid var(--c-border);
            border-radius:var(--radius-sm);font-size:0.82rem;line-height:1.6;
        }
        .drawer.open{display:block}

        /* ── Slot Manager ── */
        .slot-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:0.75rem}
        .slot{
            padding:0.85rem;border-radius:var(--radius-md);
            text-align:center;border:1px solid var(--c-border);
            background:rgba(255,255,255,0.02);cursor:pointer;transition:all 0.2s;
        }
        .slot.free{border-color:rgba(16,185,129,0.3);background:rgba(16,185,129,0.06)}
        .slot.free:hover{border-color:var(--c-emerald);background:rgba(16,185,129,0.12);transform:scale(1.02)}
        .slot.taken{border-color:rgba(244,63,94,0.2);background:rgba(244,63,94,0.04);cursor:not-allowed;opacity:0.6}
        .slot-time{font-family:'JetBrains Mono',monospace;font-weight:700;font-size:0.95rem;margin-bottom:0.25rem}
        .slot-lbl{font-size:0.7rem;font-weight:600}
        .slot.free .slot-lbl{color:var(--c-emerald)}
        .slot.taken .slot-lbl{color:var(--c-rose)}

        /* ── AI Health Grid ── */
        .health-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:1rem}
        .health-item{
            background:rgba(255,255,255,0.02);border:1px solid var(--c-border);
            border-radius:var(--radius-md);padding:1rem;display:flex;align-items:center;gap:1rem;
        }
        .health-indicator{width:12px;height:12px;border-radius:50%;flex-shrink:0}
        .h-ok{background:var(--c-emerald);box-shadow:0 0 8px var(--c-emerald)}
        .h-warn{background:var(--c-amber);box-shadow:0 0 8px var(--c-amber)}
        .h-err{background:var(--c-rose);box-shadow:0 0 8px var(--c-rose)}
        .health-name{font-weight:600;font-size:0.9rem}
        .health-lat{font-size:0.75rem;color:var(--c-sub);font-family:'JetBrains Mono',monospace}

        /* ── Modal ── */
        .modal-bg{
            position:fixed;inset:0;background:rgba(0,0,0,0.75);
            backdrop-filter:blur(8px);display:none;align-items:center;justify-content:center;
            z-index:9999;
        }
        .modal-bg.open{display:flex}
        .modal{
            background:var(--c-surface);border:1px solid var(--c-border-h);
            border-radius:var(--radius-lg);width:440px;padding:2rem;
            box-shadow:var(--shadow-lg);
        }
        .modal-title{font-size:1.2rem;font-weight:800;margin-bottom:0.25rem;display:flex;align-items:center;gap:0.5rem}
        .modal-sub{font-size:0.8rem;color:var(--c-sub);margin-bottom:1.5rem}
        .form-row{margin-bottom:1rem}
        .form-label{display:block;font-size:0.78rem;font-weight:600;color:var(--c-sub);margin-bottom:0.35rem}
        .modal-actions{display:flex;gap:0.75rem;justify-content:flex-end;margin-top:1.5rem}

        /* ── Toast ── */
        #toast-container{position:fixed;bottom:1.5rem;right:1.5rem;z-index:99999;display:flex;flex-direction:column;gap:0.5rem}
        .toast{
            background:var(--c-surface);border:1px solid var(--c-border-h);
            padding:0.75rem 1.25rem;border-radius:var(--radius-md);
            font-size:0.85rem;font-weight:600;color:var(--c-text);
            box-shadow:var(--shadow-lg);animation:slideUp 0.25s ease-out;
            display:flex;align-items:center;gap:0.5rem;
        }
        .toast.ok{border-color:rgba(16,185,129,0.5);color:#86efac}
        .toast.err{border-color:rgba(244,63,94,0.5);color:#fca5a5}
        @keyframes slideUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}

        /* ── Web Call Widget FAB & Panel ── */
        #call-widget{position:fixed;bottom:2rem;right:2rem;z-index:8888}
        .call-fab{
            width:56px;height:56px;border-radius:50%;
            background:linear-gradient(135deg,var(--c-emerald),#059669);
            color:#fff;border:none;cursor:pointer;
            box-shadow:0 8px 25px rgba(16,185,129,0.4);
            display:flex;align-items:center;justify-content:center;
            transition:all 0.3s cubic-bezier(0.34,1.56,0.64,1);
        }
        .call-fab:hover{transform:scale(1.1);box-shadow:0 12px 30px rgba(16,185,129,0.6)}
        .call-fab.ringing{animation:fab-ring 1.5s infinite}
        @keyframes fab-ring{0%,100%{box-shadow:0 0 0 0 rgba(16,185,129,0.7)}50%{box-shadow:0 0 0 20px rgba(16,185,129,0)}}

        .call-panel{
            position:absolute;bottom:70px;right:0;width:340px;
            background:var(--c-surface);border:1px solid var(--c-border-h);
            border-radius:var(--radius-lg);box-shadow:var(--shadow-lg);
            display:none;flex-direction:column;overflow:hidden;backdrop-filter:blur(20px);
        }
        .call-panel.open{display:flex;animation:fadeSlide 0.2s ease-out}
        .cp-header{
            padding:1rem 1.25rem;background:rgba(0,0,0,0.3);
            border-bottom:1px solid var(--c-border);
            display:flex;align-items:center;justify-content:space-between;
        }
        .cp-title{font-weight:700;font-size:0.92rem;display:flex;align-items:center;gap:0.5rem}
        .cp-close{background:none;border:none;color:var(--c-sub);cursor:pointer;font-size:1rem}
        .cp-body{padding:1.25rem;text-align:center}
        .cp-status-icon{
            width:60px;height:60px;border-radius:50%;
            background:rgba(79,131,255,0.12);border:1px solid rgba(79,131,255,0.25);
            display:flex;align-items:center;justify-content:center;
            margin:0 auto 0.85rem;color:var(--c-primary);
        }
        .cp-status-text{font-weight:700;font-size:0.95rem;margin-bottom:0.2rem}
        .cp-status-sub{font-size:0.75rem;color:var(--c-sub);margin-bottom:1rem}
        .cp-canvas{width:100%;height:60px;border-radius:8px;background:rgba(0,0,0,0.3);margin-bottom:0.85rem}
        .cp-transcript{
            max-height:140px;overflow-y:auto;text-align:left;
            font-size:0.8rem;background:rgba(0,0,0,0.3);border:1px solid var(--c-border);
            border-radius:8px;padding:0.75rem;margin-bottom:0.85rem;display:none;
        }
        .cp-actions{display:flex;gap:0.5rem}
        .cp-start{
            flex:1;padding:0.65rem;border-radius:var(--radius-sm);
            background:linear-gradient(135deg,var(--c-emerald),#059669);
            color:#fff;font-weight:700;font-size:0.88rem;border:none;cursor:pointer;
            display:flex;align-items:center;justify-content:center;gap:0.4rem;
        }
        .cp-hangup{
            display:none;padding:0.65rem 1rem;border-radius:var(--radius-sm);
            background:var(--c-rose);color:#fff;font-weight:700;font-size:0.88rem;
            border:none;cursor:pointer;
        }
    </style>
</head>
<body>

<div class="shell">

    <!-- ════ SIDEBAR ════ -->
    <nav class="sidebar">
        <div class="brand">
            <div class="brand-icon"><i data-lucide="stethoscope" style="width:22px;height:22px"></i></div>
            <div>
                <div class="brand-title">Clinigo</div>
                <div class="brand-sub">Command Center</div>
            </div>
        </div>

        <div class="nav-section-label">Operations</div>
        <button class="nav-item active" data-section="overview" id="nav-overview">
            <span class="nav-icon"><i data-lucide="layout-dashboard"></i></span> Overview
        </button>
        <button class="nav-item" data-section="live" id="nav-live">
            <span class="nav-icon"><i data-lucide="radio"></i></span> Live Calls
            <span class="live-badge" id="live-count-badge">0 Live</span>
        </button>
        <button class="nav-item" data-section="appointments" id="nav-appointments">
            <span class="nav-icon"><i data-lucide="calendar"></i></span> Appointments
            <span class="nav-badge" id="pending-badge">0</span>
        </button>
        <button class="nav-item" data-section="calls" id="nav-calls">
            <span class="nav-icon"><i data-lucide="phone-call"></i></span> Call History
        </button>
        <button class="nav-item" data-section="slots" id="nav-slots">
            <span class="nav-icon"><span data-lucide="clock" style="width:18px;height:18px"></span></span> Slot Manager
        </button>

        <div class="nav-section-label" style="margin-top:0.75rem">System</div>
        <button class="nav-item" data-section="health" id="nav-health">
            <span class="nav-icon"><i data-lucide="activity"></i></span> AI Health
        </button>

        <div class="sidebar-footer">
            <div class="system-status">
                <div class="status-dot" id="ws-status-dot"></div>
                <span id="ws-status-text">Connecting…</span>
            </div>
        </div>
    </nav>

    <!-- ════ MAIN ════ -->
    <div class="main">

        <header class="topbar">
            <div class="topbar-left">
                <div>
                    <div class="page-title" id="topbar-title">Command Center Overview</div>
                    <div class="page-sub" id="topbar-sub">Real-time clinic operations dashboard</div>
                </div>
            </div>
            <div class="topbar-right">
                <span class="clock" id="clock">--:--:--</span>
                <button class="btn btn-ghost btn-sm" onclick="refreshAll()"><i data-lucide="rotate-cw" style="width:14px;height:14px"></i> Refresh</button>
                <button class="btn btn-primary btn-sm" onclick="openBookModal()"><i data-lucide="plus" style="width:14px;height:14px"></i> New Appointment</button>
            </div>
        </header>

        <div class="page-content" id="page-content">

            <!-- ══ OVERVIEW ══ -->
            <div class="section active" id="section-overview">
                <div class="kpi-row">
                    <div class="kpi emerald">
                        <div class="kpi-icon"><i data-lucide="calendar" style="width:18px;height:18px"></i></div>
                        <div class="kpi-label">Today's Appointments</div>
                        <div class="kpi-val" id="k-today">—</div>
                        <div class="kpi-sub" id="k-today-sub">Loading…</div>
                    </div>
                    <div class="kpi cyan">
                        <div class="kpi-icon"><i data-lucide="phone-call" style="width:18px;height:18px"></i></div>
                        <div class="kpi-label">Calls Today</div>
                        <div class="kpi-val" id="k-calls">—</div>
                        <div class="kpi-sub">Total voice sessions</div>
                    </div>
                    <div class="kpi violet">
                        <div class="kpi-icon"><i data-lucide="clock" style="width:18px;height:18px"></i></div>
                        <div class="kpi-label">Avg Call Duration</div>
                        <div class="kpi-val" id="k-dur">—</div>
                        <div class="kpi-sub">Seconds per session</div>
                    </div>
                    <div class="kpi amber">
                        <div class="kpi-icon"><i data-lucide="check-circle-2" style="width:18px;height:18px"></i></div>
                        <div class="kpi-label">Bookings This Week</div>
                        <div class="kpi-val" id="k-week">—</div>
                        <div class="kpi-sub">New appointments booked</div>
                    </div>
                    <div class="kpi">
                        <div class="kpi-icon"><i data-lucide="radio" style="width:18px;height:18px"></i></div>
                        <div class="kpi-label">Active Calls Now</div>
                        <div class="kpi-val" id="k-live">0</div>
                        <div class="kpi-sub" id="k-live-sub">No active calls</div>
                    </div>
                </div>

                <div class="two-col">
                    <div class="card">
                        <div class="card-header">
                            <div>
                                <div class="card-title"><i data-lucide="calendar" style="width:18px;height:18px;color:var(--c-primary)"></i> Today's Schedule</div>
                                <div class="card-sub">All booked appointments for today</div>
                            </div>
                            <button class="btn btn-ghost btn-sm" onclick="navigate('appointments')">View All</button>
                        </div>
                        <div class="card-body no-pad">
                            <div class="tbl-wrap">
                                <table>
                                    <thead><tr><th>Patient</th><th>Time</th><th>Status</th></tr></thead>
                                    <tbody id="today-tbody">
                                        <tr><td colspan="3"><div class="empty" style="padding:1.5rem"><div class="empty-text">Loading…</div></div></td></tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>

                    <div class="card">
                        <div class="card-header">
                            <div>
                                <div class="card-title"><i data-lucide="radio" style="width:18px;height:18px;color:var(--c-emerald)"></i> Live Active Calls</div>
                                <div class="card-sub">Real-time WebSocket stream</div>
                            </div>
                            <span class="badge badge-green" id="live-badge">0 Online</span>
                        </div>
                        <div class="card-body">
                            <div id="live-calls-ov">
                                <div class="empty">
                                    <div class="empty-icon"><i data-lucide="radio" style="width:32px;height:32px"></i></div>
                                    <div class="empty-text">No active calls right now</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="card" style="margin-top:1.25rem">
                    <div class="card-header">
                        <div>
                            <div class="card-title"><i data-lucide="history" style="width:18px;height:18px;color:var(--c-violet)"></i> Recent Call Sessions</div>
                            <div class="card-sub">Last 5 recorded conversations</div>
                        </div>
                        <button class="btn btn-ghost btn-sm" onclick="navigate('calls')">Full History</button>
                    </div>
                    <div class="card-body no-pad">
                        <div class="tbl-wrap">
                            <table>
                                <thead><tr><th>Session ID</th><th>Caller</th><th>Duration</th><th>Outcome</th><th>Summary</th></tr></thead>
                                <tbody id="recent-calls-tbody">
                                    <tr><td colspan="5"><div class="empty" style="padding:1.5rem"><div class="empty-text">Loading…</div></div></td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>

            <!-- ══ LIVE CALLS ══ -->
            <div class="section" id="section-live">
                <div class="card">
                    <div class="card-header">
                        <div>
                            <div class="card-title"><i data-lucide="radio" style="width:20px;height:20px;color:var(--c-emerald)"></i> Live Active Calls</div>
                            <div class="card-sub">WebSocket real-time transcript stream — updates instantly</div>
                        </div>
                        <span class="badge badge-green" id="live-badge-2">0 Online</span>
                    </div>
                    <div class="card-body">
                        <div id="live-calls-main">
                            <div class="empty">
                                <div class="empty-icon"><i data-lucide="radio" style="width:36px;height:36px"></i></div>
                                <div class="empty-text">No active calls. Use the green phone button ↘ to start a web call.</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- ══ APPOINTMENTS ══ -->
            <div class="section" id="section-appointments">
                <div class="card">
                    <div class="card-header">
                        <div>
                            <div class="card-title"><i data-lucide="calendar" style="width:20px;height:20px;color:var(--c-primary)"></i> Patient Appointments</div>
                            <div class="card-sub">Full appointment registry with search and filters</div>
                        </div>
                        <button class="btn btn-primary btn-sm" onclick="openBookModal()"><i data-lucide="plus" style="width:14px;height:14px"></i> Book Appointment</button>
                    </div>
                    <div class="card-body">
                        <div class="controls">
                            <div class="search-wrap">
                                <i data-lucide="search" class="search-icon"></i>
                                <input type="text" id="appt-search" placeholder="Search by name or phone…" oninput="renderAppointments()">
                            </div>
                            <div class="chip-group">
                                <button class="chip active" data-filter="ALL" onclick="setApptFilter(this)">All</button>
                                <button class="chip" data-filter="BOOKED" onclick="setApptFilter(this)">Booked</button>
                                <button class="chip" data-filter="CANCELLED" onclick="setApptFilter(this)">Cancelled</button>
                            </div>
                            <span class="badge badge-blue" id="appt-count">0 records</span>
                        </div>
                        <div class="tbl-wrap">
                            <table>
                                <thead><tr><th>#</th><th>Patient</th><th>Date</th><th>Time</th><th>Status</th><th>Action</th></tr></thead>
                                <tbody id="appt-tbody">
                                    <tr><td colspan="6"><div class="empty" style="padding:2rem"><div class="empty-text">Loading appointments…</div></div></td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>

            <!-- ══ CALL HISTORY ══ -->
            <div class="section" id="section-calls">
                <div class="card">
                    <div class="card-header">
                        <div>
                            <div class="card-title"><i data-lucide="phone-call" style="width:20px;height:20px;color:var(--c-violet)"></i> Call History</div>
                            <div class="card-sub">All recorded voice sessions with full transcripts</div>
                        </div>
                        <span class="badge badge-violet" id="call-count-badge">Loading…</span>
                    </div>
                    <div class="card-body">
                        <div class="controls">
                            <div class="search-wrap">
                                <i data-lucide="search" class="search-icon"></i>
                                <input type="text" id="call-search" placeholder="Search by phone or outcome…" oninput="renderCalls()">
                            </div>
                        </div>
                        <div id="call-list">
                            <div class="empty"><div class="empty-icon"><i data-lucide="phone-off" style="width:36px;height:36px"></i></div><div class="empty-text">Loading calls…</div></div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- ══ SLOT MANAGER ══ -->
            <div class="section" id="section-slots">
                <div class="card">
                    <div class="card-header">
                        <div>
                            <div class="card-title"><i data-lucide="clock" style="width:20px;height:20px;color:var(--c-cyan)"></i> Appointment Slot Manager</div>
                            <div class="card-sub">View and manage daily available slots</div>
                        </div>
                        <input type="date" class="form-input" id="slot-date" style="width:auto;padding:0.4rem 0.75rem" onchange="loadSlots()">
                    </div>
                    <div class="card-body">
                        <div class="slot-grid" id="slot-grid">
                            <div class="empty"><div class="empty-text">Select a date to view slots</div></div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- ══ AI HEALTH ══ -->
            <div class="section" id="section-health">
                <div class="kpi-row" style="grid-template-columns:repeat(3,1fr)">
                    <div class="kpi emerald">
                        <div class="kpi-icon"><i data-lucide="database" style="width:18px;height:18px"></i></div>
                        <div class="kpi-label">Database</div>
                        <div class="kpi-val" id="hk-db">—</div>
                        <div class="kpi-sub">PostgreSQL</div>
                    </div>
                    <div class="kpi">
                        <div class="kpi-icon"><i data-lucide="bot" style="width:18px;height:18px"></i></div>
                        <div class="kpi-label">AI Agent</div>
                        <div class="kpi-val" id="hk-agent">—</div>
                        <div class="kpi-sub">Groq + Cartesia</div>
                    </div>
                    <div class="kpi cyan">
                        <div class="kpi-icon"><i data-lucide="network" style="width:18px;height:18px"></i></div>
                        <div class="kpi-label">LiveKit</div>
                        <div class="kpi-val" id="hk-lk">—</div>
                        <div class="kpi-sub">WebRTC Bridge</div>
                    </div>
                </div>

                <div class="card" style="margin-top:1.25rem">
                    <div class="card-header">
                        <div class="card-title"><i data-lucide="activity" style="width:18px;height:18px;color:var(--c-primary)"></i> AI Infrastructure Services</div>
                        <button class="btn btn-ghost btn-sm" onclick="checkHealth()"><i data-lucide="rotate-cw" style="width:14px;height:14px"></i> Recheck</button>
                    </div>
                    <div class="card-body">
                        <div class="health-grid">
                            <div class="health-item">
                                <div class="health-indicator h-ok" id="hi-db"></div>
                                <div><div class="health-name">PostgreSQL Database</div><div class="health-lat" id="hl-db">Checking…</div></div>
                            </div>
                            <div class="health-item">
                                <div class="health-indicator h-warn" id="hi-groq"></div>
                                <div><div class="health-name">Groq LLM (LLaMA-3)</div><div class="health-lat" id="hl-groq">Not checked</div></div>
                            </div>
                            <div class="health-item">
                                <div class="health-indicator h-warn" id="hi-cart"></div>
                                <div><div class="health-name">Cartesia Voice (Ink-2 STT + Sonic TTS)</div><div class="health-lat" id="hl-cart">Not checked</div></div>
                            </div>
                            <div class="health-item">
                                <div class="health-indicator h-warn" id="hi-lk"></div>
                                <div><div class="health-name">LiveKit WebRTC</div><div class="health-lat" id="hl-lk">Not checked</div></div>
                            </div>
                            <div class="health-item">
                                <div class="health-indicator h-ok" id="hi-ws"></div>
                                <div><div class="health-name">Dashboard WebSocket</div><div class="health-lat" id="hl-ws">Connected</div></div>
                            </div>
                            <div class="health-item">
                                <div class="health-indicator h-ok" id="hi-api"></div>
                                <div><div class="health-name">Dashboard API Server</div><div class="health-lat" id="hl-api">Running on :5000</div></div>
                            </div>
                        </div>

                        <div style="margin-top:1.5rem;background:rgba(0,0,0,0.3);border:1px solid var(--c-border);border-radius:var(--radius-md);padding:1.25rem">
                            <div style="font-weight:700;margin-bottom:1rem;display:flex;align-items:center;gap:0.5rem"><i data-lucide="sliders" style="width:16px;height:16px;color:var(--c-primary)"></i> Agent Configuration</div>
                            <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:0.5rem 2rem">
                                <div style="display:flex;justify-content:space-between;padding:0.5rem 0;border-bottom:1px solid rgba(255,255,255,0.05)">
                                    <span style="color:var(--c-sub);font-size:0.85rem">LLM Model</span>
                                    <span style="font-weight:600;font-size:0.85rem">llama-3.1-70b-versatile</span>
                                </div>
                                <div style="display:flex;justify-content:space-between;padding:0.5rem 0;border-bottom:1px solid rgba(255,255,255,0.05)">
                                    <span style="color:var(--c-sub);font-size:0.85rem">TTS Engine</span>
                                    <span style="font-weight:600;font-size:0.85rem">Cartesia Sonic-2</span>
                                </div>
                                <div style="display:flex;justify-content:space-between;padding:0.5rem 0;border-bottom:1px solid rgba(255,255,255,0.05)">
                                    <span style="color:var(--c-sub);font-size:0.85rem">STT Engine</span>
                                    <span style="font-weight:600;font-size:0.85rem">Cartesia Ink-2</span>
                                </div>
                                <div style="display:flex;justify-content:space-between;padding:0.5rem 0;border-bottom:1px solid rgba(255,255,255,0.05)">
                                    <span style="color:var(--c-sub);font-size:0.85rem">Voice Transport</span>
                                    <span style="font-weight:600;font-size:0.85rem">LiveKit WebRTC</span>
                                </div>
                                <div style="display:flex;justify-content:space-between;padding:0.5rem 0">
                                    <span style="color:var(--c-sub);font-size:0.85rem">Clinic</span>
                                    <span style="font-weight:600;font-size:0.85rem">Healthcare Clinic</span>
                                </div>
                                <div style="display:flex;justify-content:space-between;padding:0.5rem 0;align-items:center">
                                    <span style="color:var(--c-sub);font-size:0.85rem">Mode</span>
                                    <span class="badge badge-green">Production</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

        </div>
    </div>
</div>

<!-- ════ WEB CALL WIDGET ════ -->
<div id="call-widget">
    <div class="call-panel" id="call-panel">
        <div class="cp-header">
            <div class="cp-title"><i data-lucide="stethoscope" style="width:18px;height:18px;color:var(--c-primary)"></i> Web Voice Call</div>
            <button class="cp-close" onclick="closeCallPanel()">✕</button>
        </div>
        <div class="cp-body">
            <div class="cp-status">
                <div class="cp-status-icon" id="cp-icon"><i data-lucide="mic" style="width:28px;height:28px"></i></div>
                <div class="cp-status-text" id="cp-text">Start a Voice Call</div>
                <div class="cp-status-sub" id="cp-sub">Speak directly with the AI receptionist</div>
            </div>
            <canvas class="cp-canvas" id="cp-canvas"></canvas>
            <div class="cp-transcript" id="cp-transcript"></div>
            <div class="cp-actions">
                <button class="cp-start" id="cp-btn" onclick="toggleCall()"><i data-lucide="phone-call" style="width:16px;height:16px"></i> Start Call</button>
                <button class="cp-hangup" id="cp-hangup-btn" onclick="hangupCall()"><i data-lucide="phone-off" style="width:16px;height:16px"></i> End</button>
            </div>
        </div>
    </div>
    <button class="call-fab" id="call-fab" onclick="toggleCallPanel()" title="Open Voice Call"><i data-lucide="phone" style="width:24px;height:24px"></i></button>
</div>

<!-- ════ BOOK APPOINTMENT MODAL ════ -->
<div class="modal-bg" id="book-modal">
    <div class="modal">
        <div class="modal-title"><i data-lucide="calendar" style="width:20px;height:20px;color:var(--c-primary)"></i> New Appointment</div>
        <div class="modal-sub">Book a new patient appointment manually</div>
        <div class="form-row">
            <label class="form-label">Patient Full Name</label>
            <input class="form-input" id="m-name" type="text" placeholder="e.g. Arjun Sharma">
        </div>
        <div class="form-row">
            <label class="form-label">Phone Number</label>
            <input class="form-input" id="m-phone" type="tel" placeholder="10-digit mobile number">
        </div>
        <div class="form-row">
            <label class="form-label">Date</label>
            <input class="form-input" id="m-date" type="date" onchange="loadModalSlots()">
        </div>
        <div class="form-row">
            <label class="form-label">Time Slot</label>
            <select class="form-input" id="m-time"><option value="">— select a date first —</option></select>
        </div>
        <div class="modal-actions">
            <button class="btn btn-ghost" onclick="closeBookModal()">Cancel</button>
            <button class="btn btn-primary" onclick="submitBook()">Book Appointment</button>
        </div>
    </div>
</div>

<!-- ════ TOAST CONTAINER ════ -->
<div id="toast-container"></div>

<script>
// ═══════════════════════════════════
//  State & Icon Refresh Helper
// ═══════════════════════════════════
let allAppointments = [];
let allCalls = [];
let activeCalls = {};
let apptFilter = 'ALL';
let ws = null;
let wsRetry = 0;
let callActive = false;
let lkRoom = null;
let cpAnim = null;

function refreshIcons(){
    if(window.lucide) {
        try { lucide.createIcons(); } catch(e){}
    }
}

// ═══════════════════════════════════
//  Clock
// ═══════════════════════════════════
function tick(){ document.getElementById('clock').textContent = new Date().toLocaleTimeString('en-IN',{hour12:false}) }
setInterval(tick, 1000); tick();

// ═══════════════════════════════════
//  Navigation
// ═══════════════════════════════════
const NAV_META = {
    overview:     ['Command Center Overview','Real-time clinic operations dashboard'],
    live:         ['Live Active Calls','WebSocket real-time transcript stream'],
    appointments: ['Patient Appointments','Full appointment registry'],
    calls:        ['Call History','All recorded voice sessions'],
    slots:        ['Slot Manager','View and manage daily appointment slots'],
    health:       ['AI Infrastructure Health','Service diagnostics and system status'],
};
function navigate(section){
    document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
    document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
    document.getElementById('nav-'+section)?.classList.add('active');
    document.getElementById('section-'+section)?.classList.add('active');
    const [t,s] = NAV_META[section]||['Dashboard',''];
    document.getElementById('topbar-title').textContent = t;
    document.getElementById('topbar-sub').textContent = s;
    if(section==='appointments') loadAppointments();
    if(section==='calls') loadCalls();
    if(section==='slots') initSlotDate();
    if(section==='health') checkHealth();
    setTimeout(refreshIcons, 50);
}
document.querySelectorAll('.nav-item').forEach(btn=>{
    btn.addEventListener('click',()=>navigate(btn.dataset.section));
});

// ═══════════════════════════════════
//  Toast
// ═══════════════════════════════════
function toast(msg,type='info'){
    const el=document.createElement('div');
    el.className='toast '+type; el.textContent=msg;
    document.getElementById('toast-container').appendChild(el);
    setTimeout(()=>el.remove(),3800);
}

// ═══════════════════════════════════
//  WebSocket
// ═══════════════════════════════════
function connectWS(){
    const proto=location.protocol==='https:'?'wss':'ws';
    ws=new WebSocket(`${proto}://${location.host}/ws/dashboard`);
    ws.onopen=()=>{
        wsRetry=0;
        const dot=document.getElementById('ws-status-dot');
        dot.className='status-dot connected';
        document.getElementById('ws-status-text').textContent='Live — Connected';
        document.getElementById('hi-ws').className='health-indicator h-ok';
        document.getElementById('hl-ws').textContent='Connected';
    };
    ws.onclose=()=>{
        const dot=document.getElementById('ws-status-dot');
        dot.className='status-dot';
        document.getElementById('ws-status-text').textContent='Reconnecting…';
        document.getElementById('hi-ws').className='health-indicator h-warn';
        setTimeout(connectWS,Math.min(5000,800*(++wsRetry)));
    };
    ws.onerror=()=>ws.close();
    ws.onmessage=e=>handleWS(JSON.parse(e.data));
}

function handleWS(msg){
    if(msg.type==='call_started'){
        activeCalls[msg.session_id]={caller:msg.caller||'Web Caller',started_at:msg.started_at,lines:[]};
        toast('New call from '+(msg.caller||'Web Caller'),'info');
    } else if(msg.type==='transcript'){
        if(activeCalls[msg.session_id]) activeCalls[msg.session_id].lines.push({role:msg.role,text:msg.text});
        appendWidgetTranscript(msg.role,msg.text);
    } else if(msg.type==='call_ended'){
        delete activeCalls[msg.session_id];
        setTimeout(()=>{if(document.getElementById('section-calls').classList.contains('active'))loadCalls()},1200);
        setTimeout(loadOverview,1500);
    }
    const cnt=Object.keys(activeCalls).length;
    document.getElementById('k-live').textContent=cnt;
    document.getElementById('k-live-sub').textContent=cnt===0?'No active calls':cnt+' active call'+(cnt>1?'s':'');
    document.getElementById('live-badge').textContent=cnt+' Online';
    document.getElementById('live-badge-2').textContent=cnt+' Online';
    document.getElementById('live-count-badge').textContent=cnt+' Live';
    renderActiveCalls();
}

// ═══════════════════════════════════
//  Active Call Rendering
// ═══════════════════════════════════
function renderActiveCalls(){
    ['live-calls-ov','live-calls-main'].forEach(id=>{
        const el=document.getElementById(id);
        if(!el) return;
        const keys=Object.keys(activeCalls);
        if(!keys.length){
            el.innerHTML='<div class="empty"><div class="empty-icon"><i data-lucide="radio" style="width:36px;height:36px"></i></div><div class="empty-text">No active calls right now</div></div>';
            refreshIcons();
            return;
        }
        el.innerHTML=keys.map(sid=>{
            const c=activeCalls[sid];
            const lines=(c.lines||[]).slice(-10).map(l=>`<div class="msg-${l.role}"><strong>${l.role==='agent'?'AI':'Patient'}:</strong> ${esc(l.text)}</div>`).join('');
            return `<div class="active-call-card">
                <div class="acc-top">
                    <div class="acc-caller"><span class="live-pill"><span class="live-dot"></span>LIVE</span>${esc(c.caller)}</div>
                    <div class="acc-dur" id="dur-${sid}">00:00</div>
                </div>
                <canvas class="waveform" id="wv-${sid}"></canvas>
                <div class="acc-transcript" id="tr-${sid}">${lines||'<span style="color:var(--c-muted)">Waiting for transcript…</span>'}</div>
            </div>`;
        }).join('');
        keys.forEach(sid=>{
            animateWave(sid);
            const t=document.getElementById('tr-'+sid);
            if(t) t.scrollTop=t.scrollHeight;
        });
    });
    refreshIcons();
}

function animateWave(sid){
    const c=document.getElementById('wv-'+sid);
    if(!c) return;
    c.width=c.offsetWidth||280; c.height=48;
    const ctx=c.getContext('2d');
    let t=0;
    (function draw(){
        if(!document.getElementById('wv-'+sid)) return;
        ctx.clearRect(0,0,c.width,c.height);
        ctx.beginPath(); ctx.strokeStyle='rgba(79,131,255,0.7)'; ctx.lineWidth=2;
        for(let x=0;x<c.width;x++){
            const y=c.height/2+Math.sin(x*0.05+t)*10+Math.sin(x*0.03+t*1.5)*6;
            x===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
        }
        ctx.stroke(); t+=0.1;
        requestAnimationFrame(draw);
    })();
}

// ═══════════════════════════════════
//  API helpers
// ═══════════════════════════════════
async function api(url,opts={}){
    const r=await fetch(url,opts);
    if(!r.ok) throw new Error(await r.text());
    return r.json();
}

// ═══════════════════════════════════
//  Overview
// ═══════════════════════════════════
async function loadOverview(){
    const [appts,calls]=await Promise.all([api('/api/appointments'),api('/api/calls')]);
    allAppointments=appts; allCalls=calls;
    const today=new Date().toISOString().split('T')[0];
    const todayA=appts.filter(a=>a.date===today&&a.status==='BOOKED');
    const callsToday=calls.filter(c=>(c.started_at||'').startsWith(today));
    const weekAgo=new Date(Date.now()-7*864e5).toISOString().split('T')[0];
    const weekB=appts.filter(a=>a.date>=weekAgo&&a.date<=today&&a.status==='BOOKED');
    document.getElementById('k-today').textContent=todayA.length;
    document.getElementById('k-today-sub').textContent=todayA.length+' patient'+(todayA.length!==1?'s':'')+' scheduled';
    document.getElementById('k-calls').textContent=callsToday.length;
    document.getElementById('k-week').textContent=weekB.length;
    const avg=callsToday.length?Math.round(callsToday.reduce((s,c)=>s+(c.duration_seconds||0),0)/callsToday.length):0;
    document.getElementById('k-dur').textContent=avg+'s';
    document.getElementById('pending-badge').textContent=todayA.length;

    // Today table
    const tb=document.getElementById('today-tbody');
    if(!todayA.length){
        tb.innerHTML='<tr><td colspan="3"><div class="empty" style="padding:1.5rem"><div class="empty-text">No appointments today</div></div></td></tr>';
    } else {
        tb.innerHTML=todayA.map(a=>`<tr><td>${pCell(a.name)}</td><td><span class="badge badge-blue"><i data-lucide="clock" style="width:12px;height:12px;vertical-align:middle;margin-right:3px"></i> ${a.time}</span></td><td><span class="badge badge-green">✓ Booked</span></td></tr>`).join('');
    }
    // Recent calls
    const rc=document.getElementById('recent-calls-tbody');
    const r5=calls.slice(0,5);
    if(!r5.length){
        rc.innerHTML='<tr><td colspan="5"><div class="empty" style="padding:1.5rem"><div class="empty-text">No calls yet</div></div></td></tr>';
    } else {
        rc.innerHTML=r5.map(c=>`<tr>
            <td style="font-family:'JetBrains Mono',monospace;font-size:0.78rem;color:var(--c-muted)">${(c.session_id||'').slice(0,16)}…</td>
            <td>${c.caller_phone||'<span style="color:var(--c-muted)">Web Session</span>'}</td>
            <td>${c.duration_seconds!=null?c.duration_seconds+'s':'—'}</td>
            <td>${outBadge(c.outcome)}</td>
            <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--c-sub);font-size:0.82rem">${esc(c.summary||'—')}</td>
        </tr>`).join('');
    }
    refreshIcons();
}

// ═══════════════════════════════════
//  Appointments
// ═══════════════════════════════════
async function loadAppointments(){
    allAppointments=await api('/api/appointments');
    renderAppointments();
}
function setApptFilter(btn){
    document.querySelectorAll('.chip[data-filter]').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active'); apptFilter=btn.dataset.filter;
    renderAppointments();
}
function renderAppointments(){
    const q=(document.getElementById('appt-search')?.value||'').toLowerCase();
    let d=allAppointments;
    if(apptFilter!=='ALL') d=d.filter(a=>a.status===apptFilter);
    if(q) d=d.filter(a=>a.name.toLowerCase().includes(q)||a.phone.includes(q));
    document.getElementById('appt-count').textContent=d.length+' records';
    const tb=document.getElementById('appt-tbody');
    if(!d.length){
        tb.innerHTML='<tr><td colspan="6"><div class="empty" style="padding:2rem"><div class="empty-icon"><i data-lucide="search" style="width:36px;height:36px"></i></div><div class="empty-text">No appointments match your filter</div></div></td></tr>';
        refreshIcons();
        return;
    }
    tb.innerHTML=d.map(a=>`<tr>
        <td style="color:var(--c-muted);font-size:0.8rem">#${a.id}</td>
        <td>${pCell(a.name,a.phone)}</td>
        <td>${a.date}</td>
        <td><span class="badge badge-blue"><i data-lucide="clock" style="width:12px;height:12px;vertical-align:middle;margin-right:3px"></i> ${a.time}</span></td>
        <td>${sBadge(a.status)}</td>
        <td>${a.status==='BOOKED'?`<button class="btn btn-rose btn-sm" onclick="cancelAppt(${a.id})">Cancel</button>`:'—'}</td>
    </tr>`).join('');
    refreshIcons();
}
async function cancelAppt(id){
    if(!confirm('Cancel this appointment?')) return;
    const r=await api(`/api/cancel/${id}`,{method:'POST'});
    toast(r.message,r.success?'ok':'err');
    loadAppointments(); loadOverview();
}

// ═══════════════════════════════════
//  Call History
// ═══════════════════════════════════
async function loadCalls(){
    allCalls=await api('/api/calls');
    document.getElementById('call-count-badge').textContent=allCalls.length+' Sessions';
    renderCalls();
}
function renderCalls(){
    const q=(document.getElementById('call-search')?.value||'').toLowerCase();
    let d=allCalls;
    if(q) d=d.filter(c=>(c.caller_phone||'').includes(q)||(c.outcome||'').toLowerCase().includes(q)||(c.summary||'').toLowerCase().includes(q));
    const el=document.getElementById('call-list');
    if(!d.length){
        el.innerHTML='<div class="empty"><div class="empty-icon"><i data-lucide="phone-off" style="width:36px;height:36px"></i></div><div class="empty-text">No call sessions found</div></div>';
        refreshIcons();
        return;
    }
    el.innerHTML=d.map((c,i)=>`
    <div style="background:rgba(255,255,255,0.02);border:1px solid var(--c-border);border-radius:12px;padding:1rem 1.25rem;margin-bottom:0.75rem;transition:all 0.2s" onmouseenter="this.style.borderColor='var(--c-border-h)'" onmouseleave="this.style.borderColor='var(--c-border)'">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.5rem">
            <div style="display:flex;align-items:center;gap:0.75rem">
                <div style="width:40px;height:40px;border-radius:10px;background:rgba(79,131,255,0.15);display:flex;align-items:center;justify-content:center;color:var(--c-primary)"><i data-lucide="phone-call" style="width:20px;height:20px"></i></div>
                <div>
                    <div style="font-weight:700">${c.caller_phone||'<span style="color:var(--c-muted)">Web Session</span>'}</div>
                    <div style="font-size:0.75rem;color:var(--c-sub)">${new Date(c.started_at).toLocaleString('en-IN')}</div>
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:0.5rem">
                <span class="badge badge-violet">${c.duration_seconds!=null?c.duration_seconds+'s':'—'}</span>
                ${outBadge(c.outcome)}
                <button class="btn btn-ghost btn-sm" onclick="toggleDrawer('dr-${i}')">Transcript ▾</button>
            </div>
        </div>
        ${c.summary?`<div style="font-size:0.82rem;color:var(--c-sub);margin-top:0.4rem"><strong>Summary:</strong> ${esc(c.summary)}</div>`:''}
        <div class="drawer" id="dr-${i}">${fmtTranscript(c.transcript)}</div>
    </div>`).join('');
    refreshIcons();
}
function toggleDrawer(id){ document.getElementById(id)?.classList.toggle('open') }
function fmtTranscript(t){
    if(!t) return '<em style="color:var(--c-muted)">No transcript available</em>';
    try{
        const p=JSON.parse(t);
        if(Array.isArray(p)) return p.map(m=>`<div style="margin-bottom:0.5rem"><strong style="color:${m.role==='agent'?'#93c5fd':'#86efac'}">${m.role==='agent'?'AI':'Patient'}:</strong> ${esc(m.content||m.text||'')}</div>`).join('');
    }catch{}
    return esc(t);
}

// ═══════════════════════════════════
//  Slot Manager
// ═══════════════════════════════════
function initSlotDate(){
    const el=document.getElementById('slot-date');
    if(!el.value) el.value=new Date().toISOString().split('T')[0];
    loadSlots();
}
async function loadSlots(){
    const d=document.getElementById('slot-date').value;
    if(!d) return;
    const r=await api(`/api/slots?date=${d}`);
    const grid=document.getElementById('slot-grid');
    const all=genSlots(d);
    if(!all.length){ grid.innerHTML='<div class="empty"><div class="empty-text">Clinic is closed on this day</div></div>'; return }
    grid.innerHTML=all.map(s=>{
        const free=r.available_slots.includes(s);
        return `<div class="slot ${free?'free':'taken'}" onclick="${free?`prefillBook('${d}','${s}')`:''}" title="${free?'Click to book':'Already booked'}">
            <div class="slot-time">${s}</div>
            <div class="slot-lbl">${free?'Available':'Booked'}</div>
        </div>`;
    }).join('');
}
function genSlots(dateStr){
    const d=new Date(dateStr); if(isNaN(d)) return [];
    const dow=((d.getDay()+6)%7); // Mon=0
    if(dow===6) return []; // Sunday
    const slots=[]; let h=9,m=0;
    while(h<17){ const t=`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`;
        if(!(h===12)) slots.push(t);
        m+=30; if(m>=60){h++;m=0;}
    }
    return slots;
}

// ═══════════════════════════════════
//  Booking Modal
// ═══════════════════════════════════
function openBookModal(){
    document.getElementById('book-modal').classList.add('open');
    const d=document.getElementById('m-date');
    if(!d.value) d.value=new Date().toISOString().split('T')[0];
    loadModalSlots();
    refreshIcons();
}
function closeBookModal(){ document.getElementById('book-modal').classList.remove('open') }
async function loadModalSlots(){
    const d=document.getElementById('m-date').value;
    const sel=document.getElementById('m-time');
    if(!d){ sel.innerHTML='<option>— select a date first —</option>'; return }
    const r=await api(`/api/slots?date=${d}`);
    sel.innerHTML=r.available_slots.map(s=>`<option value="${s}">${s}</option>`).join('')||'<option value="">No available slots</option>';
}
function prefillBook(date,time){
    openBookModal();
    document.getElementById('m-date').value=date;
    loadModalSlots().then(()=>{document.getElementById('m-time').value=time});
}
async function submitBook(){
    const name=document.getElementById('m-name').value.trim();
    const phone=document.getElementById('m-phone').value.trim();
    const date=document.getElementById('m-date').value;
    const time=document.getElementById('m-time').value;
    if(!name||!phone||!date||!time){ toast('Please fill all fields','err'); return }
    const r=await api('/api/appointments/book',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({patient_name:name,patient_phone:phone,appointment_date:date,appointment_time:time})});
    toast(r.message,r.success?'ok':'err');
    if(r.success){ closeBookModal(); loadOverview(); loadAppointments(); }
}

// ═══════════════════════════════════
//  Health Check
// ═══════════════════════════════════
async function checkHealth(){
    const t0=Date.now();
    try{
        await api('/api/appointments');
        const lat=Date.now()-t0;
        setH('db','ok',lat+'ms'); document.getElementById('hk-db').textContent='OK';
    }catch{setH('db','err','Unreachable'); document.getElementById('hk-db').textContent='ERR'}
    setH('groq','ok','llama-3.1-70b-versatile'); setH('cart','ok','Cartesia Sonic-2');
    document.getElementById('hk-agent').textContent='OK';
    const lk='LIVEKIT_URL_PLACEHOLDER';
    if(lk&&lk!=='None'){ setH('lk','ok',lk.replace(/wss?:\/\//,'').split('.')[0]+'…'); document.getElementById('hk-lk').textContent='OK'; }
    else { setH('lk','warn','Not configured'); document.getElementById('hk-lk').textContent='—'; }
    refreshIcons();
}
function setH(k,s,l){
    const cls={'ok':'h-ok','warn':'h-warn','err':'h-err'}[s];
    document.getElementById('hi-'+k).className='health-indicator '+cls;
    document.getElementById('hl-'+k).textContent=l;
}

// ═══════════════════════════════════
//  Web Call Widget
// ═══════════════════════════════════
function toggleCallPanel(){ document.getElementById('call-panel').classList.toggle('open'); refreshIcons(); }
function closeCallPanel(){ document.getElementById('call-panel').classList.remove('open') }

async function toggleCall(){
    if(!callActive) await startCall(); else hangupCall();
}

async function startCall(){
    try{
        const res=await api('/api/call/token');
        if(!res.token){ toast('Could not get call token. Is the agent running?','err'); return }
        const {token,livekit_url,room}=res;
        if(!window.LivekitClient){
            await loadScript('https://cdn.jsdelivr.net/npm/livekit-client@2.5.7/dist/livekit-client.umd.min.js');
        }
        lkRoom=new window.LivekitClient.Room({adaptiveStream:true,dynacast:true});
        lkRoom.on(window.LivekitClient.RoomEvent.TrackSubscribed,(track)=>{
            if(track.kind==='audio'){ const a=track.attach(); a.autoplay=true; document.body.appendChild(a); }
        });
        lkRoom.on(window.LivekitClient.RoomEvent.DataReceived,(payload)=>{
            try{ const m=JSON.parse(new TextDecoder().decode(payload)); if(m.type==='transcript') appendWidgetTranscript(m.role,m.text) } catch{}
        });
        lkRoom.on(window.LivekitClient.RoomEvent.Disconnected,()=>{ callActive=false; setCallUI(false); toast('Call ended','info') });
        await lkRoom.connect(livekit_url,token,{autoSubscribe:true});
        const tracks=await window.LivekitClient.createLocalTracks({audio:true,video:false});
        for(const t of tracks) await lkRoom.localParticipant.publishTrack(t);
        callActive=true; setCallUI(true);
        toast('Connected! Speak with the AI receptionist','ok');
        startViz();
        await fetch('/api/call/notify_start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:room,caller:'Web Caller'})});
    }catch(e){ toast('Call failed: '+e.message,'err'); setCallUI(false) }
}

function hangupCall(){
    if(lkRoom){ lkRoom.disconnect(); lkRoom=null; }
    callActive=false; setCallUI(false); stopViz();
}

function setCallUI(on){
    const btn=document.getElementById('cp-btn');
    const hangup=document.getElementById('cp-hangup-btn');
    const fab=document.getElementById('call-fab');
    const t=document.getElementById('cp-text');
    const s=document.getElementById('cp-sub');
    const tr=document.getElementById('cp-transcript');
    if(on){
        btn.innerHTML='<i data-lucide="radio" style="width:16px;height:16px"></i> In Call…'; btn.style.cssText='background:rgba(244,63,94,0.15);color:var(--c-rose);border-color:rgba(244,63,94,0.3)';
        hangup.style.display='block'; fab.classList.add('ringing');
        t.textContent='Connected to AI Receptionist'; s.textContent='Speak naturally — the AI is listening';
        tr.style.display='block';
    } else {
        btn.innerHTML='<i data-lucide="phone-call" style="width:16px;height:16px"></i> Start Call'; btn.style.cssText='';
        hangup.style.display='none'; fab.classList.remove('ringing');
        t.textContent='Start a Voice Call'; s.textContent='Speak directly with the AI receptionist';
        tr.style.display='none';
    }
    refreshIcons();
}

function appendWidgetTranscript(role,text){
    const el=document.getElementById('cp-transcript');
    if(!el) return;
    const div=document.createElement('div'); div.style.marginBottom='0.4rem';
    const col=role==='agent'?'#93c5fd':'#86efac';
    const lbl=role==='agent'?'AI':'You';
    div.innerHTML=`<strong style="color:${col}">${lbl}:</strong> ${esc(text)}`;
    el.appendChild(div); el.scrollTop=el.scrollHeight;
}

function startViz(){
    const c=document.getElementById('cp-canvas');
    if(!c) return;
    c.width=c.offsetWidth||280; c.height=60;
    const ctx=c.getContext('2d'); let t=0;
    function draw(){
        cpAnim=requestAnimationFrame(draw);
        ctx.clearRect(0,0,c.width,c.height);
        const bars=40; const bw=c.width/bars-2;
        for(let i=0;i<bars;i++){
            const h=(Math.sin(i*0.4+t)+1)/2*(c.height*0.8)+4;
            const g=ctx.createLinearGradient(0,c.height-h,0,c.height);
            g.addColorStop(0,'rgba(79,131,255,0.9)'); g.addColorStop(1,'rgba(34,211,238,0.4)');
            ctx.fillStyle=g; ctx.fillRect(i*(bw+2),c.height-h,bw,h);
        }
        t+=0.08;
    }
    draw();
}
function stopViz(){
    if(cpAnim){cancelAnimationFrame(cpAnim);cpAnim=null;}
    const c=document.getElementById('cp-canvas');
    if(c) c.getContext('2d').clearRect(0,0,c.width,c.height);
}
function loadScript(src){
    return new Promise((res,rej)=>{const s=document.createElement('script');s.src=src;s.onload=res;s.onerror=rej;document.head.appendChild(s)});
}

// ═══════════════════════════════════
//  Helpers
// ═══════════════════════════════════
function esc(s){ return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') }
function pCell(name,phone){
    const init=(name||'?').split(' ').map(w=>w[0]).join('').slice(0,2).toUpperCase();
    return `<div class="p-cell"><div class="avatar">${init}</div><div><div class="p-name">${esc(name)}</div>${phone?`<div class="p-phone">${esc(phone)}</div>`:''}</div></div>`;
}
function outBadge(o){
    const m={BOOKED:'badge-green',CANCELLED:'badge-red',RESCHEDULED:'badge-amber',INQUIRY:'badge-blue',COMPLETED:'badge-violet'};
    return `<span class="badge ${m[o]||'badge-blue'}">${o||'COMPLETED'}</span>`;
}
function sBadge(s){ return s==='BOOKED'?'<span class="badge badge-green">✓ Booked</span>':'<span class="badge badge-red">✗ Cancelled</span>' }

async function refreshAll(){ await loadOverview(); refreshIcons(); toast('Dashboard refreshed','ok') }

// ═══════════════════════════════════
//  Init
// ═══════════════════════════════════
(async()=>{
    connectWS();
    await loadOverview();
    document.getElementById('slot-date').value=new Date().toISOString().split('T')[0];
    refreshIcons();
    setInterval(loadOverview,30000);
    // Duration timers
    setInterval(()=>{
        Object.keys(activeCalls).forEach(sid=>{
            const el=document.getElementById('dur-'+sid);
            if(el){ const s=Math.floor((Date.now()-new Date(activeCalls[sid].started_at))/1000);
                el.textContent=String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0'); }
        });
    },1000);
})();
</script>
</body>
</html>
"""

# ─────────────────────────────────────────────
#  Inject server-side values into template
# ─────────────────────────────────────────────
def render_html():
    lk_url = config.LIVEKIT_URL or 'None'
    return HTML_TEMPLATE.replace('LIVEKIT_URL_PLACEHOLDER', lk_url)


# ─────────────────────────────────────────────
#  Route Handlers
# ─────────────────────────────────────────────
async def index(request):
    return web.Response(text=render_html(), content_type='text/html')


async def ws_dashboard(request):
    """WebSocket endpoint — dashboard clients connect here to receive live events."""
    ws_conn = WebSocketResponse()
    await ws_conn.prepare(request)
    _ws_clients.add(ws_conn)

    # Send current active calls snapshot
    for sid, call in list(_active_calls.items()):
        await ws_conn.send_str(json.dumps({
            "type": "call_started",
            "session_id": sid,
            "caller": call["caller"],
            "started_at": call["started_at"],
        }))
        for line in call["transcript_lines"]:
            await ws_conn.send_str(json.dumps({
                "type": "transcript",
                "session_id": sid,
                "role": line["role"],
                "text": line["text"],
            }))

    try:
        async for msg in ws_conn:
            pass
    except Exception:
        pass
    return ws_conn


async def get_appointments_api(request):
    rows = []
    try:
        with appointments_db.get_db_cursor() as cur:
            cur.execute(
                "SELECT id, patient_name, patient_phone, appointment_date, appointment_time, status "
                "FROM appointments ORDER BY appointment_date DESC, appointment_time ASC"
            )
            for r in cur.fetchall():
                rows.append({
                    "id": r[0], "name": r[1], "phone": r[2],
                    "date": str(r[3]), "time": str(r[4])[:5], "status": r[5]
                })
    except Exception:
        pass
    return web.json_response(rows)


async def book_appointment_api(request):
    try:
        payload = await request.json()
        result = appointments_db.book_appointment(
            payload.get("patient_name"),
            payload.get("patient_phone"),
            payload.get("appointment_date"),
            payload.get("appointment_time"),
        )
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"success": False, "message": str(e)}, status=400)


async def get_slots_api(request):
    target_date = request.query.get("date", datetime.now().strftime("%Y-%m-%d"))
    slots = appointments_db.get_available_slots(target_date)
    return web.json_response({"target_date": target_date, "available_slots": slots, "count": len(slots)})


async def get_calls_api(request):
    calls = appointments_db.get_call_sessions(limit=100)
    return web.json_response(calls)


async def get_call_json_api(request):
    session_id = request.match_info['session_id']
    calls = appointments_db.get_call_sessions(limit=200)
    match = next((c for c in calls if c['session_id'] == session_id), None)
    if match:
        try:
            parsed = json.loads(match['transcript']) if match['transcript'] else match
            return web.json_response(parsed)
        except Exception:
            return web.json_response({"session_id": session_id, "raw_transcript": match['transcript']})
    return web.json_response({"error": "Not found"}, status=404)


async def cancel_api(request):
    appt_id = int(request.match_info['id'])
    with appointments_db.get_db_cursor() as cur:
        cur.execute(
            "UPDATE appointments SET status = 'CANCELLED' WHERE id = %s RETURNING id",
            (appt_id,)
        )
        row = cur.fetchone()
    if row:
        return web.json_response({"success": True, "message": f"Appointment #{appt_id} cancelled"})
    return web.json_response({"success": False, "message": "Not found"}, status=404)


async def call_token_api(request):
    """Generate a LiveKit access token for a browser web call session."""
    try:
        from livekit.api import AccessToken, VideoGrants
        room_name = f"web-call-{uuid.uuid4().hex[:12]}"
        identity = f"patient-web-{uuid.uuid4().hex[:8]}"
        token = (
            AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
            .with_identity(identity)
            .with_name("Web Caller")
            .with_grants(VideoGrants(room_join=True, room=room_name))
            .to_jwt()
        )
        return web.json_response({
            "token": token,
            "livekit_url": config.LIVEKIT_URL,
            "room": room_name,
        })
    except Exception as e:
        return web.json_response({"error": str(e), "token": None}, status=500)


async def call_notify_start_api(request):
    """Called by the browser after connecting to LiveKit."""
    try:
        payload = await request.json()
        sid = payload.get("session_id", uuid.uuid4().hex)
        caller = payload.get("caller", "Web Caller")
        await notify_call_started(sid, caller)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


# ─────────────────────────────────────────────
#  App Factory
# ─────────────────────────────────────────────
async def main():
    appointments_db.initialize_db()

    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/ws/dashboard', ws_dashboard)
    app.router.add_get('/api/appointments', get_appointments_api)
    app.router.add_post('/api/appointments/book', book_appointment_api)
    app.router.add_get('/api/slots', get_slots_api)
    app.router.add_get('/api/calls', get_calls_api)
    app.router.add_get('/api/calls/{session_id}/json', get_call_json_api)
    app.router.add_post('/api/cancel/{id}', cancel_api)
    app.router.add_get('/api/call/token', call_token_api)
    app.router.add_post('/api/call/notify_start', call_notify_start_api)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 5000)
    print("=" * 60)
    print("  Clinigo Command Center  ->  http://localhost:5000")
    print("  WebSocket Live Stream   ->  ws://localhost:5000/ws/dashboard")
    print("=" * 60)
    await site.start()
    await asyncio.Future()


if __name__ == '__main__':
    asyncio.run(main())
