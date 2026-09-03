"""Session viewer: web dashboard for crawl progress.

Shows crawl progress: countries, leagues, clubs, players, national teams, images.

Usage:
    python session_viewer.py [--port 8080] [--log <path>]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from config import CACHE_PATH, CANON_DIR, CDN_RPS, HTML_RPS, IMAGES_DIR, RATES_PATH

PORT = int(os.getenv("TM_VIEWER_PORT", "8080"))
LOG_PATH = Path(os.getenv("TM_VIEWER_LOG", str(Path(__file__).resolve().parent / "logs" / "crawl.log")))
ACTIVE_WINDOW = 120

logger = logging.getLogger("scraper.viewer")


def _db_query(sql: str, params=()) -> list:
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=5)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []


def _pilot_progress() -> dict:
    """Compute progress from cache (all leagues, not just pilot)."""
    # Get all cached data
    comp_lists = _db_query(
        "SELECT url, payload FROM requests WHERE url LIKE '%/competitions/%/clubs' AND status=200"
    )
    club_profiles = _db_query(
        "SELECT url, payload FROM requests WHERE url LIKE '%/clubs/%/profile' AND status=200"
    )
    club_rosters = _db_query(
        "SELECT url, payload FROM requests WHERE url LIKE '%/clubs/%/players' AND status=200"
    )
    nt_profiles = _db_query(
        "SELECT url, payload FROM requests WHERE url LIKE '%/national-teams/%/profile' AND status=200"
    )
    nt_rosters = _db_query(
        "SELECT url, payload FROM requests WHERE url LIKE '%/national-teams/%/players' AND status=200"
    )

    # Index club data
    roster_clubs = {}  # cid -> {player_count, with_photo}
    for (url, payload) in club_rosters:
        m = re.search(r"/clubs/(\d+)/players", url)
        if not m:
            continue
        cid = m.group(1)
        try:
            players = json.loads(payload).get("players") or []
            photos = sum(1 for p in players if p.get("imageUrl"))
            roster_clubs[cid] = {"count": len(players), "photos": photos}
        except:
            pass

    profile_clubs = {}  # cid -> {name, league_id, country_id, colors, logo}
    for (url, payload) in club_profiles:
        m = re.search(r"/clubs/(\d+)/profile", url)
        if not m:
            continue
        cid = m.group(1)
        try:
            data = json.loads(payload)
            league = data.get("league") or {}
            profile_clubs[cid] = {
                "name": data.get("name"),
                "league_id": league.get("id"),
                "league_name": league.get("name"),
                "country_id": str(league.get("countryId", "")),
                "country_name": league.get("countryName", ""),
                "colors": data.get("colors", []),
                "logo": data.get("image"),
            }
        except:
            pass

    # Index competition club lists
    comp_clubs = {}  # lid -> [cid]
    for (url, payload) in comp_lists:
        m = re.search(r"/competitions/(\w+)/clubs", url)
        if not m:
            continue
        lid = m.group(1)
        try:
            clubs = [c.get("id") for c in (json.loads(payload).get("clubs") or [])]
            comp_clubs[lid] = clubs
        except:
            pass

    # Index national team data
    nt_data = {}  # tid -> {name, player_count, with_photo}
    for (url, payload) in nt_profiles:
        m = re.search(r"/national-teams/(\d+)/profile", url)
        if not m:
            continue
        tid = m.group(1)
        try:
            data = json.loads(payload)
            nt_data[tid] = {"name": data.get("name"), "players": 0, "photos": 0}
        except:
            pass

    for (url, payload) in nt_rosters:
        m = re.search(r"/national-teams/(\d+)/players", url)
        if not m:
            continue
        tid = m.group(1)
        try:
            players = json.loads(payload).get("players") or []
            if tid not in nt_data:
                nt_data[tid] = {"name": "?", "players": 0, "photos": 0}
            nt_data[tid]["players"] = len(players)
        except:
            pass

    # Build country -> league hierarchy dynamically from club profiles
    countries = {}  # country_id -> {name, leagues: {lid -> stats}}
    for cid, pinfo in profile_clubs.items():
        country_id = pinfo.get("country_id", "")
        league_id = pinfo.get("league_id", "")
        if not country_id or not league_id:
            continue
        if country_id not in countries:
            countries[country_id] = {
                "name": pinfo.get("country_name", ""),
                "leagues": {},
                "total_clubs": 0, "clubs_with_profile": 0, "clubs_with_roster": 0,
                "total_players": 0, "players_with_photo": 0,
            }
        if league_id not in countries[country_id]["leagues"]:
            countries[country_id]["leagues"][league_id] = {
                "id": league_id, "name": pinfo.get("league_name", league_id),
                "clubs_total": 0, "clubs_profile": 0, "clubs_roster": 0,
                "players": 0, "photos": 0,
            }

    # Count clubs per league from comp_clubs
    for lid, cids in comp_clubs.items():
        # Find which country this league belongs to
        country_id = None
        league_name = lid
        for pinfo in profile_clubs.values():
            if pinfo.get("league_id") == lid:
                country_id = pinfo.get("country_id", "")
                league_name = pinfo.get("league_name", lid)
                break
        if not country_id:
            # Fallback: use first club's country
            for cid in cids:
                if cid in profile_clubs:
                    country_id = profile_clubs[cid].get("country_id", "")
                    league_name = profile_clubs[cid].get("league_name", lid)
                    break
        if not country_id:
            country_id = "unknown"
        if country_id not in countries:
            countries[country_id] = {
                "name": "", "leagues": {},
                "total_clubs": 0, "clubs_with_profile": 0, "clubs_with_roster": 0,
                "total_players": 0, "players_with_photo": 0,
            }
        if lid not in countries[country_id]["leagues"]:
            countries[country_id]["leagues"][lid] = {
                "id": lid, "name": league_name,
                "clubs_total": 0, "clubs_profile": 0, "clubs_roster": 0,
                "players": 0, "photos": 0,
            }
        countries[country_id]["leagues"][lid]["clubs_total"] = len(cids)

    # Fill in profile/roster counts
    for country_id, cinfo in countries.items():
        for lid, lstats in cinfo["leagues"].items():
            league_cids = comp_clubs.get(lid, [])
            for cid2 in league_cids:
                if cid2 in profile_clubs:
                    lstats["clubs_profile"] += 1
                if cid2 in roster_clubs:
                    lstats["clubs_roster"] += 1
                    lstats["players"] += roster_clubs[cid2]["count"]
                    lstats["photos"] += roster_clubs[cid2]["photos"]
            cinfo["total_clubs"] += lstats["clubs_total"]
            cinfo["clubs_with_profile"] += lstats["clubs_profile"]
            cinfo["clubs_with_roster"] += lstats["clubs_roster"]
            cinfo["total_players"] += lstats["players"]
            cinfo["players_with_photo"] += lstats["photos"]

    # National teams
    nt_list = []
    for tid in sorted(nt_data.keys(), key=lambda x: nt_data[x].get("name", "")):
        info = nt_data[tid]
        nt_list.append({"id": tid, "name": info["name"],
                        "players": info["players"], "photos": info["photos"]})

    # Image stats
    images = {"faces": 0, "logos": 0, "leagues": 0, "emblems": 0, "flags": 0}
    for d in ["faces", "logos", "leagues", "emblems", "flags"]:
        img_dir = IMAGES_DIR / d
        if img_dir.exists():
            images[d] = sum(1 for f in img_dir.iterdir() if f.is_file())

    # Totals
    total_clubs = sum(c["total_clubs"] for c in countries.values())
    total_profiles = sum(c["clubs_with_profile"] for c in countries.values())
    total_rosters = sum(c["clubs_with_roster"] for c in countries.values())
    total_players = sum(c["total_players"] for c in countries.values())
    total_photos = sum(c["players_with_photo"] for c in countries.values())
    nt_players = sum(n["players"] for n in nt_list)

    return {
        "countries": countries,
        "national_teams": nt_list,
        "images": images,
        "totals": {
            "clubs": total_clubs, "clubs_profile": total_profiles,
            "clubs_roster": total_rosters, "players": total_players,
            "players_photo": total_photos, "nt_teams": len(nt_list),
            "nt_players": nt_players,
        },
    }


def _log_tail(n: int = 50) -> dict:
    try:
        if not LOG_PATH.exists():
            return {"lines": [], "mtime": 0}
        mtime = LOG_PATH.stat().st_mtime
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return {"lines": [l.rstrip("\n") for l in lines[-n:]], "mtime": mtime}
    except OSError:
        return {"lines": [], "mtime": 0}


def _rates() -> dict:
    rates = {"html": HTML_RPS, "cdn": CDN_RPS}
    try:
        with open(RATES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for key in ("html", "cdn"):
            if isinstance(data.get(key), (int, float)):
                rates[key] = float(data[key])
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return rates


def stats() -> dict:
    log = _log_tail()
    mtime = log.get("mtime") or 0
    active = mtime > 0 and (time.time() - mtime) < ACTIVE_WINDOW
    return {
        "active": active,
        "log_age_s": round(time.time() - mtime, 1) if mtime else None,
        "crawl": _pilot_progress(),
        "rates": _rates(),
        "log": log.get("lines", []),
    }


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>TM scraper — crawl</title>
<style>
 body{font:14px/1.5 system-ui,Segoe UI,sans-serif;background:#111;color:#ddd;margin:0}
 .wrap{max-width:1100px;margin:0 auto;padding:16px}
 h1{font-size:18px;margin:0 0 8px}
 h2{font-size:14px;margin:16px 0 6px;color:#aaa;text-transform:uppercase;letter-spacing:.5px}
 table{border-collapse:collapse;width:100%}
 td,th{border:1px solid #2a2a2a;padding:4px 8px;text-align:left;font-size:13px}
 th{color:#888;font-weight:600}
 .num{text-align:right;font-variant-numeric:tabular-nums}
 .badge{display:inline-block;padding:2px 10px;border-radius:10px;font-size:12px;font-weight:600}
 .on{background:#1e5;color:#041} .off{background:#555;color:#ddd}
 .kpi{display:inline-block;background:#1c1c1c;border:1px solid #2a2a2a;border-radius:8px;
      padding:8px 14px;margin:4px 6px 0 0}
 .kpi b{display:block;font-size:22px} .kpi span{color:#999;font-size:12px}
 .pct{color:#5bf}
 pre{font:12px/1.4 Consolas,monospace;background:#0c0c0c;border:1px solid #2a2a2a;
     padding:8px;overflow:auto;max-height:320px;white-space:pre-wrap}
 .done{color:#5f5} .wip{color:#fa0} .miss{color:#f55}
</style></head><body><div class="wrap">
<h1>TM scraper — crawl</h1>
<div id="status" class="kpi"><span>статус</span><b id="statusb">…</b></div>
<div class="kpi"><span>rate html</span><b id="rhtml">…</b></div>
<div class="kpi"><span>rate cdn</span><b id="rcdn">…</b></div>

<h2>Сводка</h2>
<div id="totals"></div>

<h2>Страны / Лиги</h2>
<div id="countries"></div>

<h2>Сборные</h2>
<table id="nt"><thead><tr><th>Команда</th><th>ID</th><th class="num">Игроков</th></tr></thead>
<tbody></tbody></table>

<h2>Изображения</h2>
<div id="images"></div>

<h2>Лог</h2>
<pre id="log">…</pre>
</div>
<script>
async function refresh(){
  let s;
  try{ s=await(await fetch('/api/stats')).json(); }catch(e){ return; }
  const p=s.crawl;
  document.getElementById('statusb').textContent=
    (s.active?'АКТИВЕН':'ПАУЗА')+(s.log_age_s!=null?' · '+s.log_age_s+'с назад':'');
  document.getElementById('statusb').className='badge '+(s.active?'on':'off');
  document.getElementById('rhtml').textContent=s.rates.html+' rps';
  document.getElementById('rcdn').textContent=s.rates.cdn+' rps';

  // Totals
  const T=p.totals;
  document.getElementById('totals').innerHTML=
    kpi('Клубов',T.clubs_profile+'/'+T.clubs)+' '+
    kpi('Игроков',T.players)+' '+
    kpi('С фото',T.players_photo,'pct')+' '+
    kpi('Сборных',T.nt_teams)+' '+
    kpi('Игроков сборных',T.nt_players);

  // Countries
  let ch='';
  for(const [cid,c] of Object.entries(p.countries)){
    ch+='<h3>'+c.name+'</h3><table><tr><th>Лига</th><th class="num">Клубов</th><th class="num">Профили</th><th class="num">Составы</th><th class="num">Игроков</th><th class="num">Фото</th></tr>';
    for(const [lid,l] of Object.entries(c.leagues)){
      const cls=l.clubs_roster>=l.clubs_total?'done':(l.clubs_roster>0?'wip':'miss');
      ch+='<tr><td>'+l.name+' ('+lid+')</td><td class="num">'+l.clubs_total+'</td><td class="num '+cls+'">'+l.clubs_profile+'</td><td class="num '+cls+'">'+l.clubs_roster+'</td><td class="num">'+l.players+'</td><td class="num">'+l.photos+'</td></tr>';
    }
    ch+='<tr style="font-weight:600"><td>Итого</td><td class="num">'+c.total_clubs+'</td><td class="num">'+c.clubs_with_profile+'</td><td class="num">'+c.clubs_with_roster+'</td><td class="num">'+c.total_players+'</td><td class="num">'+c.players_with_photo+'</td></tr></table>';
  }
  document.getElementById('countries').innerHTML=ch;

  // National teams
  const ntb=document.getElementById('nt').querySelector('tbody');
  ntb.innerHTML='';
  for(const nt of p.national_teams){
    const tr=document.createElement('tr');
    tr.innerHTML='<td>'+nt.name+'</td><td>'+nt.id+'</td><td class="num">'+nt.players+'</td>';
    ntb.appendChild(tr);
  }

  // Images
  const I=p.images;
  document.getElementById('images').innerHTML=
    kpi('Лица (фото)',I.faces)+' '+kpi('Логотипы клубов',I.logos)+' '+
    kpi('Логотипы лиг',I.leagues)+' '+kpi('Эмблемы',I.emblems)+' '+kpi('Флаги',I.flags);

  // Log
  document.getElementById('log').textContent=(s.log||[]).join('\\n');
}
function kpi(label,val,cls){return '<div class="kpi"><span>'+label+'</span><b'+(cls?' class="'+cls+'"':'')+'>'+(val??'—')+'</b></div>';}
refresh();setInterval(refresh,3000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/stats"):
            body = json.dumps(stats()).encode("utf-8")
        else:
            body = PAGE.encode("utf-8")
        ct = "application/json; charset=utf-8" if self.path.startswith("/api/") else "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002
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
