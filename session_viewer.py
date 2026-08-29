"""Session viewer: a tiny local web dashboard for the crawl.

Reads the resume cache (SQLite), adaptive rates (data/rates.json), canon summary
(data/canon/meta.json) and the crawl log, then serves a single auto-refreshing
page on http://127.0.0.1:<port>. Stdlib only — no dependencies.

Usage:
    python session_viewer.py [--port 8080] [--log <path>]
    env: TM_VIEWER_PORT, TM_VIEWER_LOG
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from config import CACHE_PATH, CANON_DIR, CDN_RPS, HTML_RPS, RATES_PATH

PORT = int(os.getenv("TM_VIEWER_PORT", "8080"))
LOG_PATH = Path(os.getenv("TM_VIEWER_LOG", str(Path(__file__).resolve().parent / "logs" / "crawl.log")))
ACTIVE_WINDOW = 120  # s; if the log wasn't written within this window -> idle

logger = logging.getLogger("scraper.viewer")


def classify(url: str) -> str:
    u = url
    if "competitions/" in u:
        return "competitions"
    if "/clubs/" in u and u.endswith("/players"):
        return "club_rosters"
    if "/clubs/" in u:
        return "club_profiles"
    if "most-valuable" in u:
        return "nt_list"
    if "/national-teams/" in u and u.endswith("/players"):
        return "nt_rosters"
    if "/national-teams/" in u:
        return "nt_profiles"
    if "/players/" in u and "market_value" in u:
        return "player_market_values"
    if "ceapi" in u:
        return "mv_ceapi_direct"
    if "/players/" in u:
        return "player_profiles"
    if "mitarbeiter" in u:
        return "coach_ids"
    if "/coaches/" in u:
        return "coaches"
    return "other"


def _cache_stats() -> dict:
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute(
                "SELECT url, status, fetched_at FROM requests"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return {"error": str(exc)}

    by_status: dict[str, int] = {}
    by_cat: dict[str, int] = {}
    last_minute = 0
    cutoff = time.time() - 60
    for url, status, ts in rows:
        by_status[str(status)] = by_status.get(str(status), 0) + 1
        cat = classify(url)
        by_cat[cat] = by_cat.get(cat, 0) + 1
        if ts >= cutoff:
            last_minute += 1
    return {
        "total": len(rows),
        "by_status": dict(sorted(by_status.items())),
        "by_category": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
        "last_minute": last_minute,
    }


def _rates() -> dict:
    rates = {"html": HTML_RPS, "cdn": CDN_RPS}
    try:
        with open(RATES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in ("html", "cdn"):
                if isinstance(data.get(key), (int, float)):
                    rates[key] = float(data[key])
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return rates


def _canon() -> dict | None:
    meta = CANON_DIR / "meta.json"
    try:
        with open(meta, encoding="utf-8") as f:
            data = json.load(f)
        return {"snapshot_date": data.get("snapshot_date"),
                "counts": data.get("counts")}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _log_tail(n: int = 80) -> dict:
    try:
        if not LOG_PATH.exists():
            return {"lines": [], "mtime": 0}
        mtime = LOG_PATH.stat().st_mtime
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        tail = [l.rstrip("\n") for l in lines[-n:]]
        return {"lines": tail, "mtime": mtime}
    except OSError as exc:
        return {"lines": [f"log read error: {exc}"], "mtime": 0}


def stats() -> dict:
    log = _log_tail()
    mtime = log.get("mtime") or 0
    active = mtime > 0 and (time.time() - mtime) < ACTIVE_WINDOW
    warn = sum(1 for l in log.get("lines", []) if "WARNING" in l or "ERROR" in l)
    return {
        "active": active,
        "now": time.time(),
        "log_mtime": mtime,
        "log_age_s": round(time.time() - mtime, 1) if mtime else None,
        "cache": _cache_stats(),
        "rates": _rates(),
        "canon": _canon(),
        "log": log.get("lines", []),
        "warnings_last80": warn,
    }


INDEX_URL = "https://www.transfermarkt.com/wettbewerbe/national/"


def _leagues_stats() -> dict:
    """Per-league crawl progress from the resume cache (club lists + rosters)."""
    import re
    from bs4 import BeautifulSoup
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=5)
        try:
            idx = con.execute("SELECT payload FROM requests WHERE url=? AND status=200",
                              (INDEX_URL,)).fetchone()
            lists = con.execute(
                "SELECT url, payload FROM requests WHERE url LIKE '%/competitions/%/clubs' AND status=200"
            ).fetchall()
            rosters = con.execute(
                "SELECT url FROM requests WHERE url LIKE '%/clubs/%/players' AND status=200"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return {"error": str(exc)}

    roster_clubs = {m.group(1) for (url,) in rosters
                    if (m := re.search(r"/clubs/(\d+)/players", url))}

    leagues: dict[str, dict] = {}
    if idx:
        soup = BeautifulSoup(idx[0], "html.parser")
        for a in soup.find_all("a", href=True):
            h = a.get("href") or ""
            if "/wettbewerb/" in h:
                lid = h.split("/wettbewerb/")[-1].split("/")[0]
                if lid not in leagues:
                    leagues[lid] = {"id": lid,
                                    "name": a.get("title") or a.get_text(strip=True),
                                    "clubs": None, "rosters": 0, "status": "pending"}

    for url, payload in lists:
        m = re.search(r"/competitions/(\w+)/clubs", url)
        if not m:
            continue
        lid = m.group(1)
        try:
            clubs = [c.get("id") for c in (json.loads(payload).get("clubs") or [])]
        except (json.JSONDecodeError, TypeError):
            continue
        entry = leagues.setdefault(lid, {"id": lid, "name": lid,
                                         "clubs": None, "rosters": 0, "status": "pending"})
        entry["clubs"] = len(clubs)
        entry["rosters"] = sum(1 for c in clubs if c in roster_clubs)
        entry["status"] = "done" if entry["rosters"] >= entry["clubs"] else "in-progress"

    done = sum(1 for v in leagues.values() if v["status"] == "done")
    prog = sum(1 for v in leagues.values() if v["status"] == "in-progress")
    return {"leagues": list(leagues.values()), "total": len(leagues),
            "done": done, "in_progress": prog, "rostered_clubs": len(roster_clubs)}


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>TM scraper — сессия</title>
<style>
 body{font:14px/1.5 system-ui,Segoe UI,sans-serif;background:#111;color:#ddd;margin:0}
 .wrap{max-width:980px;margin:0 auto;padding:16px}
 h1{font-size:18px;margin:0 0 4px}
 h2{font-size:14px;margin:16px 0 6px;color:#aaa;text-transform:uppercase;letter-spacing:.5px}
 table{border-collapse:collapse;width:100%}
 td,th{border:1px solid #2a2a2a;padding:4px 8px;text-align:left;font-size:13px}
 th{color:#888;font-weight:600}
 .num{text-align:right;font-variant-numeric:tabular-nums}
 .badge{display:inline-block;padding:2px 10px;border-radius:10px;font-size:12px;font-weight:600}
 .on{background:#1e5;color:#041}
 .off{background:#555;color:#ddd}
 .warn{color:#f80}
 pre{font:12px/1.4 Consolas,monospace;background:#0c0c0c;border:1px solid #2a2a2a;
     padding:8px;overflow:auto;max-height:420px;white-space:pre-wrap}
 .grid{display:flex;gap:16px;flex-wrap:wrap}
 .grid>div{flex:1;min-width:260px}
 .kpi{display:inline-block;background:#1c1c1c;border:1px solid #2a2a2a;border-radius:8px;
      padding:8px 14px;margin:4px 6px 0 0}
 .kpi b{display:block;font-size:22px}
 .kpi span{color:#999;font-size:12px}
</style></head><body><div class="wrap">
<h1>TM scraper — сессия</h1>
<div id="status" class="kpi"><span>статус</span><b id="statusb">…</b></div>
<div class="kpi"><span>кэш всего</span><b id="total">…</b></div>
<div class="kpi"><span>за посл. минуту</span><b id="min">…</b></div>
<div class="kpi"><span>rate html</span><b id="rhtml">…</b></div>
<div class="kpi"><span>rate cdn</span><b id="rcdn">…</b></div>
<div class="kpi"><span>warn/err (80)</span><b id="warn">…</b></div>

<div class="grid">
 <div><h2>По эндпоинтам</h2><table id="cat"><tbody></tbody></table></div>
 <div><h2>По статусам</h2><table id="st"><tbody></tbody></table></div>
 <div><h2>Канон</h2><table id="canon"><tbody></tbody></table></div>
</div>
<h2>Лиги (крол составов) <span id="lgsum" class="warn" style="font-size:12px"></span></h2>
<table id="leagues"><tbody></tbody></table>
<h2>Лог (хвост)</h2><pre id="log">…</pre>
</div>
<script>
async function refresh(){
  let s;
  try{ s=await (await fetch('/api/stats')).json(); }catch(e){ return; }
  document.getElementById('statusb').textContent =
    (s.active?'АКТИВЕН':'ПАУЗА') + (s.log_age_s!=null?' · '+s.log_age_s+'с назад':'');
  document.getElementById('statusb').className='badge '+(s.active?'on':'off');
  document.getElementById('total').textContent=s.cache.total??'—';
  document.getElementById('min').textContent=s.cache.last_minute??'—';
  document.getElementById('rhtml').textContent=s.rates.html+' rps';
  document.getElementById('rcdn').textContent=s.rates.cdn+' rps';
  document.getElementById('warn').textContent=s.warnings_last80;
  const cat=document.getElementById('cat').querySelector('tbody');
  cat.innerHTML='';
  for(const [k,v] of Object.entries(s.cache.by_category||{})){
    const tr=document.createElement('tr');
    tr.innerHTML='<td>'+k+'</td><td class="num">'+v+'</td>';
    cat.appendChild(tr);
  }
  const st=document.getElementById('st').querySelector('tbody');
  st.innerHTML='';
  for(const [k,v] of Object.entries(s.cache.by_status||{})){
    const tr=document.createElement('tr');
    tr.innerHTML='<td>HTTP '+k+'</td><td class="num">'+v+'</td>';
    st.appendChild(tr);
  }
  const ca=document.getElementById('canon').querySelector('tbody');
  ca.innerHTML='';
  if(s.canon){
    const row=document.createElement('tr');
    row.innerHTML='<td>снимок</td><td>'+s.canon.snapshot_date+'</td>';
    ca.appendChild(row);
    for(const [k,v] of Object.entries(s.canon.counts||{})){
      const tr=document.createElement('tr');
      tr.innerHTML='<td>'+k+'</td><td class="num">'+v+'</td>';
      ca.appendChild(tr);
    }
  } else { ca.innerHTML='<tr><td colspan="2">канон ещё не собран</td></tr>'; }
  document.getElementById('log').textContent=(s.log||[]).join('\\n');
}
async function refreshLeagues(){
  let l;
  try{ l=await (await fetch('/api/leagues')).json(); }catch(e){ return; }
  document.getElementById('lgsum').textContent =
    ' · сделано '+l.done+' · идёт '+l.in_progress+' · всего '+l.total+' · клубов с составами '+l.rostered_clubs;
  const tb=document.getElementById('leagues').querySelector('tbody');
  tb.innerHTML='';
  for(const lg of (l.leagues||[])){
    const tr=document.createElement('tr');
    const bar = lg.clubs ? Math.round(100*lg.rosters/lg.clubs) : 0;
    const status = lg.status==='done' ? '✅' : (lg.status==='in-progress' ? '⏳' : '•');
    tr.innerHTML = '<td>'+status+'</td><td>'+lg.name+'</td><td>'+lg.id+'</td>' +
      '<td class="num">'+(lg.clubs??'—')+'</td><td class="num">'+(lg.rosters??'—')+'</td>' +
      '<td class="num">'+(lg.clubs?bar+'%':'—')+'</td>';
    tb.appendChild(tr);
  }
}
refresh(); setInterval(refresh, 3000);
refreshLeagues(); setInterval(refreshLeagues, 5000);
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/stats"):
            body = json.dumps(stats()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/leagues"):
            body = json.dumps(_leagues_stats()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - silence access log
        pass


def main() -> None:
    global LOG_PATH
    parser = argparse.ArgumentParser(description="Web dashboard for the crawl session.")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--log", default=str(LOG_PATH))
    args = parser.parse_args()
    LOG_PATH = Path(args.log)
    logging.basicConfig(level=logging.INFO)
    srv = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Session viewer: http://127.0.0.1:{args.port}  (log: {LOG_PATH})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()