"""Сборка бота: Bot, Dispatcher, уведомления, настройка меню."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, WebAppInfo

from ..config import Settings
from ..context import Context
from . import handlers, texts

log = logging.getLogger("ezzprod.bot")


def create_bot(settings: Settings) -> Bot:
    session = AiohttpSession(proxy=settings.bot_proxy) if settings.bot_proxy else None
    return Bot(token=settings.bot_token, session=session, default=DefaultBotProperties())


def create_dispatcher(ctx: Context) -> Dispatcher:
    dp = Dispatcher(ctx=ctx)  # ctx попадает в хендлеры как именованный аргумент
    # роутер модульный, а aiogram разрешает ему иметь только одного родителя: отцепляем от прошлого диспетчера
    # (нужно при повторном создании в одном процессе: перезапуск бота, тесты)
    if handlers.router.parent_router is not None:
        handlers.router._parent_router = None
    dp.include_router(handlers.router)
    return dp


class TelegramNotifier:
    def __init__(self, bot: Bot, ctx: Context) -> None:
        self._bot = bot
        self._ctx = ctx

    async def _send(self, user_id: int, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
        try:
            await self._bot.send_message(user_id, text, reply_markup=markup)
        except Exception as exc:  # пользователь мог заблокировать бота — это не ошибка сервиса
            log.warning("не доставлено сообщение пользователю %s: %s", user_id, exc)

    async def pack_ready(self, user_id: int, pack: dict) -> None:
        s = self._ctx.settings
        markup = None
        if pack.get("link", "").startswith("https://"):
            markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_OPEN_PACK, url=pack["link"])]]
            )
        await self._send(user_id, texts.pack_ready(pack["title"], pack["count"], s.demo_mode), markup)

    async def pack_failed(self, user_id: int, pack_id: int) -> None:
        await self._send(user_id, texts.pack_failed())

    async def referral_reward(self, user_id: int, amount: int) -> None:
        await self._send(user_id, texts.referral_reward(amount))

    async def payment_ok(self, user_id: int, qty: int, balance: int) -> None:
        await self._send(user_id, texts.payment_ok(qty, balance))

    async def admin_alert(self, text: str) -> None:
        for admin_id in self._ctx.settings.admin_ids:
            await self._send(admin_id, text)


async def configure_bot(bot: Bot, ctx: Context) -> None:
    """Имя бота, меню «открыть», команды, описание. Каждый шаг не критичен для запуска."""
    s = ctx.settings
    me = await bot.get_me()
    ctx.bot_username = s.bot_username or (me.username or "")
    log.info("бот запущен: @%s", ctx.bot_username)

    async def attempt(label: str, coro) -> None:
        try:
            await coro
        except Exception as exc:
            log.warning("не удалось настроить %s: %s", label, exc)

    if s.webapp_url.startswith("https://"):
        await attempt(
            "кнопку меню",
            bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text=texts.MENU_BUTTON, web_app=WebAppInfo(url=s.webapp_url))
            ),
        )
    else:
        log.warning("WEBAPP_URL не https — кнопка Mini App в боте недоступна (нужен туннель)")
    await attempt(
        "команды",
        bot.set_my_commands([
            BotCommand(command="start", description="открыть редактор"),
            BotCommand(command="help", description="как это работает"),
            BotCommand(command="terms", description="условия покупки"),
            BotCommand(command="support", description="поддержка"),
            BotCommand(command="paysupport", description="вопросы по оплате и возврат"),
        ]),
    )
    await attempt("описание", bot.set_my_description(texts.long_description(s.brand_name)))
    await attempt("короткое описание", bot.set_my_short_description(texts.short_description(s.brand_name)))
