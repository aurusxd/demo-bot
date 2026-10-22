# All user-facing bot texts live here.

from collections.abc import Mapping

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

COMMAND_START = "Главное меню"

# --- Main menu ---

BTN_LEAD = "📝 Оставить заявку"
BTN_CATALOG = "🖼 Каталог"
BTN_TRYON = "🏠 Попробовать у себя"

START = (
    "👋 Здравствуйте! Я ИИ-помощник мебельной компании.\n\n"
    "Помогу оформить заявку на кухню, шкаф или гардеробную, сделаю предварительный расчёт "
    "и покажу, как мебель будет смотреться у вас дома.\n\n"
    "Выберите, с чего начнём 👇"
)

MENU_PLACEHOLDER = "Выберите действие"

COMING_SOON = "🛠 Этот раздел скоро заработает. Пока можно посмотреть другие пункты меню."

# --- Qualification ---

QUALIFY_START = (
    "Отлично, давайте оформим заявку 📝\n\n"
    "Что хотите заказать — кухню, шкаф или гардеробную? "
    "Если знаете размеры, напишите их сразу. Можно отвечать голосом — скоро научусь 🙂"
)

QUALIFY_FALLBACK = (
    "Извините, не совсем понял. Уточните, пожалуйста: "
    "что вы хотите заказать и какие примерно размеры?"
)

TEXT_TOO_LONG = "✂️ Сообщение получилось слишком длинным. Сократите, пожалуйста, до 1000 символов."

LEAD_QUALIFIED = (
    "✅ Заявка собрана:\n\n{summary}\n\n"
    "Менеджер свяжется с вами в ближайшее время. "
    "Предварительный расчёт стоимости скоро будет приходить прямо сюда."
)

SLOT_LABELS = {
    "furniture_type": "Мебель",
    "dimensions": "Размеры",
    "style": "Стиль",
    "budget": "Бюджет",
    "city": "Город",
    "deadline": "Сроки",
    "phone": "Телефон",
    "comment": "Комментарий",
}

FURNITURE_LABELS = {
    "kitchen": "кухня",
    "wardrobe": "шкаф",
    "walk_in": "гардеробная",
}


def lead_summary(slots: Mapping[str, str]) -> str:
    lines: list[str] = []
    for name, label in SLOT_LABELS.items():
        value = slots.get(name)
        if value is None:
            continue
        if name == "furniture_type":
            value = FURNITURE_LABELS.get(value, value)
        lines.append(f"• {label}: {value}")
    return "\n".join(lines)


# --- Errors ---

ERROR_GENERIC = "😔 Что-то пошло не так. Попробуйте ещё раз чуть позже."
