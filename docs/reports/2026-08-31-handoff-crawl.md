# Handoff: краул данных (сессия 2)

Датированный handoff-пакет для новой сессии. Создан 2026-08-31. Актуальное состояние — в
`docs/wiki/конвейер.md`, хронология — `log.md`.

---

# Промпт для новой сессии

Ты работаешь с `C:\Users\Egor\Desktop\projects\transfermarkt_scrapper` (ветка `scraper-v2`).

Скрипты для краула **уже написаны**, нужно **запустить** и проверить результат.

## Запуск API

```bat
cd C:\Users\Egor\Desktop\projects\transfermarkt-api
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Скрейпер: `.venv\Scripts\python` внутри `transfermarkt_scrapper`.

## Что сделать (по порядку)

### 1. fetch_all_rosters.py --refresh

```bat
cd C:\Users\Egor\Desktop\projects\transfermarkt_scrapper
.venv\Scripts\python fetch_all_rosters.py --refresh
```

Добор `imageUrl` в составы, закэшированные до фикса фото (~1900 клубов без фото).
Скрипт сам пропускает те, где фото уже есть. Ресумаблно.

**Проверка:** после завершения запустить `_photo_check.py` (создать если нет) —
проверить что imageUrl покрытие > 0%.

### 2. fetch_all_club_profiles.py

```bat
.venv\Scripts\python fetch_all_club_profiles.py
```

Профили (`/clubs/{id}/profile`) для **всех** 3949 клубов из кэша (цвета, стадион, лига, лого).
Профиль клуба — не заблокированный путь. Ресумаблно.

**Проверка:** после завершения проверить что количество club_profiles в кэше выросло
с 198 до ~3949.

### 3. fetch_all_national_teams.py

```bat
.venv\Scripts\python fetch_all_national_teams.py
```

Все сборные: `/national-teams/most-valuable` (один запрос, все ID внутри) + profile + roster
для каждой. Ресумаблно.

**Проверка:** после завершения проверить что NT profiles в кэше >= 100 (все сборные мира).

### После завершения 1-3

Показать статистику кэша (сколько клубов/сборных/игроков с фото) и **решить** что делать дальше:

- download_images.py (faces + club logos + league logos)
- build_gf_database.py (конвертер канон → GF SQLite)
- fetch_player_extras.py (stats/injuries/achievements — ~535k запросов, ~6 дней)
- что-то ещё

## Что уже сделано (контекст предыдущей сессии)

- **tmapi** (`tmapi.transfermarkt.technology`) — внутренний JSON API TM, reverse-engineered.
  Не блокируется. Заменяет заблокированный HTML-скрейпинг профилей/стоимостей.
- **transfermarkt-api переписан**: profile.py → tmapi, market_value.py → tmapi, stats.py → ceapi.
- **Все 13 эндпоинтов работают** без бана (test All endpoints — OK).
- **open-football-database отложена** в долгий ящик (TM доступен напрямую).
- **Скрипты созданы** но не запущены: fetch_all_club_profiles.py, fetch_all_national_teams.py,
  fetch_player_extras.py, download_images.py (обновлён).
- **Вики обновлена**: конвейер.md, глоссарий.md, открытые-вопросы.md, log.md.

## Ограничения

- Не ходить на HTML-страницы профилей игроков (`/profil/spieler/`) — заблокированы.
- Все атрибуты игроков — через tmapi (`tmapi.transfermarkt.technology/player/{id}`).
- Профили клубов (`/clubs/{id}/profile`) — не заблокированы.
- tmapi может измениться без предупреждения (не публичный API).
- Кэш: `tm_cache.sqlite3` (WAL, TTL 7 дней, только 2xx).

## Полезные скрипты

```bat
# Проверка покрытия фото
.venv\Scripts\python -c "import sqlite3,json; con=sqlite3.connect('file:tm_cache.sqlite3?mode=ro',uri=True); rows=con.execute(\"SELECT payload FROM requests WHERE url LIKE '%/clubs/%/players' AND status=200\").fetchall(); con.close(); total=sum(len(json.loads(p).get('players',[])) for p, in rows); photo=sum(1 for p,in rows for pl in json.loads(p).get('players',[]) if pl.get('imageUrl')); print(f'{photo}/{total} ({100*photo/max(total,1):.1f}%)')"

# Проверка club profiles в кэше
.venv\Scripts\python -c "import sqlite3; con=sqlite3.connect('file:tm_cache.sqlite3?mode=ro',uri=True); n=con.execute(\"SELECT COUNT(*) FROM requests WHERE url LIKE '%/clubs/%/profile' AND status=200\").fetchone()[0]; con.close(); print(f'Club profiles: {n}')"

# Проверка NT profiles в кэше
.venv\Scripts\python -c "import sqlite3; con=sqlite3.connect('file:tm_cache.sqlite3?mode=ro',uri=True); n=con.execute(\"SELECT COUNT(*) FROM requests WHERE url LIKE '%/national-teams/%/profile' AND status=200\").fetchone()[0]; con.close(); print(f'NT profiles: {n}')"

# Session viewer
.venv\Scripts\python session_viewer.py --port 8080
```
