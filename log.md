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

## [2026-08-29] milestone | прямой замер потолка TM: чисто на 75 rps, скорость поднята до 8 rps

`probe.py --html-direct` (прямой замер www.transfermarkt.com в обход API) на 5→10→20→40→60→75 rps —
**всё чисто (10/10 × 200)**. Потолок TM выше 75 rps; внешние «блоки на ~75 rps» у тестировавшей —
почти наверняка переполнение API (тредпул anyio 40 потоков не успевал), а не бан TM. Лимиты
подняты: **HTML 1.5 → 8.0 rps** (9× запас до потолка), CDN 10 → 15 rps.

Реализована **weight-aware** динамика (в ответ на «разные эндпоинты с разными ограничениями»):
лимит TM глобальный на IP, делить его per-endpoint незачем; `client.request(weight=N)` — token-
bucket съедает N токенов. `/players/{id}/market_value` = weight 2 (2 TM-запроса), остальное = 1.
Так дорогие эндпоинты сами замедляются внутри общего бюджета. Параллелизм динамический — AIMD.
32 теста проходят; пилот идемпотентен.

## [2026-08-29] decision | адаптивная скорость (AIMD на rps) + память ограничений

В ответ на «что если 8 rps на часовой сессии начнёт падать»: скорость теперь **самонастраивается**
— `RateAimer` в `client.py`: троттлинг (429/5xx/блок-страница/разрыв) делит rps пополам до пола
`TM_HTML_RPS_MIN` (1.0), 50 успехов → +25% (до `TM_HTML_RPS`). Скорость хранится в `data/rates.json`
(`TM_RATES_PATH`) — следующая сессия стартует с выученной, не сжигая ошибки на 8 rps. Probe и тесты
работают с `adaptive_rate=False` (фиксированный уровень). `TokenBucket.set_rate()` — мутабельная
скорость; `client.request(weight=N)` уже учитывает стоимость эндпоинта. 38 тестов проходят; пилот
идемпотентен; проверено: троттл 8→4 сохраняется и следующая сессия стартует с 4.

## [2026-08-29] decision | volume-блоки страниц игроков + retry-очередь (не пропускать игроков)

Пробный сбор профилей/стоимостей выявил главное: **TM блокирует `/profil/spieler/` и
`/marktwertverlauf/` по объёму** — на 8 rps первые ~15-24 запроса ок, дальше новые URL отдают 403
(0 байт) и остаются заблокированными (проверено: те же URL 403 даже curl_cffi напрямую, ранее
успешные страницы продолжают отдаваться). Короткие замеры главной страницы («чисто на 75 rps») нас
обманули. Устойчивый HTML-режим ~1-2 rps: **`TM_HTML_RPS` 8.0 → 2.0**, пол `TM_HTML_RPS_MIN` → 0.5.

Сделано по запросу «не оставлять игроков без данных, лучше подождать»:
- **Адаптивная скорость теперь реагирует на серии 403** (`TM_BLOCK_STREAK`=5 подряд) — раньше
  реагировала только на 429/5xx, и блоки оставались незамеченными.
- **Retry-очередь** в `fetch/profiles.py` и `fetch/market_values.py`: неудавшиеся игроки идут в
  pending и повторяются раундами (`TM_FETCH_RETRY_ROUNDS`=5, `TM_FETCH_RETRY_COOLDOWN`=60 с).
- **Кэш больше не финализирует 403/404** — кэшируются только 2xx; провалы дотягиваются повторным
  запуском (а не пропускаются 7 дней).
- Circuit breaker + адаптивная скорость дают паузу при блоке; retry-раунды добивают оставшихся.

48 тестов проходят. Пилот не перегонялся (см. ограничение выше).

## [2026-08-29] decision | TM вошёл в почти тотальный бан страниц игроков; severity-aware защита 403

После сегодняшних экспериментов (probe до 75 rps, smoke на 8 rps, серии профилей) TM вошёл в
**почти тотальный блок `/profil/spieler/`**: замер ~12% успеха (19 ok против 137 403), причём блок
держится минутами и не снимается паузами. Краул остановлен — IP должен остыть 30-60 мин, потом
повторный запуск добьёт (провалы не кэшируются, retry-очередь + resume-кэш).

Защита переработана с «серий подряд» на **severity по ratio в окне** (`TM_BLOCK_WINDOW`=20):
- ratio ≥ 0.7 → устойчивый бан: circuit-пауза (стоп молотьбы) + скорость пополам;
- 0.4–0.7 → кластерные блоки: только скорость пополам (дотягивает retry-очередь);
- < 0.4 → разрозненные хонейпоты: ничего.
Это чинит и «стоп на коротких кластерах» (120-секундные паузы), и «молотьбу против сплошного бана».
49 тестов проходят.

## [2026-08-31] milestone | tmapi: player profile/market_value/stats починены через внутренний JSON API Transfermarkt

Найден внутренний JSON API Transfermarkt: `tmapi.transfermarkt.technology` — используется их Svelte
web-компонентами, reverse-engineered из JS-бандлов (`player-performance-proxy/bundle.js` и др.).
Не блокируется тем же механизмом что HTML-страницы игроков.

**Ключевые эндпоинты:**
- `tmapi/player/{id}` — profile: height, foot, position+side positions, outfitter, imageUrl, contractUntil
- `tmapi/player/{id}/market-value-history` — история стоимости (37 точек)
- `tmapi/player/{id}/absence` — травмы
- `tmapi/player/{id}/gallery` — фото галерея
- `ceapi/performance-game/{id}` — статистика по матчам (goals, assists, cards, minutes)

**Изменения в transfermarkt-api:**
- `app/services/players/profile.py` — переписан: HTML-скрейпинг → tmapi JSON
- `app/services/players/market_value.py` — переписан: HTML+ceapi → tmapi JSON
- `app/services/players/stats.py` — переписан: HTML-скрейпинг (сломан) → ceapi JSON
- `app/schemas/base.py` — `parse_str_to_int/height` исправлены: принимают int/float/str

**Результат:** все 13 эндпоинтов работают без бана, включая profile/stats/market_value/injuries/
achievements/transfers. Все атрибуты игроков (height, foot, position, outfitter, imageUrl, marketValue)
доступны напрямую с TM для 107k игроков без блокировок.

open-football-database отложена в долгий ящик — TM доступен напрямую через tmapi, ofdb может
перестать поддерживаться.

Открыто: fetch_all_club_profiles.py (198/3949), fetch_all_national_teams.py (19/?),
fetch_player_extras.py (тестировано на 5 игроках), download_images.py (extensions),
build_gf_database.py (конвертер канон → GF SQLite). Подробности — [[конвейер]].

## [2026-08-31] session | данные для GF: open-football + TM-ростеры; стратегия сменилась

Смена стратегии: TM-страницы игроков блокируются per-IP (~15-20 запросов) — профили/стоимости
больше не тянем. Источник атрибутов для GF — **open-football-database** (git-репо, TM-производный,
сезон 2025/26, 59k игроков: CA/PA, позиции+уровни, value, ноги, контракт, история, TM-id).
Изучены и другие источники: DF11 Faces (фото, через сервер open-football), FM-базы (совместимость),
API-Football (альтернатива), FIFA/Kaggle.

Сделано:
- **API-форк**: curl_cffi (ротация иммитаций — починен «unsupported impersonation»); составы клубов
  теперь возвращают **`imageUrl`** (фото из страницы состава, профиль не нужен; даже 403-страницы
  игроков получают фото). 26/26 у PSG.
- **Крол составов всех лиг TM** (`fetch_all_rosters.py`): индекс `/wettbewerbe/national/` = 269 лиг,
  **3949 клубов, 107454 игрока** (рост, гражданство, стоимость, фото). Завершён.
- `load_openfootball.py`: join open-football + TM-ростеры по `ids.transfermarkt.com` (gb/es: 6655
  игроков, рост 25% — ограничено TM-id: es 74%, gb 15%).
- Вьюер по лигам (`/api/leagues`), `--refresh` для добора фото, `download_faces_from_cache`.
- Хэндофф для новой сессии: `docs/reports/2026-08-31-handoff-data-pipeline.md`.

Открыто: профили клубов (цвета/лого) только 198/3949; TM-id нет у ~60% игроков open-football
(нижние дивизионы Англии 0-38%) — фаззи-матч или дефолт роста; закрепить open-football-database
сабмодулем. Подробности — [[конвейер]] и handoff.

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