# All user-facing bot texts live here.

# --- Bot profile (shown before /start). Limits: description <= 512, short <= 120. ---

BOT_DESCRIPTION = (
    "🤖 ИИ-квалификатор для мебельных компаний\n\n"
    "Принимает заявки на кухни, шкафы и гардеробные, понимает фото и голосовые, "
    "задаёт вопросы как менеджер, делает предварительный расчёт и передаёт тёплый лид в CRM.\n\n"
    "📲 Работает в каналах: Авито, ВК, Telegram, WhatsApp, MAX, сайт.\n\n"
    "🤑 Окупается механикой: меньше потерянных заявок, быстрее первый ответ, "
    "больше доведённых до замера клиентов и меньше рутины у менеджеров.\n"
    "🔗 Подробнее: https://isc-g.ru/ai/mebel/"
)

BOT_SHORT_DESCRIPTION = (
    "ИИ-квалификатор заявок на кухни, шкафы и гардеробные: вопросы, расчёт и тёплый лид в CRM."
)

COMMAND_START = "Начать диалог заново"
COMMAND_DELETE_ME = "Удалить мои данные"

# --- Dialog (no menu: the bot talks like a live manager) ---

GREETING = (
    "👋 Здравствуйте! Это демо ИИ-продавца для мебельных компаний.\n\n"
    "Что я умею:\n"
    "• общаюсь с клиентом как живой менеджер и выясняю, что ему нужно;\n"
    "• понимаю голосовые и принимаю фото;\n"
    "• делаю предварительный расчёт стоимости;\n"
    "• показываю каталог работ и примеряю мебель на фото комнаты клиента;\n"
    "• передаю готовую заявку менеджеру и в CRM.\n\n"
    "🔌 Здесь я работаю в Telegram, но меня можно подключить к любому мессенджеру "
    "или сайту: WhatsApp, ВК, Авито, MAX, виджет на сайте.\n\n"
    "🧪 Чтобы проверить, как это работает, представьте, что вам нужна кухня, шкаф "
    "или гардеробная, и напишите свой запрос."
)

QUALIFY_FALLBACK = "Извините, не совсем понял вас 🙏 Можете сформулировать чуть иначе?"

TEXT_TOO_LONG = "✂️ Сообщение получилось слишком длинным. Сократите, пожалуйста, до 1000 символов."

DELETE_ME_DONE = (
    "🗑 Готово: ваши данные и заявки удалены из бота. Чтобы начать заново — нажмите /start."
)

# --- Voice and photo ---

VOICE_TOO_LONG = "🎙 Голосовое получилось длинным. Запишите, пожалуйста, покороче — до минуты."

VOICE_NOT_RECOGNIZED = (
    "🎙 Не удалось разобрать голосовое. Повторите, пожалуйста, или напишите текстом."
)

VOICE_FAILED = "😔 Не получилось обработать голосовое. Попробуйте ещё раз или напишите текстом."

PHOTO_RECEIVED = "📎 Фото получил и приложил к заявке — менеджер его увидит."

PHOTO_INVALID = (
    "🖼 Не получилось открыть это фото. Пришлите, пожалуйста, другое — "
    "в формате JPG, PNG или WEBP, до 10 МБ."
)

PHOTO_FAILED = "😔 Не получилось загрузить фото. Попробуйте отправить ещё раз."

SEND_AS_PHOTO = (
    "🖼 Пришлите, пожалуйста, картинку как фото, а не файлом — так я приложу её к заявке."
)

# --- Estimate for the client (sent after the LLM's thank-you on qualification) ---

ESTIMATE_RANGE = (
    "💰 Предварительный расчёт: {min_price} – {max_price}.\n"
    "Это ориентир: точную стоимость назовёт менеджер после замера."
)

ESTIMATE_AFTER_MEASURE = (
    "📏 Точную стоимость менеджер посчитает после замера — он скоро с вами свяжется."
)


# --- Catalog cards (caption is HTML; values are escaped by the handler) ---

CATALOG_CAPTION = "<b>{title}</b>"
CATALOG_CAPTION_STYLE = "\n🎨 {style}"
CATALOG_PREV = "◀"
CATALOG_NEXT = "▶"
CATALOG_COUNTER = "{index} / {total}"
CATALOG_CURRENT_CATEGORY = "· {label} ·"
CATALOG_CATEGORY_LABELS = {
    "kitchen": "🍳 Кухни",
    "wardrobe": "🚪 Шкафы",
    "walk_in": "👔 Гардеробные",
    "interior": "🛋 Интерьеры",
}
CATALOG_FAILED = "😔 Не получилось загрузить примеры. Попросите показать их ещё раз чуть позже."
CATALOG_EMPTY = "🖼 Примеров в этой категории пока нет — менеджер покажет их на встрече."

# --- Try-on: a catalog item in the client's room (HTML; values are escaped by the handler) ---

TRYON_BUTTON = "🏠 Попробовать у себя"
TRYON_MORE_BUTTON = "🔄 Другая мебель"
TRYON_LEAD_BUTTON = "📝 Оставить заявку"
TRYON_ASK_PHOTO = (
    "📸 Пришлите фото вашей комнаты или стены, куда хотите <b>{title}</b>.\n"
    "Лучше снимать днём, целиком, с уровня глаз — так результат будет точнее."
)
TRYON_GENERATING = "⏳ Генерирую — это займёт до минуты…"
TRYON_RESULT_CAPTION = (
    "✨ <b>{title}</b> у вас в интерьере\n"
    "Это визуализация ИИ: точный проект и размеры подготовит дизайнер после замера."
)
TRYON_FAILED = (
    "😔 Не получилось сгенерировать примерку. Пришлите фото ещё раз или попробуйте чуть позже."
)
TRYON_LIMIT = (
    "🙏 На сегодня примерки закончились — это демо, генераций {limit} в день. Возвращайтесь завтра!"
)
TRYON_ITEM_GONE = "Этой позиции больше нет в каталоге 🙏 Выберите, пожалуйста, другую."
# Sent into the dialog as the client's message when they press «Оставить заявку».
TRYON_LEAD_REQUEST = "Хочу оставить заявку. На примерке у себя понравился вариант «{title}»."


def format_price(value: int, currency: str) -> str:
    """115000 -> '115 000 ₽' (narrow no-break space keeps the number on one line)."""
    return f"{value:,}".replace(",", " ") + f" {currency}"


# --- Lead card for managers (HTML; every value is escaped by the renderer) ---

LEAD_CARD_TITLE = "🔥 <b>Новая заявка #{lead_id}</b>"
LEAD_CARD_ESTIMATE = "💰 <b>Предварительно:</b> {min_price} – {max_price}"
LEAD_CARD_NO_ESTIMATE = "💰 <b>Расчёт:</b> после замера"
LEAD_CARD_CONTACT = "✈️ <b>Telegram:</b> {contact}"
LEAD_CARD_PHOTOS = "📎 <b>Фото:</b> {count} (ниже)"
LEAD_CARD_FIELD = "<b>{label}:</b> {value}"
LEAD_UPDATE_TITLE = "✏️ <b>Дополнение к заявке #{lead_id}</b>"
LEAD_UPDATE_PHOTO = "📎 <b>Дополнение к заявке #{lead_id}:</b> фото"
LEAD_CARD_B2B_TITLE = "\n🏢 <i>О посетителе демо</i>"

# Order and labels of the card fields.
LEAD_CARD_FIELDS = (
    ("name", "Имя"),
    ("phone", "Телефон"),
    ("furniture_type", "Что нужно"),
    ("dimensions", "Размеры"),
    ("length_m", "Погонных метров"),
    ("price_class", "Класс материалов"),
    ("style", "Стиль"),
    ("budget", "Бюджет"),
    ("city", "Город"),
    ("deadline", "Сроки"),
    ("comment", "Комментарий"),
)
LEAD_CARD_B2B_FIELDS = (
    ("business_role", "Роль в бизнесе"),
    ("lead_sources", "Откуда заявки"),
)

VALUE_LABELS = {
    "kitchen": "Кухня",
    "wardrobe": "Шкаф",
    "walk_in": "Гардеробная",
    "economy": "Эконом",
    "standard": "Стандарт",
    "premium": "Премиум",
}

THROTTLED = "Вы пишете очень быстро 🙂 Дайте мне минутку — и продолжим."

# --- Errors ---

ERROR_GENERIC = "😔 Что-то пошло не так. Попробуйте ещё раз чуть позже."
