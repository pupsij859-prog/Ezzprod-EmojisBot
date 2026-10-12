"""Тексты бота. Стиль как у референса: коротко, дружелюбно, со строчной буквы."""
from __future__ import annotations

from ..ru import gens

BTN_OPEN = "открыть редактор"
BTN_OPEN_PACK = "открыть пак"
MENU_BUTTON = "открыть"


def start_no_webapp(brand: str) -> str:
    return (
        f"привет! это {brand}.\n\n"
        "редактор пока недоступен: не настроен адрес Mini App (WEBAPP_URL). "
        "как только он появится, здесь будет кнопка «открыть редактор»."
    )


def help_text(brand: str) -> str:
    return (
        f"{brand}: собираем свои эмодзи и стикеры за пару минут.\n\n"
        "1. выбери шаблоны\n"
        "2. добавь свой текст, логотип и цвета\n"
        "3. создай пак и добавь его в telegram\n\n"
        "всё происходит в редакторе: жми кнопку ниже. инструкция с картинками есть в профиле."
    )


def short_description(brand: str) -> str:
    return f"{brand}: свои эмодзи и стикеры с ником и логотипом."


def long_description(brand: str) -> str:
    return (
        f"{brand} собирает персональные паки эмодзи и стикеров: выбери шаблон, "
        "добавь ник или логотип, подбери цвета, и пак готов. первая генерация в подарок."
    )


def pack_ready(title: str, count: int, demo: bool) -> str:
    text = f"пак «{title}» готов ✨\nвнутри: {count}"
    if demo:
        text += "\n\nэто демо-сборка: настоящие наборы подключим на следующем этапе."
    return text


def pack_failed() -> str:
    return "не получилось собрать пак. генерации вернули на баланс, попробуй ещё раз."


def referral_reward(amount: int) -> str:
    return f"друг собрал первый пак: тебе +{gens(amount)} 🎁"


def payment_ok(qty: int, balance: int) -> str:
    return f"оплата прошла ✅\n+{gens(qty)}, баланс: {balance}"


PAYMENT_STALE = "платёж устарел, открой покупку в редакторе заново."
PAYMENT_PROBLEM = (
    "оплата прошла, но мы не смогли её сопоставить. напиши в поддержку и приложи этот чат: "
    "мы всё вернём или начислим вручную."
)
FALLBACK = "жми кнопку ниже, чтобы открыть редактор."


def terms(url: str | None) -> str:
    base = ("условия покупки: генерации не сгорают, 1 генерация расходуется на 1 эмодзи или стикер. "
            "если пак не удалось собрать, генерации вернутся на баланс.")
    return f"{base}\n\n{url}" if url else base


def support(username: str | None) -> str:
    return f"поддержка: @{username}" if username else "поддержка скоро появится."


def paysupport(username: str | None) -> str:
    who = f"@{username}" if username else "в поддержку"
    return (
        f"если оплата прошла, а генерации не пришли, или нужен возврат: напиши {who} и приложи скрин оплаты. "
        "неиспользованные генерации вернём."
    )


REFUND_USAGE = "использование: /refund <charge_id>"
REFUND_ERRORS = {
    "unknown": "платёж с таким charge_id не найден",
    "bad_status": "этот платёж нельзя вернуть: он не в статусе «зачислен»",
    "spent": "генерации по этому платежу уже потрачены: автоматический возврат невозможен, решай вручную",
}
REFUND_DONE = "возврат выполнен: Stars вернулись пользователю, генерации сняты с баланса"
REFUND_FAILED = "Telegram не вернул Stars, генерации возвращены пользователю на баланс. подробности в логе"


def refund_user_notice(qty: int) -> str:
    return f"возврат оплаты выполнен: Stars вернулись на твой счёт, {gens(qty)} сняты с баланса."
