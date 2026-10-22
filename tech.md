# tech.md — ИИ-квалификатор для мебельных компаний (демо-бот)

> Telegram-бот-демо для показа клиентам. Принимает заявки на кухни, шкафы, гардеробные, понимает текст/фото/голос, делает предварительный расчёт, отдаёт «тёплый» лид в CRM и умеет «примерить» мебель/интерьер на фото клиента.
> Принцип: **демо, а не прод**. Простота важнее полноты. Всё, чего нет в этом файле, не делаем.

---

## 1. Цели и границы

**Цель:** за 2–3 минуты показать клиенту (мебельной компании) полный путь: заявка → квалификация → расчёт → лид в CRM + вау-фича «примерить у себя».

**Входит:**
- Диалоговая квалификация заявки (DeepSeek).
- Приём текста и голосовых; фото клиента принимаются и прикладываются к лиду **без анализа** (vision нет).
- Предварительный расчёт (детерминированный, в коде).
- Передача лида: карточка менеджеру в Telegram + CRM-адаптер (в демо — заглушка/webhook).
- Каталог интерьера/мебели (фото) + «Попробовать у себя» (Nano Banana через Polza AI).
- Шапка бота (description + картинка) как на референсе.

**Не входит (сознательно):** мульти-тенантность, админ-панель, оплата, WhatsApp/ВК/Авито/MAX-каналы (только заявлено в шапке, реализуем один Telegram), реальные интеграции с конкретной CRM, горизонтальное масштабирование.

---

## 2. Стек

| Что | Чем |
|---|---|
| Язык | Python 3.12 |
| Бот | aiogram 3.x (polling, webhook — не нужен) |
| Чат-LLM | DeepSeek API (OpenAI-совместимый, `deepseek-chat`) |
| Картинки | Polza AI API, модель Nano Banana (id модели — в `.env`) |
| Голос (STT) | через Polza AI (модель в `.env`), см. §9 |
| БД | SQLite + SQLAlchemy 2.x (async, `aiosqlite`) + Alembic |
| Конфиг | pydantic-settings |
| Логи | loguru |
| Тесты | pytest, pytest-asyncio, respx (мок HTTP) |
| Типы | pyright (strict для `app/`) |
| Линтер/формат | ruff |
| Зависимости | uv + `uv.lock` |
| Запуск | Docker + docker compose |
| Команды | Makefile (на Windows — GNU make через winget `ezwinports.make`) |
| HTTP-клиент | httpx (async) |
| Картинки (локально) | Pillow |

---

## 3. Архитектура

Слои, зависимость только сверху вниз:

```
handlers (aiogram)  →  services  →  repositories  →  models (SQLAlchemy)
                          ↓
                       clients (DeepSeek, Polza, CRM)  — за Protocol-интерфейсами
```

- **handlers** — только разбор апдейта, вызов сервиса, ответ. Никакой бизнес-логики.
- **services** — бизнес-логика (квалификация, расчёт, каталог, примерка, медиа).
- **repositories** — единственное место с SQL/SQLAlchemy-запросами.
- **clients** — внешние API. Каждый реализует `Protocol` (`LLMClient`, `ImageGenClient`, `SpeechClient`, `CrmClient`), в сервисы приходят через DI → легко мокать в тестах.
- **DI**: ручной (контейнер в `main.py`), зависимости прокидываются в aiogram через `dp.workflow_data` / middleware. Без DI-фреймворков.

### Структура репозитория

```
.
├── app/
│   ├── main.py                  # сборка контейнера, запуск polling
│   ├── config.py                # Settings (pydantic-settings)
│   ├── logging_setup.py         # loguru: sinks, маскирование секретов
│   ├── bot/
│   │   ├── handlers/            # start.py, qualify.py, catalog.py, tryon.py, media.py
│   │   ├── middlewares/         # logging.py, throttle.py, db_session.py, user.py
│   │   ├── keyboards.py
│   │   ├── states.py            # aiogram FSM states
│   │   └── texts.py             # ВСЕ тексты бота (шапка, сообщения)
│   ├── services/
│   │   ├── qualification.py     # диалог + слоты
│   │   ├── estimate.py          # расчёт
│   │   ├── leads.py             # сборка и отправка лида
│   │   ├── catalog.py
│   │   ├── tryon.py             # примерка
│   │   ├── media.py             # скачивание, валидация, ресайз
│   │   └── users.py             # регистрация пользователя (upsert по tg_id)
│   ├── clients/
│   │   ├── base.py              # Protocol'ы + базовый HttpClient (ретраи, таймауты, логи)
│   │   ├── deepseek.py
│   │   ├── polza.py             # image-gen, speech-to-text
│   │   └── crm.py               # LogCrmClient (демо), WebhookCrmClient
│   ├── db/
│   │   ├── base.py              # Base, engine, session factory
│   │   ├── models.py
│   │   └── repositories.py
│   ├── schemas/                 # pydantic: LeadSlots, LLMReply, EstimateResult
│   └── core/
│       └── errors.py            # доменные исключения
├── assets/catalog/              # фото + catalog.yaml (seed)
├── assets/header/               # картинка шапки (грузится вручную через @BotFather)
├── scripts/set_bot_profile.py   # description / short description / команды через Bot API
├── config/pricing.yaml          # прайс для расчёта
├── migrations/                  # alembic
├── tests/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── Makefile
├── tech.md
└── CLAUDE.md
```

---

## 4. Модель данных (SQLite)

Минимум таблиц.

- `users` — `id`, `tg_id` (unique), `username`, `created_at`, `tryon_used_today`, `tryon_reset_date`
- `leads` — `id`, `user_id`, `status` (`draft|qualified|sent`), `furniture_type` (`kitchen|wardrobe|walk_in`), `dimensions`, `style`, `budget`, `city`, `deadline`, `phone`, `comment`, `estimate_min`, `estimate_max`, `created_at`, `sent_at`
- `messages` — `id`, `lead_id`, `role` (`user|assistant`), `content`, `kind` (`text|voice|photo`), `created_at` — история для LLM (хранить последние N)
- `lead_media` — `id`, `lead_id`, `tg_file_id`, `kind`
- `catalog_items` — `id`, `category` (`kitchen|wardrobe|walk_in|interior`), `title`, `style`, `image_path`, `tg_file_id` (кэш после первой отправки), `source_url`
- `tryon_requests` — `id`, `user_id`, `catalog_item_id`, `status` (`pending|done|failed`), `latency_ms`, `error`, `created_at`

Результаты генерации и фото клиентов **не храним на диске дольше обработки** (удаляем temp-файлы; в БД только `tg_file_id`).

---

## 5. Ключевые решения по фичам

### 5.1 Шапка бота (то, что видно до `/start`)
- Текст — Bot API `setMyDescription` (**≤ 512 символов**), кнопка «What can this bot do?» показывает его. Короткое — `setMyShortDescription` (≤ 120).
- Картинка «Менеджер или бот?» — загружается вручную через @BotFather (`/setdescriptionpic`), в API не настраивается. Файл лежит в `assets/header/`.
- Текст шапки живёт в `texts.py`; скрипт `scripts/set_bot_profile.py` (`make profile`) выставляет description, short description и список команд через API. Тест проверяет лимиты 512/120 (в UTF-16, как считает Telegram).
- Пользователь регистрируется `UserMiddleware` на каждом апдейте (атомарный upsert по `tg_id`), в хендлеры приходит как `user`.

Текст (по референсу):

```
🤖 ИИ-квалификатор для мебельных компаний

Принимает заявки на кухни, шкафы и гардеробные, понимает фото и голосовые, задаёт вопросы как менеджер, делает предварительный расчёт и передаёт тёплый лид в CRM.

📲 Работает в каналах: Авито, ВК, Telegram, WhatsApp, MAX, сайт.

🤑 Окупается механикой: меньше потерянных заявок, быстрее первый ответ, больше доведённых до замера клиентов и меньше рутины у менеджеров.
🔗 Подробнее: https://isc-g.ru/ai/mebel/
```

### 5.2 Квалификация (DeepSeek)
- Бот ведёт диалог как менеджер и собирает **слоты**: тип мебели, размеры/план, стиль, бюджет, город, сроки, телефон.
- Каждый ход: в DeepSeek уходит system-prompt + история (последние ~12 сообщений) + текущие слоты. Ответ — **строго JSON** (`response_format: json_object`), валидируется pydantic-схемой `LLMReply {reply: str, slots: LeadSlotsPatch, ready: bool}`.
- При невалидном JSON — 1 ретрай с подсказкой, затем fallback-ответ «уточните, пожалуйста…». Бот не падает.
- Слоты мержатся в `leads` кодом, **LLM не пишет в БД напрямую**.
- `ready = true`, когда заполнены обязательные слоты (тип, размеры, телефон) → переход к расчёту.
- Текст пользователя — это данные, а не инструкции: в system-prompt явный запрет менять роль/формат; ответ LLM не исполняется, только парсится.

### 5.3 Расчёт
- **Детерминированный, без LLM.** `config/pricing.yaml`: базовая цена за погонный метр по типу мебели × коэффициент класса материалов/стиля.
- Результат — вилка ±15 % + дисклеймер «предварительно, точную цену назовёт менеджер».
- Если размеров недостаточно — расчёт не делаем, спрашиваем.

### 5.4 Фото и голос
- **Голос:** скачать `.ogg` → транскрипция через Polza (STT-модель из `.env`) → текст идёт в тот же пайплайн квалификации.
- **Фото:** без анализа. Валидация (размер, тип, Pillow open), strip EXIF; в квалификации фото просто сохраняется как `tg_file_id` в `lead_media` и пересылается менеджеру вместе с карточкой лида. Для примерки (§5.7) фото дополнительно ресайзится ≤ 1536 px.

### 5.5 Передача лида
- Сервис `LeadService.submit(lead)` → `CrmClient.send(lead)` + карточка в чат менеджера (`ADMIN_CHAT_ID`) с слотами, вилкой цены и фото.
- Демо: `LogCrmClient` (пишет в лог) и `WebhookCrmClient` (POST на `CRM_WEBHOOK_URL`, если задан). Реальные CRM клиент подключит позже — через тот же `Protocol`.

### 5.6 Каталог
- Источник: `assets/catalog/catalog.yaml` + файлы фото. Сидится в БД при старте (идемпотентно).
- Фото берём из интернета **вручную** и кладём в репозиторий (не парсим на лету). В `source_url` пишем источник. ⚠️ Это демо — для боевого использования заменить на фото клиента/лицензионные.
- UX: категории → листание карточек (inline-кнопки «◀ ▶») → «🏠 Попробовать у себя». `tg_file_id` кэшируем после первой отправки.
- Объём для демо: ~5–8 позиций на категорию.

### 5.7 «Попробовать у себя» (главная вау-фича)
1. Пользователь выбирает позицию каталога → жмёт «Попробовать у себя».
2. Бот просит фото комнаты/стены (FSM-состояние `TryOn.waiting_photo`).
3. Фото валидируется и ресайзится.
4. `TryOnService` вызывает `ImageGenClient.edit(room_photo, reference_photo, prompt)` — Nano Banana через Polza AI (фото комнаты + референс из каталога + промпт «вставь эту мебель в этот интерьер, сохрани перспективу и освещение»).
5. Пока идёт генерация — сообщение «⏳ Генерирую…» + chat action `upload_photo`. Результат отправляется фото; кнопки: «Другая мебель», «Оставить заявку».
6. Таймаут 90 с, 1 ретрай, понятное сообщение об ошибке. Статус и latency пишем в `tryon_requests`.
7. **Лимит**: N генераций на пользователя в сутки (`TRYON_DAILY_LIMIT`, по умолчанию 5) — защита бюджета на демо.

---

## 6. Логирование (loguru) — обязательно

- Единая настройка в `logging_setup.py`: stdout (человекочитаемо) + файл `logs/app.log` (JSON, `serialize=True`, rotation 10 MB, retention 14 дней, compression).
- Перехват стандартного `logging` (aiogram, httpx, sqlalchemy) в loguru.
- **Контекст**: `logger.contextualize(update_id=..., tg_id=..., lead_id=...)` в middleware на каждый апдейт — каждая строка лога привязана к пользователю/апдейту.
- **Что логируем:**
  - входящий апдейт (тип, user, длина текста — не весь текст), хендлер, время обработки;
  - каждый вызов внешнего API: сервис, метод, статус, latency, номер попытки, размер payload (не содержимое);
  - переходы FSM и статусов лида;
  - лимиты/ошибки валидации/ретраи;
  - необработанные исключения — `logger.exception` + глобальный error-handler aiogram.
- **Чего не логируем:** токены/ключи (фильтр-маскирование в sink), телефоны (маска `+7***1234`), содержимое фото, полные тексты сообщений на уровне INFO (только DEBUG и только локально).
- Уровень — `LOG_LEVEL` из `.env`.

---

## 7. Безопасность (минимально достаточная для публичного демо)

- Секреты только в `.env` / env-переменных, `.env` в `.gitignore`, `.env.example` в репозитории. Токены не попадают в логи.
- Все настройки — через `pydantic-settings`, при старте валидация (бот не стартует без обязательных).
- Входные данные: лимит размера файла (≤ 10 МБ), whitelist MIME (`jpeg/png/webp`), проверка через Pillow, голосовые ≤ 60 с.
- Throttling-middleware: не более X сообщений/сек на пользователя.
- Лимит дневных генераций (см. 5.7).
- Prompt-injection: см. 5.2 — вывод LLM только парсится и валидируется схемой.
- SQL — только через SQLAlchemy (параметризация), никакого сырого SQL со строками пользователя.
- Docker: непривилегированный пользователь, `read_only` rootfs где возможно, volume только для `data/` и `logs/`.
- ПДн (телефон): храним минимум, команда `/delete_me` стирает пользователя и его лиды.
- Админ-функции (если появятся) — только для `ADMIN_IDS`.

---

## 8. Конфиг (`.env.example`)

```
BOT_TOKEN=
ADMIN_CHAT_ID=
ADMIN_IDS=

DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

POLZA_API_KEY=
POLZA_BASE_URL=
POLZA_IMAGE_MODEL=        # Nano Banana, точный id взять в доке Polza
POLZA_STT_MODEL=

CRM_WEBHOOK_URL=          # пусто → LogCrmClient
DATABASE_URL=sqlite+aiosqlite:///data/app.db
TRYON_DAILY_LIMIT=5
LOG_LEVEL=INFO
```

---

## 9. Открытые вопросы / риски

1. **DeepSeek не умеет аудио** → STT идём через Polza (модель в `.env`). Проверить в доке Polza доступные модели и формат запроса.
2. **Точные эндпоинт и формат Polza для Nano Banana** (edit с несколькими входными картинками, base64 vs URL) — уточнить в доке на Стадии 0 и зафиксировать в `clients/polza.py`. Клиент прячется за `ImageGenClient`, так что смена формата не затронет остальной код.
3. **Качество примерки** зависит от промпта и референса — заложить время на итерации промпта (в `texts.py`/`prompts/`, не в коде).
4. **Авторские права на фото каталога** — для демо ок, для клиентского показа заменить.
5. SQLite + async: один процесс бота, этого достаточно; при росте — смена `DATABASE_URL` на Postgres без переписывания (SQLAlchemy).

---

## 10. План работ

### Стадия 0 — Фундамент (без фич)
- [x] Репозиторий, `pyproject.toml`, структура папок, `.gitignore`, `.env.example`
- [x] `config.py` (pydantic-settings) + валидация при старте
- [x] `logging_setup.py` (loguru, маскирование, перехват logging) + logging-middleware
- [x] БД: `db/base.py`, модели, первая миграция Alembic, session middleware
- [x] `clients/base.py`: Protocol'ы + `HttpClient` (таймауты, ретраи, логи вызовов)
- [x] Docker / docker-compose (бот + volume для `data/`, `logs/`)
- [x] pytest + pyright (strict) + Makefile (`make run|test|lint|migrate`)
- [ ] «Hello bot»: поднимается в контейнере, отвечает на `/start` заглушкой, пишет логи
- **DoD:** `docker compose up` → бот отвечает, лог-файл пишется, `make test` и `pyright` зелёные.

### Слайсы фич (каждый — вертикальный срез: handler → service → repo → тесты → логи)

| # | Слайс | DoD |
|---|---|---|
| 1 | **Шапка + `/start` + меню** | description/short description выставлены скриптом, картинка загружена, меню: «Оставить заявку», «Каталог», «Попробовать у себя» |
| 2 | **Квалификация (текст)** | диалог с DeepSeek, слоты в БД, JSON-валидация, fallback; тесты на парсинг/мерж слотов |
| 3 | **Голос и фото во входе** | голос → текст через Polza, фото валидируется и прикладывается к лиду (без анализа) |
| 4 | **Расчёт + передача лида** | `EstimateService` по `pricing.yaml`, карточка менеджеру, `CrmClient` (Log + Webhook) |
| 5 | **Каталог** | seed из yaml, категории, листание, кэш `file_id` |
| 6 | **Примерка у себя** | FSM, Nano Banana через Polza, лимит, таймаут/ретрай, статусы в БД |
| 7 | **Полировка демо** | дневные лимиты, `/delete_me`, тексты, сценарий демо для клиента, README-инструкция запуска |

Порядок можно менять: если «вау» важнее — слайс 6 можно сделать сразу после 1, пропустив 5 (каталог из 3–5 позиций хардкодом в yaml).

---

## 11. Тестирование

- **Unit:** расчёт (`estimate`), мерж слотов, парсинг/валидация LLM-ответа, маскирование логов, длина шапки.
- **Сервисы с моками клиентов:** `FakeLLMClient`, `FakeImageGenClient` (реализуют те же `Protocol`).
- **Клиенты:** `respx` — успех, 4xx/5xx, таймаут, ретраи.
- **Репозитории:** in-memory SQLite.
- Хендлеры — тонкие, покрываем 1–2 smoke-тестами на ключевые сценарии. E2E руками в Telegram.
