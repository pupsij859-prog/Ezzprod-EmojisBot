"""Тексты бота. Стиль как у референса: коротко, дружелюбно, со строчной буквы."""
from __future__ import annotations

from ..ru import gens

BTN_OPEN = "открыть редактор"
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


BTN_VIEW_RESULT = "посмотреть результат"
BTN_SEND_FRIEND = "отправить другу"


def pack_ready(name: str, demo: bool, failed: int = 0) -> str:
    text = f"🥳 пак {name} создан!"
    if failed:
        text += f"\n\nне получилось собрать {failed}: генерации за них вернули на баланс."
    if demo:
        text += "\n\nэто демо-сборка: настоящий набор в Telegram не создавался."
    return text


def share_text(kind: str) -> str:
    return "смотри, какие эмодзи я собрал" if kind == "emoji" else "смотри, какие стикеры я собрал"


def pack_failed() -> str:
    return "не получилось собрать пак. генерации вернули на баланс, попробуй ещё раз."


def referral_reward(amount: int) -> str:
    return f"друг собрал первый пак и купил генерации: тебе +{gens(amount)} 🎁"


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
REFUND_UNKNOWN = (
    "Telegram не ответил, и проверить исход не удалось. платёж оставлен в статусе «возврат», "
    "генерации сняты. проверь журнал Stars и при необходимости повтори /refund"
)


def refund_user_notice(qty: int) -> str:
    return f"возврат оплаты выполнен: Stars вернулись на твой счёт, {gens(qty)} сняты с баланса."


MAINTENANCE = "🔧 сейчас идут технические работы. загляни чуть позже — всё сохранится."
PROMO_USAGE = "введи промокод так: /promo КОД"
PROMO_ERRORS = {
    "unknown": "такого промокода нет. проверь, как он написан.",
    "used": "этот промокод ты уже активировал.",
    "exhausted": "у этого промокода закончились активации.",
}


def promo_ok(gens: int, balance: int) -> str:
    return f"🎟 промокод активирован: +{gens} генераций. на балансе {balance}."
