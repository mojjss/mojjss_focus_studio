from __future__ import annotations

import csv
import json
import socket
import threading
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


HTML_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b1220">
<meta name="robots" content="noindex,nofollow">
<link rel="manifest" href="/manifest.json">
<title>mojjss live activity</title>
<style>
:root{
  color-scheme:dark;
  --bg:#08111f;--panel:#111c2e;--panel2:#17243a;--line:#263752;
  --text:#f7f9fc;--muted:#91a2bb;--blue:#4cc2ff;--green:#40d9a0;
  --amber:#ffc857;--red:#ff7a8a;--shadow:0 18px 60px rgba(0,0,0,.28)
}
:root[data-theme="light"]{
  color-scheme:light;
  --bg:#eef3f9;--panel:#ffffff;--panel2:#f5f8fc;--line:#dce5f0;
  --text:#102039;--muted:#607089;--blue:#087fc2;--green:#087d5d;
  --amber:#a96700;--red:#c7384d;--shadow:0 16px 44px rgba(31,55,85,.12)
}
*{box-sizing:border-box}
html,body{min-height:100%}
body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
button{font:inherit}
.shell{max-width:1180px;margin:auto;padding:22px}
header{display:flex;gap:16px;align-items:center;justify-content:space-between;margin-bottom:18px}
.brand small{display:block;color:var(--muted);font-weight:700;letter-spacing:.12em}
.brand h1{font-size:24px;margin:4px 0 0}.profile-links{display:flex;gap:12px;margin-top:5px}.profile-links a{color:var(--blue);font-size:12px;text-decoration:none;font-weight:700}
.header-right{display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:flex-end}
.clock{font-variant-numeric:tabular-nums;color:var(--muted);font-weight:650}
.icon-button{border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:12px;padding:8px 11px;cursor:pointer}
.badge{padding:7px 11px;border-radius:999px;background:rgba(64,217,160,.14);color:var(--green);font-size:12px;font-weight:800;border:1px solid rgba(64,217,160,.22)}
.badge.offline{background:rgba(255,122,138,.12);color:var(--red);border-color:rgba(255,122,138,.24)}
.grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(310px,.8fr);gap:16px}
.stack{display:grid;gap:16px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:19px;box-shadow:var(--shadow)}
.card h2{font-size:17px;margin:0 0 12px}
.label{color:var(--muted);font-size:12px;font-weight:750;letter-spacing:.08em}
.current{min-height:300px;display:flex;flex-direction:column}
.task{font-size:28px;font-weight:800;line-height:1.18;margin:13px 0 9px}
.pills{display:flex;flex-wrap:wrap;gap:7px}
.pill{padding:5px 9px;border-radius:999px;background:rgba(76,194,255,.12);color:var(--blue);font-size:12px;font-weight:700}
.timer{font-size:76px;font-weight:850;font-variant-numeric:tabular-nums;letter-spacing:.01em;margin:auto 0 7px}
.timer-sub{display:flex;justify-content:space-between;gap:12px;color:var(--muted);font-size:13px}
.progress{height:8px;border-radius:999px;background:var(--panel2);overflow:hidden;margin:14px 0 9px}
.progress>div{height:100%;background:linear-gradient(90deg,var(--blue),var(--green));width:0%;transition:width .35s ease}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.stat{background:var(--panel2);border:1px solid var(--line);border-radius:15px;padding:13px;min-height:86px}
.stat b{display:block;font-size:22px;margin-top:7px;font-variant-numeric:tabular-nums}
.stat .small{font-size:11px;color:var(--muted)}
.item{position:relative;display:grid;grid-template-columns:66px minmax(0,1fr) auto;gap:11px;padding:12px 4px;border-bottom:1px solid var(--line);align-items:start}
.item:last-child{border-bottom:0}
.item.now{background:rgba(76,194,255,.08);border-radius:11px;padding-left:9px;padding-right:9px}
.item.done{opacity:.57}
.time{color:var(--blue);font-variant-numeric:tabular-nums;font-weight:750}
.item-title{font-weight:720;overflow-wrap:anywhere}
.small{font-size:12px;color:var(--muted);margin-top:3px}
.right{font-variant-numeric:tabular-nums;white-space:nowrap}.session-date{display:block;color:var(--text);font-size:11px;font-weight:850}.session-day{display:block;color:var(--blue);font-size:11px;font-weight:800;margin-top:2px}.session-clock{display:block;color:var(--muted);font-size:11px;margin-top:3px}
.empty{color:var(--muted);padding:12px 2px}
.next{padding:13px;border-radius:14px;background:var(--panel2);border:1px solid var(--line);margin-bottom:8px}
.next b{display:block;margin-top:4px}
.graph-wrap{background:#fff;border-radius:14px;padding:12px;min-height:230px;display:flex;align-items:center;justify-content:flex-start;overflow-x:auto}
.graph{display:block;width:100%;min-width:900px;height:auto}
.sync-line{display:flex;gap:8px;align-items:flex-start;margin-top:12px;color:var(--muted);font-size:12px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--green);margin-top:4px;flex:0 0 auto}
.dot.warn{background:var(--amber)}
.footer{text-align:center;color:var(--muted);font-size:12px;margin:18px 0 4px}
@media(max-width:900px){.grid{grid-template-columns:1fr}.timer{font-size:64px}}
@media(max-width:620px){
  .shell{padding:13px}.brand h1{font-size:20px}.clock{display:none}
  .stats{grid-template-columns:1fr 1fr}.timer{font-size:52px}.task{font-size:23px}
  .item{grid-template-columns:56px minmax(0,1fr)}.item .right{grid-column:2}
  .current{min-height:270px}
}
</style>
</head>
<body>
<div class="shell">
<header>
  <div class="brand"><small>MOJJSS</small><h1>mojjss live activity</h1><div class="profile-links"><a href="https://mojsadafi.ir" target="_blank" rel="noopener">mojsadafi.ir</a><a href="https://github.com/mojjss" target="_blank" rel="noopener">github.com/mojjss</a></div></div>
  <div class="header-right">
    <span id="clock" class="clock">--:--:--</span>
    <button id="themeButton" class="icon-button" type="button" aria-label="Change theme">◐ Theme</button>
    <span id="connection" class="badge">Connecting…</span>
  </div>
</header>

<div class="grid">
  <main class="stack">
    <section class="card current">
      <div class="label">CURRENT ACTIVITY</div>
      <div id="task" class="task">No active timer</div>
      <div class="pills"><span id="mode" class="pill">Idle</span><span id="category" class="pill">—</span><span id="focusFlag" class="pill">Not counting as focus</span></div>
      <div id="timer" class="timer">00:00</div>
      <div class="progress"><div id="progressBar"></div></div>
      <div class="timer-sub"><span id="timerStatus">Waiting for the desktop app</span><span id="updated">—</span></div>
    </section>

    <section class="card">
      <h2>Today at a glance</h2>
      <div class="stats">
        <div class="stat"><span class="label">FOCUSED</span><b id="focus">0m</b><span class="small" id="focusHours">0h</span></div>
        <div class="stat"><span class="label">OTHER PRODUCTIVE</span><b id="productive">0m</b><span class="small">calls · meetings · admin</span></div>
        <div class="stat"><span class="label">TOTAL PRODUCTIVE</span><b id="totalProductive">0m</b><span class="small">focus + other work</span></div>
        <div class="stat"><span class="label">SESSIONS</span><b id="sessions">0</b><span class="small">completed today</span></div>
      </div>
    </section>

    <section class="card">
      <h2>Today's schedule</h2>
      <div id="nextEvent" class="next"><span class="label">NEXT</span><b>No upcoming item</b></div>
      <div id="schedule" class="empty">No scheduled items.</div>
    </section>
  </main>

  <aside class="stack">
    <section class="card">
      <h2>Pixela focus graph</h2>
      <div class="graph-wrap"><img id="graph" class="graph" alt="Pixela focus graph"></div>
      <div class="sync-line"><span id="syncDot" class="dot warn"></span><span id="pixelaStatus">Pixela status unavailable</span></div>
    </section>

    <section class="card">
      <h2>Recent sessions</h2>
      <div id="recent" class="empty">No sessions yet.</div>
    </section>
  </aside>
</div>
<div class="footer">Read-only dashboard · updates automatically · keep Focus Studio running</div>
</div>

<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>'"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
const fmt=s=>{s=Math.max(0,Math.floor(Number(s)||0));const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),x=s%60;return h?`${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(x).padStart(2,"0")}`:`${String(m).padStart(2,"0")}:${String(x).padStart(2,"0")}`};
const prettyMinutes=m=>{m=Math.max(0,Number(m)||0);const h=Math.floor(m/60),r=m%60;return h?(r?`${h}h ${r}m`:`${h}h`):`${m}m`};
const minuteOfDay=t=>{if(!t||!/^\d\d:\d\d/.test(t))return null;const [h,m]=t.slice(0,5).split(":").map(Number);return h*60+m};
let latest=null, fetchedAt=performance.now(), graphKey="", theme=localStorage.getItem("focusTheme")||"dark";
document.documentElement.dataset.theme=theme;
$("themeButton").onclick=()=>{theme=theme==="dark"?"light":"dark";document.documentElement.dataset.theme=theme;localStorage.setItem("focusTheme",theme)};

function renderSchedule(items){
  const now=new Date(), current=now.getHours()*60+now.getMinutes();
  if(!items?.length){$("schedule").innerHTML='<div class="empty">No scheduled items.</div>';$("nextEvent").innerHTML='<span class="label">NEXT</span><b>No upcoming item</b>';return}
  let next=null;
  $("schedule").innerHTML=items.map(x=>{
    const start=minuteOfDay(x.start), end=minuteOfDay(x.end);
    let cls="";
    if(start!==null&&end!==null&&current>=start&&current<end)cls=" now";
    else if(end!==null&&current>=end)cls=" done";
    if(!next&&start!==null&&start>=current)next=x;
    return `<div class="item${cls}"><div class="time">${esc(x.start||"")}</div><div><div class="item-title">${esc(x.title||"Scheduled activity")}</div><div class="small">${esc(x.category||"")}</div></div><div class="right small">${esc(x.end||"")}</div></div>`
  }).join("");
  $("nextEvent").innerHTML=next?`<span class="label">NEXT · ${esc(next.start||"")}</span><b>${esc(next.title||"Scheduled activity")}</b><div class="small">${esc(next.category||"")}</div>`:'<span class="label">NEXT</span><b>Schedule complete for today</b>';
}

function renderRecent(items){
  if(!items?.length){$("recent").innerHTML='<div class="empty">No completed sessions yet.</div>';return}
  $("recent").innerHTML=items.slice().reverse().slice(0,8).map(x=>`<div class="item"><div class="time"><span class="session-date">${esc(x.display_date||x.date||"")}</span><span class="session-day">${esc(x.weekday||"")}</span><span class="session-clock">${esc(x.start||"")}</span></div><div><div class="item-title">${esc(x.task||"Untitled session")}</div><div class="small">${esc(x.mode||"")} · ${esc(x.category||"")}${x.counts_toward_focus?" · focus":""}</div></div><div class="right">${esc(x.minutes||0)}m</div></div>`).join("");
}

function render(d){
  latest=d;fetchedAt=performance.now();
  const t=d.timer||{}, s=d.today||{}, p=d.pixela||{};
  $("connection").textContent="Live";$("connection").className="badge";
  $("task").textContent=t.running?(t.task||"Untitled activity"):"No active timer";
  $("mode").textContent=t.mode||"Idle";$("category").textContent=t.category||"—";
  $("focusFlag").textContent=t.counts_toward_focus?"Counts as focus":"Not counting as focus";
  $("timerStatus").textContent=t.status||"Ready";
  $("focus").textContent=prettyMinutes(s.focus_minutes||0);
  $("focusHours").textContent=((Number(s.focus_minutes||0)/60).toFixed(1).replace(".0",""))+" hours";
  $("productive").textContent=prettyMinutes(s.other_productive_minutes||0);
  $("totalProductive").textContent=prettyMinutes((s.focus_minutes||0)+(s.other_productive_minutes||0));
  $("sessions").textContent=s.total_productive_sessions||0;
  renderSchedule(d.schedule||[]);renderRecent(d.recent||[]);
  $("pixelaStatus").textContent=p.status||"Pixela status unavailable";
  $("syncDot").className=(String(p.status||"").toLowerCase().includes("connected")||String(p.status||"").toLowerCase().includes("up to date"))?"dot":"dot warn";
  const newKey=`${p.username}/${p.graph_id}`;
  if(p.username&&p.graph_id&&newKey!==graphKey){
    graphKey=newKey;
    $("graph").src=`https://pixe.la/v1/users/${encodeURIComponent(p.username)}/graphs/${encodeURIComponent(p.graph_id)}.svg`;
  }
}

function animate(){
  $("clock").textContent=new Date().toLocaleTimeString();
  if(latest){
    const t=latest.timer||{};
    let seconds=Number(t.display_seconds)||0;
    if(t.running&&!t.paused){
      const delta=(performance.now()-fetchedAt)/1000;
      const countUp=["Flow","Productive","Personal"].includes(t.mode);
      seconds=countUp?seconds+delta:seconds-delta;
    }
    $("timer").textContent=fmt(seconds);
    const duration=Math.max(1,Number(t.duration_seconds)||1);
    const elapsed=Math.max(0,Number(t.elapsed_seconds)||0)+((t.running&&!t.paused)?(performance.now()-fetchedAt)/1000:0);
    $("progressBar").style.width=["Flow","Productive","Personal"].includes(t.mode)?"100%":`${Math.min(100,Math.max(0,elapsed/duration*100))}%`;
  }
  requestAnimationFrame(animate);
}

async function refresh(){
  try{
    const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),3000);
    const response=await fetch("/api/status",{cache:"no-store",signal:controller.signal});
    clearTimeout(timeout);
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    render(await response.json());
    $("updated").textContent="Updated "+new Date().toLocaleTimeString();
  }catch(error){
    $("connection").textContent="Offline";$("connection").className="badge offline";
    $("updated").textContent="Dashboard cannot reach Focus Studio";
  }
}
refresh();setInterval(refresh,1000);requestAnimationFrame(animate);
</script>
</body>
</html>"""


MANIFEST = {
    "name": "Pixela Focus Dashboard",
    "short_name": "Focus Dashboard",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#08111f",
    "theme_color": "#0b1220",
    "description": "Read-only live dashboard for Pixela Focus Studio",
}


def local_ipv4() -> str:
    """Best-effort LAN address without making an external request."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 80))
        return str(sock.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


def _load_schedule_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                date_value = str(raw.get("date", "")).strip()
                if date_value:
                    try:
                        date_value = datetime.fromisoformat(date_value).date().isoformat()
                    except ValueError:
                        continue
                rows.append(
                    {
                        "date": date_value,
                        "start": str(raw.get("start", "")).strip(),
                        "end": str(raw.get("end", "")).strip(),
                        "title": str(raw.get("title", "")).strip() or "Scheduled activity",
                        "category": str(raw.get("category", "")).strip(),
                    }
                )
    except (OSError, csv.Error):
        return []
    return rows


def read_schedule_range(
    path: Path,
    start_date: datetime.date,
    end_date: datetime.date,
) -> list[dict[str, str]]:
    """Expand recurring blank-date rows across an inclusive date range."""
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    source_rows = _load_schedule_rows(path)
    output: list[dict[str, str]] = []
    current = start_date
    while current <= end_date:
        iso_date = current.isoformat()
        for row in source_rows:
            if row["date"] not in {"", iso_date}:
                continue
            output.append(
                {
                    **row,
                    "date": iso_date,
                    "recurring": "1" if row["date"] == "" else "0",
                }
            )
        current += timedelta(days=1)

    output.sort(key=lambda item: (item["date"], item["start"], item["title"]))
    return output


def read_schedule(path: Path, timezone_name: str) -> list[dict[str, str]]:
    try:
        zone = ZoneInfo(timezone_name)
    except Exception:
        zone = datetime.now().astimezone().tzinfo
    today = datetime.now(zone).date()
    return read_schedule_range(path, today, today)


class MonitorServer:
    def __init__(
        self,
        payload_provider: Callable[[], dict[str, Any]],
        graph_png_provider: Callable[[], bytes | None] | None = None,
        host: str = "0.0.0.0",
        port: int = 8765,
    ) -> None:
        self.payload_provider = payload_provider
        self.graph_png_provider = graph_png_provider
        self.host = host
        self.port = int(port)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{local_ipv4()}:{self.port}"

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        provider = self.payload_provider
        graph_provider = self.graph_png_provider

        class Handler(BaseHTTPRequestHandler):
            server_version = "FocusDashboard/2.0"

            def _send(self, status: int, content_type: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "script-src 'self' 'unsafe-inline'; img-src 'self' data: https://pixe.la",
                )
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                path = urllib.parse.urlparse(self.path).path
                try:
                    if path == "/":
                        self._send(200, "text/html; charset=utf-8", HTML_PAGE.encode("utf-8"))
                    elif path == "/api/status":
                        body = json.dumps(provider(), ensure_ascii=False).encode("utf-8")
                        self._send(200, "application/json; charset=utf-8", body)
                    elif path == "/api/health":
                        self._send(200, "application/json", b'{"ok":true}')
                    elif path == "/manifest.json":
                        body = json.dumps(MANIFEST).encode("utf-8")
                        self._send(200, "application/manifest+json", body)
                    elif path == "/robots.txt":
                        self._send(200, "text/plain; charset=utf-8", b"User-agent: *\nDisallow: /\n")
                    elif path == "/pixela.png" and graph_provider is not None:
                        image = graph_provider()
                        if image:
                            self._send(200, "image/png", image)
                        else:
                            self._send(404, "text/plain; charset=utf-8", b"Graph unavailable")
                    else:
                        self._send(404, "text/plain; charset=utf-8", b"Not found")
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as exc:
                    body = json.dumps({"error": str(exc)}).encode("utf-8")
                    self._send(500, "application/json", body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
