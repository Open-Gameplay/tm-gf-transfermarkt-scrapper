# Handoff: полный краул TM (все лиги + все сборные)

## Контекст

Репозиторий: `C:\Users\Egor\Desktop\projects\transfermarkt_scrapper` (ветка `scraper-v2`)

API: `C:\Users\Egor\Desktop\projects\transfermarkt-api` (ветка `main`, порт 8000)
Запуск API:
```
& "C:\Users\Egor\Desktop\projects\transfermarkt-api\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Пилот завершён

Пилот (England GB1/GB2, Spain ES1/ES2, Russia RU1/RU2 + top-20 сборных) — все данные скачаны:
- 120 клубов, 3232 игрока, 20 сборных
- Все изображения (faces big, logos, emblems, flags)
- JSON: `data\pilot\clubs.json`, `data\pilot\national_teams.json`

## Задача: полный краул

Скачать **все** лиги TM (269 лиг, ~4000 клубов) + **все** сборные (250).

### Что нужно

1. **Все лиги TM** — `fetch_leagues.py` (уже работает, нужно расширить PILOT_LEAGUES на все 269)
2. **Все俱乐部** — `fetch_club_profiles.py` + `fetch_club_rosters.py --refresh`
3. **Все сборные** — `fetch_national_teams.py --top 250`
4. **Все изображения** — `download_pilot_images.py` (уже фильтрует по пилоту, нужно убрать фильтр)
5. **JSON** — `build_pilot_json.py` (переименовать в build_full_json.py)

### Порядок запуска

```
1. python fetch_leagues.py --leagues <ALL_LEAGUE_IDS>
2. python fetch_club_profiles.py
3. python fetch_club_rosters.py --refresh
4. python fetch_national_teams.py --top 250
5. python download_pilot_images.py --all
6. python build_pilot_json.py --out data/full
7. python retry_failures.py
```

### Время и ресурсы

| Этап | Запросов | Время (2 rps) |
|---|---|---|
| Все лиги (269) | 269 | ~2 мин |
| Все клубы (~4000) | 4000 | ~35 мин |
| Все составы (~4000) | 4000 | ~35 мин |
| Все сборные (250) | 500 | ~4 мин |
| Все изображения (~150k) | ~150k | ~30 мин |
| **Итого** | **~162k** | **~2.5 часа** |

### Лимиты TM

- HTML: 2 rps (через API)
- CDN: 15 rps (фото, логотипы)
- tmapi: не блокируется
- Адаптивная скорость: старт 0.5 rps, растёт при успехах
- Circuit breaker: 120 сек пауза при 3 ошибках подряд

### Что скачается

| Данные | Кол-во |
|---|---|
| Лиги | ~269 |
| Клубы | ~4000 |
| Игроки | ~120k |
| Сборные | ~250 |
| Игроков сборных | ~10k |
| Faces | ~100k |
| Логотипы клубов | ~4000 |
| Логотипы лиг | ~269 |
| Эмблемы | ~250 |
| Флаги | ~200 |

### Ограничения

- **Профили игроков TM** — заблокированы (HTML). Используем tmapi (не блокируется).
- **Player extras** (stats/injuries/achievements) — доступны через API, но weight=2. Массовый сбор ~6 дней.
- **open-football** — отложена в долгий ящик. TM доступен напрямую.
- **Кэш** — SQLite `tm_cache.sqlite3`. Ресумаблно. Не удалять между запусками.

### Файлы

| Файл | Назначение |
|---|---|
| `fetch_leagues.py` | Списки клубов по лигам |
| `fetch_club_profiles.py` | Профили клубов (colors, logo, tier) |
| `fetch_club_rosters.py` | Составы клубов (imageUrl) |
| `fetch_national_teams.py` | Профили + составы сборных |
| `download_pilot_images.py` | Изображения (faces, logos, emblems, flags) |
| `build_pilot_json.py` | Сборка JSON |
| `retry_failures.py` | Повтор всех ошибок |
| `pilot_crawl.py` | Оркестратор (все шаги + retry) |
| `session_viewer.py` | Веб-дашборд: http://127.0.0.1:8080 |

### Баги, исправленные

- `transfermarkt.us` → `.com` в `app/services/clubs/profile.py:20` (клуб 677 таймаутится на .us)
- `_is_default()` в `download_pilot_images.py` — пропускает placeholder images
- `_dl_big()` — пробует `portrait/big/`, fallback на `portrait/medium/`
