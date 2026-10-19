"""Тексты бота. Стиль как у референса: коротко, дружелюбно, со строчной буквы."""
from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from aiogram.types import CopyTextButton, InlineKeyboardButton, WebAppInfo

from ..emoji import IDS as EMOJI_IDS, KEYS as EMOJI_KEYS
from ..ru import gens


def tg_emoji(key: str) -> str:
    """Премиум-эмодзи по ключу из app/emoji.py (без Premium у владельца бота Telegram покажет обычный эмодзи)."""
    return f'<tg-emoji emoji-id="{EMOJI_IDS[key]}">{EMOJI_KEYS[key]}</tg-emoji>'


SUPPORT_DEFAULT = "EzzSup"  # поддержка в приветствии, если SUPPORT_USERNAME не задан

BTN_OPEN = "открыть редактор"
BTN_OPEN_STYLE = "success"  # цвет кнопки: success — зелёный, primary — синий, danger — красный
MENU_BUTTON = "открыть"


@dataclass(frozen=True)
class MessageDef:
    key: str
    title: str
    default: str
    placeholders: tuple[str, ...]
    hint: str = ""


class SafeDict(dict):
    """Словарь для format_map: если переменная не передана, оставляет её как есть ({var})."""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


MSG_DEFS: dict[str, MessageDef] = {
    "subscription": MessageDef(
        "subscription", "🔒 Сообщение об обязательной подписке",
        "чтобы пользоваться ботом, подпишись на наш канал и нажми «Проверить подписку».",
        ("{channel}",), "Сообщение перед входом в бот; поддерживаются HTML и премиум-эмодзи",
    ),
    "subscription_join": MessageDef(
        "subscription_join", "📢 Кнопка перехода в канал", "Подписаться на канал", (), "Название первой кнопки (до 64 символов)",
    ),
    "subscription_check": MessageDef(
        "subscription_check", "✅ Кнопка проверки подписки", "Проверить подписку", (), "Название второй кнопки (до 64 символов)",
    ),
    "subscription_missing": MessageDef(
        "subscription_missing", "🔎 Подписка не найдена",
        "подписка пока не найдена. подпишись на канал и нажми проверку ещё раз.", (), "Ответ при отсутствии подписки (до 200 символов)",
    ),
    "subscription_ok": MessageDef(
        "subscription_ok", "✅ Подписка подтверждена",
        "подписка подтверждена ✅ теперь можно пользоваться ботом!", (), "Сообщение с кнопкой открытия редактора",
    ),
    "subscription_error": MessageDef(
        "subscription_error", "⚠️ Ошибка проверки подписки",
        "не получилось проверить подписку. попробуй чуть позже или напиши /support.", (), "Ответ при ошибке Telegram (до 200 символов)",
    ),
    "start": MessageDef(
        key="start",
        title="👋 Приветствие (/start, /help)",
        default=(
            "{brand}: собираем свои эмодзи и стикеры за пару минут.\n\n"
            "1. выбери шаблоны\n"
            "2. добавь свой текст, логотип и цвета\n"
            "3. создай пак и добавь его в telegram\n\n"
            "всё происходит в редакторе: жми кнопку ниже. инструкция с картинками есть в профиле."
        ),
        placeholders=("{brand}",),
        hint="Ответ на команды /start и /help",
    ),
    "welcome": MessageDef(
        key="welcome",
        title="🚀 Приветствие с баннером (/start)",
        default=(
            f"{tg_emoji('chat')} Привет, <b>{{name}}</b>!\n\n"
            f"{tg_emoji('sparkle')} <b>EzzSticker</b> — это телеграм бот, где вы можете создавать свои эмодзи "
            "и стикеры прямо в Telegram\n\n"
            f"{tg_emoji('fire')} Более 300+ уникальных шаблонов\n"
            f"{tg_emoji('gift')} Готовые наборы в один тап\n"
            f"{tg_emoji('gem')} Свой ник, логотип и градиенты\n"
            f"{tg_emoji('rocket')} Пак за пару минут\n\n"
            f"{tg_emoji('star')} <b>Создай свой первый пак</b>"
        ),
        placeholders=("{name}", "{brand}"),
        hint="Подпись к баннеру на /start; поддерживаются HTML и премиум-эмодзи (до 1024 символов)",
    ),
    "pack_ready": MessageDef(
        key="pack_ready",
        title="🥳 Пак создан",
        default=(
            "🥳 <b>пак «{name}» готов!</b>\n\n"
            "<blockquote>жми «добавить в Telegram» — пак сразу появится у тебя в эмодзи и стикерах.</blockquote>"
            "{details}"
        ),
        placeholders=("{name}", "{failed}", "{details}"),
        hint="Отправляется пользователю, когда пак успешно собран",
    ),
    "pack_failed": MessageDef(
        key="pack_failed",
        title="❌ Ошибка сборки пака",
        default=(
            "😔 <b>пак не собрался</b>\n\n"
            "<blockquote>генерации уже вернули на баланс — ничего не потеряно.</blockquote>\n"
            "попробуй ещё раз. если повторится — напиши /support."
        ),
        placeholders=(),
        hint="Отправляется пользователю, если сборка завершилась ошибкой",
    ),
    "payment_ok": MessageDef(
        key="payment_ok",
        title="✅ Успешная оплата",
        default=(
            "✅ <b>оплата прошла</b>\n\n"
            "<blockquote>💎 начислено: <b>+{qty_text}</b>\n"
            "💰 на балансе: <b>{balance}</b></blockquote>\n\n"
            "генерации не сгорают — собирай паки, когда удобно."
        ),
        placeholders=("{qty}", "{qty_text}", "{balance}"),
        hint="Отправляется сразу после зачисления оплаты",
    ),
    "referral_reward": MessageDef(
        key="referral_reward",
        title="🎁 Награда за реферала",
        default=(
            "🎁 <b>награда за друга!</b>\n\n"
            "твой друг собрал первый пак и сделал покупку.\n"
            "<blockquote>начислено: <b>+{amount_text}</b></blockquote>\n\n"
            "приглашай ещё — ссылка в профиле редактора."
        ),
        placeholders=("{amount}", "{amount_text}"),
        hint="Отправляется пользователю, когда приглашённый друг сделал покупку",
    ),
    "partner_reward": MessageDef(
        key="partner_reward",
        title="🔗 Награда партнёру",
        default=(
            "🔗 <b>+{amount_text} по твоей ссылке!</b>\n\n"
            "по твоей партнёрской ссылке пришёл новый человек.\n"
            "<blockquote>начислено: <b>+{amount_text}</b></blockquote>\n\n"
            "статистика ссылки — /partner"
        ),
        placeholders=("{amount}", "{amount_text}"),
        hint="Отправляется владельцу партнёрской ссылки, когда приведённый выполнил условие награды",
    ),
    "support": MessageDef(
        key="support",
        title="🤝 Контакты поддержки (/support)",
        default="поддержка: {who}",
        placeholders=("{username}", "{who}"),
        hint="Ответ на команду /support",
    ),
    "paysupport": MessageDef(
        key="paysupport",
        title="💳 Поддержка по оплате (/paysupport)",
        default=(
            "если оплата прошла, а генерации не пришли, или нужен возврат: "
            "напиши {who} и приложи скрин оплаты. неиспользованные генерации вернём."
        ),
        placeholders=("{username}", "{who}"),
        hint="Ответ на команду /paysupport",
    ),
    "terms": MessageDef(
        key="terms",
        title="📜 Условия покупки (/terms)",
        default=(
            "условия покупки: генерации не сгорают, 1 генерация расходуется на 1 эмодзи или стикер. "
            "если пак не удалось собрать, генерации вернутся на баланс.{url_clause}"
        ),
        placeholders=("{url}", "{url_clause}"),
        hint="Ответ на команду /terms",
    ),
    "maintenance": MessageDef(
        key="maintenance",
        title="🔧 Режим техработ",
        default="🔧 сейчас идут технические работы. загляни чуть позже — всё сохранится.",
        placeholders=(),
        hint="Ответ бота, когда включён режим техработ",
    ),
    "fallback": MessageDef(
        key="fallback",
        title="💬 Кнопка редактора (на любое сообщение)",
        default="жми кнопку ниже, чтобы открыть редактор.",
        placeholders=(),
        hint="Ответ на любое входящее сообщение без команды",
    ),
    "payment_problem": MessageDef(
        key="payment_problem",
        title="⚠️ Проблема с платежом",
        default=(
            "⚠️ <b>оплата получена, но требует проверки</b>\n\n"
            "<blockquote>мы не смогли автоматически сопоставить платёж с заказом. деньги не потеряются.</blockquote>\n"
            "напиши /paysupport и приложи этот чат: начислим генерации вручную или вернём оплату."
        ),
        placeholders=(),
        hint="Отправляется, если платеж требует ручного разбора",
    ),
}


def get_custom_text(ctx: Any, key: str) -> str:
    if ctx and getattr(ctx, "custom_texts", None) and key in ctx.custom_texts:
        return ctx.custom_texts[key]
    mdef = MSG_DEFS.get(key)
    return mdef.default if mdef else ""


def render_msg(ctx: Any, key: str, **kwargs) -> str:
    template = get_custom_text(ctx, key)
    # Экранируем строковые параметры пользователя, чтобы они не ломали HTML:
    safe_kwargs = {
        k: html.escape(str(v)) if isinstance(v, str) and k not in ("details", "url_clause", "who", "url") else v
        for k, v in kwargs.items()
    }
    try:
        return template.format_map(SafeDict(**safe_kwargs))
    except Exception:
        mdef = MSG_DEFS.get(key)
        if mdef and template != mdef.default:
            try:
                return mdef.default.format_map(SafeDict(**safe_kwargs))
            except Exception:
                pass
        return template


def start_no_webapp(brand: str) -> str:
    return (
        f"привет! это {brand}.\n\n"
        "редактор пока недоступен: не настроен адрес Mini App (WEBAPP_URL). "
        "как только он появится, здесь будет кнопка «открыть редактор»."
    )


def help_text(brand: str, ctx: Any = None) -> str:
    return render_msg(ctx, "start", brand=brand)


def short_description(brand: str) -> str:
    return f"{brand}: свои эмодзи и стикеры с ником и логотипом."


def long_description(brand: str) -> str:
    return (
        f"{brand} собирает персональные паки эмодзи и стикеров: выбери шаблон, "
        "добавь ник или логотип, подбери цвета, и пак готов. первая генерация в подарок."
    )


BTN_VIEW_RESULT = "✨ добавить в Telegram"
BTN_SEND_FRIEND = "📤 отправить другу"
BTN_MORE = "🎨 собрать ещё"
BTN_RETRY = "🔁 попробовать снова"
BTN_TO_EDITOR = "🎨 открыть редактор"


@dataclass(frozen=True)
class ButtonDef:
    key: str
    title: str
    category: str
    default_text: str
    default_style: str | None = None
    default_emoji_id: str | None = None
    hint: str = ""


BTN_DEFS: dict[str, ButtonDef] = {
    "pack_ready_add": ButtonDef(
        key="pack_ready_add",
        title="✨ Кнопка «Добавить в Telegram»",
        category="Готовый пак",
        default_text=BTN_VIEW_RESULT,
        default_style="primary",
        hint="Ссылка на готовый пак стикеров/эмодзи",
    ),
    "pack_ready_share": ButtonDef(
        key="pack_ready_share",
        title="📥 Кнопка «Отправить другу»",
        category="Готовый пак",
        default_text=BTN_SEND_FRIEND,
        default_style=None,
        hint="Ссылка 'поделиться паком'",
    ),
    "pack_ready_more": ButtonDef(
        key="pack_ready_more",
        title="🎨 Кнопка «Собрать ещё»",
        category="Готовый пак",
        default_text=BTN_MORE,
        default_style="success",
        hint="Открывает Mini App для сборки нового пака",
    ),
    "start_open": ButtonDef(
        key="start_open",
        title="🚀 Кнопка «Открыть редактор» (/start)",
        category="Приветствие (/start)",
        default_text=BTN_OPEN,
        default_style=BTN_OPEN_STYLE,
        hint="Главная кнопка запуска редактора в приветствии и при ответах на сообщения",
    ),
    "welcome_open": ButtonDef(
        key="welcome_open",
        title="🚀 Кнопка «Запустить бота» (/start)",
        category="Приветствие (/start)",
        default_text="Запустить бота",
        default_style="primary",
        default_emoji_id=EMOJI_IDS["rocket"],
        hint="Открывает Mini App под баннером приветствия",
    ),
    "welcome_support": ButtonDef(
        key="welcome_support",
        title="💬 Кнопка «Поддержка» (/start)",
        category="Приветствие (/start)",
        default_text="Поддержка",
        default_emoji_id=EMOJI_IDS["question"],
        hint="Ссылка на поддержку (SUPPORT_USERNAME, по умолчанию @EzzSup)",
    ),
    "welcome_ref": ButtonDef(
        key="welcome_ref",
        title="🔗 Кнопка «Реф. ссылка» (/start)",
        category="Приветствие (/start)",
        default_text="Реф. ссылка",
        default_emoji_id=EMOJI_IDS["gift"],
        hint="Копирует реферальную ссылку пользователя",
    ),
    "pack_failed_retry": ButtonDef(
        key="pack_failed_retry",
        title="🔁 Кнопка «Попробовать снова»",
        category="Ошибка сборки",
        default_text=BTN_RETRY,
        default_style="danger",
        hint="Открывает редактор при ошибке сборки пака",
    ),
    "payment_ok_editor": ButtonDef(
        key="payment_ok_editor",
        title="💎 Кнопка «В редактор» после оплаты",
        category="Уведомления",
        default_text=BTN_TO_EDITOR,
        default_style="success",
        hint="Отправляется в сообщении об успешном зачислении генераций",
    ),
    "referral_reward_editor": ButtonDef(
        key="referral_reward_editor",
        title="🎁 Кнопка «В редактор» за друга",
        category="Уведомления",
        default_text=BTN_TO_EDITOR,
        default_style="primary",
        hint="Отправляется в сообщении о награде за реферала",
    ),
    "sub_join": ButtonDef(
        key="sub_join",
        title="📢 Кнопка «Подписаться на канал»",
        category="Обязательная подписка",
        default_text="Подписаться на канал",
        default_style=None,
        hint="Ссылка на канал для обязательной подписки",
    ),
    "sub_check": ButtonDef(
        key="sub_check",
        title="✅ Кнопка «Проверить подписку»",
        category="Обязательная подписка",
        default_text="Проверить подписку",
        default_style=None,
        hint="Кнопка проверки подписки на канал",
    ),
}


def make_button(
    ctx: Any,
    key: str,
    *,
    url: str | None = None,
    web_app_url: str | None = None,
    callback_data: str | None = None,
    copy_text: str | None = None,
    default_text_override: str | None = None,
) -> InlineKeyboardButton:
    """Создаёт InlineKeyboardButton с учётом кастомного текста, цвета (style) и премиум-эмодзи."""
    text, style, emoji_id = button_look(ctx, key, default_text_override)

    kwargs: dict[str, Any] = {"text": text}
    if style:
        kwargs["style"] = style
    if emoji_id:
        kwargs["icon_custom_emoji_id"] = str(emoji_id)

    if web_app_url:
        kwargs["web_app"] = WebAppInfo(url=web_app_url)
    elif url:
        kwargs["url"] = url
    elif callback_data:
        kwargs["callback_data"] = callback_data
    elif copy_text:
        kwargs["copy_text"] = CopyTextButton(text=copy_text[:256])
    return InlineKeyboardButton(**kwargs)


def button_look(ctx: Any, key: str, default_text_override: str | None = None) -> tuple[str, str | None, str | None]:
    """Текст, цвет (primary/success/danger или None) и премиум-эмодзи кнопки: настройки из админки поверх стандартных."""
    bdef = BTN_DEFS.get(key)
    custom = (getattr(ctx, "custom_buttons", None) or {}).get(key) or {}
    legacy_text = None
    if key == "sub_join":
        legacy_text = (getattr(ctx, "custom_texts", None) or {}).get("subscription_join")
    elif key == "sub_check":
        legacy_text = (getattr(ctx, "custom_texts", None) or {}).get("subscription_check")

    text = custom.get("text") or default_text_override or legacy_text or (bdef.default_text if bdef else key)

    custom_style = custom.get("style")
    if custom_style in ("primary", "success", "danger"):
        style = custom_style
    elif custom_style in ("default", "none"):
        style = None
    else:
        style = bdef.default_style if bdef else None

    emoji_id = custom.get("icon_custom_emoji_id", bdef.default_emoji_id if bdef else None)
    return text, style if style in ("primary", "success", "danger") else None, str(emoji_id) if emoji_id else None


# ───────────── rich-сообщения (Bot API 10.1+): баннер, текст и кнопки внутри одного сообщения ─────────────

RICH_BANNER_ID = "banner"  # картинка из поля media, на неё ссылается <img src="tg://photo?id=banner"/>


def rich_button(ctx: Any, key: str, kind: str, **attrs: str) -> str:
    """Кнопка внутри rich-сообщения: <tg-button type="web_app|url|copy_text" ...>[эмодзи] текст</tg-button>."""
    text, style, emoji_id = button_look(ctx, key)
    parts = [f'type="{kind}"'] + ([f'style="{style}"'] if style else [])
    parts += [f'{name}="{html.escape(value, quote=True)}"' for name, value in attrs.items()]
    icon = f'<tg-emoji emoji-id="{emoji_id}"></tg-emoji> ' if emoji_id else ""
    return f"<tg-button {' '.join(parts)}>{icon}{html.escape(text)}</tg-button>"


def rich_paragraphs(text: str) -> str:
    """Текст в формате подписи → rich HTML. Абзацы <p> в rich-сообщении идут вплотную, поэтому текст —
    один абзац: перевод строки → <br/>, пустая строка между блоками → пустая строка (<br/><br/>), как в обычном сообщении."""
    blocks = [b.strip("\n") for b in text.replace("\r\n", "\n").split("\n\n") if b.strip()]
    body = "<br/><br/>".join("<br/>".join(b.split("\n")) for b in blocks)
    return f"<p>{body}</p>" if body else ""


def rich_welcome(ctx: Any, text: str, rows: list[list[str]]) -> str:
    """Приветствие /start одним rich-сообщением: баннер, текст и ряды кнопок (<tg-button-row>)."""
    buttons = "".join(f"<tg-button-row>{''.join(row)}</tg-button-row>" for row in rows if row)
    return f'<img src="tg://photo?id={RICH_BANNER_ID}"/>{rich_paragraphs(text)}{buttons}'


def pack_ready(name: str, demo: bool, failed: int = 0, ctx: Any = None) -> str:
    details = ""
    if failed:
        details += f"\n\n⚠️ не получилось собрать {failed} шт. — генерации за них вернули на баланс."
    if demo:
        details += "\n\n🧪 <i>это демо-сборка: настоящий набор в Telegram не создавался.</i>"
    return render_msg(ctx, "pack_ready", name=name, failed=failed, details=details)


def share_text(kind: str) -> str:
    return "смотри, какие эмодзи я собрал" if kind == "emoji" else "смотри, какие стикеры я собрал"


def pack_failed(ctx: Any = None) -> str:
    return render_msg(ctx, "pack_failed")


def referral_reward(amount: int, ctx: Any = None) -> str:
    return render_msg(ctx, "referral_reward", amount=amount, amount_text=gens(amount))


def partner_reward(amount: int, ctx: Any = None) -> str:
    return render_msg(ctx, "partner_reward", amount=amount, amount_text=gens(amount))


def payment_ok(qty: int, balance: int, ctx: Any = None) -> str:
    return render_msg(ctx, "payment_ok", qty=qty, qty_text=gens(qty), balance=balance)


PAYMENT_STALE = "платёж устарел, открой покупку в редакторе заново."
PAYMENT_PROBLEM = MSG_DEFS["payment_problem"].default
FALLBACK = MSG_DEFS["fallback"].default
MAINTENANCE = MSG_DEFS["maintenance"].default


def payment_problem_text(ctx: Any = None) -> str:
    return render_msg(ctx, "payment_problem")


def fallback_text(ctx: Any = None) -> str:
    return render_msg(ctx, "fallback")


def maintenance_text(ctx: Any = None) -> str:
    return render_msg(ctx, "maintenance")


def terms(url: str | None, ctx: Any = None) -> str:
    url_clause = f"\n\n{url}" if url else ""
    return render_msg(ctx, "terms", url=url or "", url_clause=url_clause)


def support(username: str | None, ctx: Any = None) -> str:
    who = f"@{username}" if username else "поддержка скоро появится."
    return render_msg(ctx, "support", username=username or "", who=who)


def paysupport(username: str | None, ctx: Any = None) -> str:
    who = f"@{username}" if username else "в поддержку"
    return render_msg(ctx, "paysupport", username=username or "", who=who)


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
    return (f"↩️ <b>возврат выполнен</b>\n\n"
            f"<blockquote>Stars вернулись на твой счёт, {gens(qty)} сняты с баланса.</blockquote>")


PROMO_USAGE = "введи промокод так: /promo КОД"
PROMO_ERRORS = {
    "unknown": "такого промокода нет. проверь, как он написан.",
    "used": "этот промокод ты уже активировал.",
    "exhausted": "у этого промокода закончились активации.",
    "expired": "срок действия этого промокода закончился.",
    "paused": "этот промокод сейчас не действует.",
    "not_new": "этот промокод только для новых пользователей: тех, кто ещё ничего не покупал.",
}
PROMO_ASK = "🎟 пришли промокод следующим сообщением"
PROMO_SLOW = "слишком много попыток. подожди минутку и попробуй снова."


def promo_ok(amount: int, balance: int, discount_pct: int = 0) -> str:
    lines = []
    if amount > 0:
        lines.append(f"💎 начислено: <b>+{gens(amount)}</b>")
        lines.append(f"💰 на балансе: <b>{balance}</b>")
    if discount_pct > 0:
        lines.append(f"🏷 скидка на покупки: <b>−{discount_pct}%</b>")
    body = "\n".join(lines)
    return (f"🎟 <b>промокод активирован!</b>\n\n"
            f"<blockquote>{body}</blockquote>")


def admin_alert(text: str) -> str:
    import html as _html

    return f"🛎 <b>для админа</b>\n\n{_html.escape(text)}"
