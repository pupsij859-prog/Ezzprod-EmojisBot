"""Точка сборки: FastAPI (API + статика Mini App) и бот aiogram в одном процессе."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .api.admin import router as admin_router
from .api.routes import install_error_handlers, router
from .config import WEBAPP_DIR, Settings
from .context import Context
from .db import Database
from .notify import NullNotifier
from .packs import MockPackBuilder, PackRunner

log = logging.getLogger("ezzprod")

# Ограничения тела запроса проверяются по Content-Length ДО разбора (иначе multipart успевает долиться на диск)
MAX_JSON_BODY = 64 * 1024
MAX_UPLOAD_BODY = 3 * 1024 * 1024


def create_app(settings: Settings | None = None, *, with_bot: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.db_path)
        await db.connect()
        builder = MockPackBuilder(settings)
        runner = PackRunner(db, settings, builder, NullNotifier())
        ctx = Context(settings=settings, db=db, builder=builder, runner=runner, notifier=runner.notifier)
        app.state.ctx = ctx

        bot = None
        tasks: list[asyncio.Task] = []
        if settings.dev_mode:
            log.warning("DEV_MODE включён: вход без подписи Telegram разрешён только с localhost")
        if with_bot and settings.bot_token:
            from aiogram.exceptions import TelegramNetworkError

            from .bot import TelegramNotifier, configure_bot, create_bot, create_dispatcher
            from .payments import StarsProvider
            from .reconcile import reconcile_loop

            bot = create_bot(settings)

            async def start_bot() -> None:
                """Подключение к Telegram в фоне: веб-часть уже работает. Нет связи с api.telegram.org —
                повторяем с растущей паузой (бывает после смены туннеля или VPN); другие ошибки (например,
                неверный токен) повторять бессмысленно."""
                delay = 5
                while True:
                    try:
                        await configure_bot(bot, ctx)
                        break
                    except (TelegramNetworkError, OSError, asyncio.TimeoutError) as exc:
                        log.warning("нет связи с api.telegram.org (%s), повтор через %s с", exc, delay)
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, 60)
                    except Exception:
                        log.exception("бот не запустился (проверь BOT_TOKEN). Работает только веб-часть")
                        return
                ctx.bot = bot
                ctx.notifier = runner.notifier = TelegramNotifier(bot, ctx)
                ctx.payments = StarsProvider(bot, settings.brand_name)
                builder.set_bot_username(ctx.bot_username)
                dp = create_dispatcher(ctx)
                tasks.append(asyncio.create_task(
                    dp.start_polling(bot, handle_signals=False, allowed_updates=dp.resolve_used_update_types()),
                    name="bot-polling",
                ))
                if settings.payments_live:
                    tasks.append(asyncio.create_task(reconcile_loop(ctx), name="stars-reconcile"))
                    if settings.demo_mode:
                        log.warning(
                            "DEMO_MODE=1: выпуск счетов и подтверждение оплаты Stars заблокированы. "
                            "Для работы с реальными платежами нужна настоящая сборка паков."
                        )

            tasks.append(asyncio.create_task(start_bot(), name="bot-start"))
        elif with_bot:
            log.warning("BOT_TOKEN не задан: работает только веб-часть (без бота и оплаты Stars)")

        mismatches = await _check_ledger(db)
        if mismatches:
            log.error("БАЛАНС НЕ СХОДИТСЯ С ЖУРНАЛОМ у пользователей: %s", mismatches)
        await runner.recover()
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await runner.shutdown()
            if bot is not None:
                await bot.session.close()
            await db.close()

    app = FastAPI(title=f"{settings.brand_name} bot", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(router)
    app.include_router(admin_router)
    if settings.dev_mode:
        from .api.dev import router as dev_router  # в проде модуль даже не импортируется

        app.include_router(dev_router)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.middleware("http")
    async def guards(request: Request, call_next):
        path = request.url.path
        if request.method in ("POST", "PUT", "PATCH"):
            declared = request.headers.get("content-length")
            limit = MAX_UPLOAD_BODY if path == "/api/logos" else MAX_JSON_BODY
            if declared is not None and declared.isdigit() and int(declared) > limit:
                return JSONResponse({"detail": {"code": "too_large"}}, status_code=413)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not path.startswith("/api/") and "Cache-Control" not in response.headers:
            # html/js/css всегда ревалидируем, тяжёлые ассеты кэшируем
            heavy = path.endswith((".svg", ".jpg", ".jpeg", ".png", ".webp", ".woff2"))
            response.headers["Cache-Control"] = "public, max-age=3600" if heavy else "no-cache"
        return response

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api/"):
            detail = getattr(exc, "detail", None)
            return JSONResponse({"detail": detail if isinstance(detail, dict) else {"code": "not_found"}}, status_code=404)
        return PlainTextResponse("not found", status_code=404)

    app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")
    return app


async def _check_ledger(db: Database):
    from . import services

    return await services.ledger_mismatches(db)
