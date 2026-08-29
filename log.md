# Хронология

Append-only хронология проекта (паттерн LLM-Wiki): вехи, деплои, решения, опровергнутые гипотезы.
Дописывать в конец; старые записи не редактировать и не удалять. Парсится: `grep "^## \[" log.md | tail -5`.

Типы: `session` (итог сессии работы агента), `milestone` (веха), `deploy` (релиз), `decision`
(решение), `rejected` (опровергнутая гипотеза), `fix` (исправление).

## [2026-08-29] milestone | scraper v2 реализован и пропилотирован (ветка scraper-v2)

По handoff-пакету `docs/reports/2026-08-29-handoff-scraper-v2.md` реализован переписанный скрейпер
по дизайну [[конвейер]]: `config.py`, `client.py` (curl_cffi/requests, глобальный token-bucket
html/cdn, circuit breaker, ретраи с Retry-After, resume-кэш всех статусов), `cache.py` (SQLite
`requests(url,status,payload,fetched_at)`), `fetch/` (competitions/clubs/rosters/profiles/
market_values/coaches), `build_canon.py` + `canon_schema.json`, `download_images.py`, `crawl.py`,
`probe.py`.

**Лимиты (probe 2026-08-29, до массового сбора):** HTML через API — чисто на 2.0 rps sustained и
3.0 rps burst, безопасно **1.5 rps**; CDN (img.a.transfermarkt.technology) — чисто на 20 rps,
безопасно **10 rps** (гипотеза «CDN не ограничен как HTML» подтверждена). 429/Retry-After не
наблюдались. Результаты — в [[конвейер]] (раздел «Лимиты»).

**Находки:** API-эндпоинт `market_value` TM 403-ит (голый requests/статичный UA Chrome 113 на
`/marktwertverlauf/` и `ceapi/.../graph/`); добавлен прямой fallback на ceapi через curl_cffi
(Chrome 131) — пробивает. Отдельные страницы игроков TM 403 даже curl_cffi (0 байт, похоже на
honeypot) — пропускаются и кэшируются. `/players/{id}/market_value` = 2 запроса к TM (страница +
график), фактор ×2 при бюджетировании.

**Пилот (PSG + Real Madrid + Франция):** канон `data/canon/` собран и валидирован по схеме
(69 игроков, 2 клуба, 2 лиги, 1 сборная, 1 тренер, 5 историй стоимости); фото скачаны. Повторный
прогон — 0.6 с, ноль сетевых запросов (resume-кэш, идемпотентно). Ветка `scraper-v2` от `main`.

## [2026-08-29] session | см. milestone выше

## [2026-08-29] decision | внешние данные о лимитах и встроенный рейтлимитер API

Обратная связь от тестировавшей: **блоки TM начинаются ~75 rps** (на уровне API «падали запросы»;
тип блокировки не определён — это уже сторона парсера). Наш HTML 1.5 rps — огромный запас до
потолка; потолок CDN из probe (20 rps clean) согласуется с тем, что CDN не является узким местом.

В transfermarkt-api встроен slowapi-рейтлимитер: `RATE_LIMITING_FREQUENCY="2/3seconds"`,
`RATE_LIMITING_ENABLE=false` — локально выключен. При включении API сам режет до ~0.67 rps (429 с
Retry-After) — узким местом станет API, а не TM. Учли в вики и поправили политику кэша: **429/5xx
не кэшируются** (транзиентны), кэшируются только 2xx и 4xx-блоки (403-хонейпоты, 404). Иначе
замороженный в кэше 429 от slowapi на 7 дней молча «терял» бы URL. Добавлен тест
`test_429_is_not_cached`; всего 31 тест, все проходят; пилот по-прежнему идемпотентен.

## [2026-08-29] decision | перенесены тесты и AIMD из transfermarkt_scrapper_upgraded

Разобран `C:\Users\Egor\Desktop\transfermarkt_scrapper_upgraded\upgraded` — это ветка `fix` +
pytest-набор + пример миграции. Полезное для scraper-v2: **pytest-набор** (30 тестов: token-bucket,
AIMD, circuit breaker, resume-кэш, интеграция `client.request()` против mock-HTTP-сервера) и
**`AdaptiveSemaphore` (AIMD)** — у нас был фиксированный семафор. Добавлены в scraper-v2:
`AdaptiveSemaphore` в `client.py` (старт `TM_CONCURRENCY`, рост до ×2, деление пополам при
429/5xx; token-bucket всё равно режет глобальную частоту), `tests/` + `pytest.ini` +
`requirements.txt`. Тесты адаптированы под наш API (кэш ключуется URL, `Client`-класс). Все 30
проходят; пилот по-прежнему идемпотентен (0.8 с, ноль сети). Сам `tm_common.py` из папки —
тот же, что у нас за основой; отличие только в дефолтах AIMD 4..8 (не переносили — наши лимиты
1.5 rps, высокая конкурентность просто встанет в очередь).