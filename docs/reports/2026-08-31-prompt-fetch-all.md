# Промпт: стягивание всех данных с TM

## Запуск API
```
& "C:\Users\Egor\Desktop\projects\transfermarkt-api\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Рабочая папка
`C:\Users\Egor\Desktop\projects\transfermarkt_scrapper` (ветка `scraper-v2`), скрейпер: `.venv\Scripts\python`

## Что делаем

1. **Составы клубов** — `fetch_all_rosters.py --refresh`
   - Индекс 269 лиг → 3949 клубов → `/clubs/{id}/players` для каждого
   - `--refresh` перекачивает все составы (нужен imageUrl, которого нет в старом кэше)
   - Фон, ресумаблно, ~2 часа на 0.5 rps
   - Прогресс: `session_viewer.py` на :8080, лог в `logs/crawl.log`

2. **Профили клубов** — `fetch_all_club_profiles.py`
   - Все club IDs из кэша → `/clubs/{id}/profile` для каждого
   - Цвета, стадион, лига, лого. ~3949 запросов, ~33 мин

3. **Сборные** — `fetch_all_national_teams.py`
   - `/national-teams/most-valuable` (все ID) → profile + roster для каждой
   - ~2 мин

## Что НЕ делаем
- open-football-database — отложена в долгий ящик
- HTML-скрейпинг профилей игроков — заблокирован, заменён на tmapi
- Player extras (stats/injuries/achievements) — пока не mass-scale

## Ловушки
- API必须 работать на :8000 пока краул активен
- circuit breaker открывается при падении API → ждёт 120 сек
- Адаптивная скорость стартует с 0.5 rps и растёт при успехах
- `use_cache=False` в refresh — кэшируем ответ вручную после получения
