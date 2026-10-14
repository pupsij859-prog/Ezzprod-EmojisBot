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

# Ограничения тела запроса проверяются ДО разбора (иначе multipart успевает долиться на диск)
MAX_JSON_BODY = 64 * 1024
MAX_UPLOAD_BODY = 3 * 1024 * 1024


# Политика для HTML Mini App: свой код, скрипт Telegram, Lottie с cdnjs (анимированные эмодзи), шрифт с Google.
# frame-ancestors — Mini App открывается во фрейме Telegram Web.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://telegram.org https://cdnjs.cloudflare.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: blob: https:",
    "media-src 'self' blob:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org https://telegram.org",
])


class BodyLimitMiddleware:
    """Чистый ASGI-слой: режет тело запроса сверх лимита и отвечает 413.
    Работает и для chunked-запросов без Content-Length (заголовок проверяется как быстрый путь)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        limit = MAX_UPLOAD_BODY if scope["path"] == "/api/logos" else MAX_JSON_BODY
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            return await self._reject(send)

        received = 0
        exceeded = False
        replied = False

        async def limited_receive():
            nonlocal received, exceeded
            if exceeded:  # дальше не читаем: тело обрывается, разбор упадёт, а ответ подменим на 413
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def guarded_send(message):
            nonlocal replied
            if not exceeded:
                await send(message)
                return
            # приложение ответило на оборванное тело (обычно 400 от разбора multipart) — отдаём 413 вместо него
            if message["type"] == "http.response.start" and not replied:
                replied = True
                await self._reject(send)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not replied:
            await self._reject(send)

    @staticmethod
    async def _reject(send) -> None:
        body = b'{"detail":{"code":"too_large"}}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def create_app(settings: Settings | None = None, *, with_bot: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.db_path)
        await db.connect()
        builder = MockPackBuilder(settings)
        runner = PackRunner(db, settings, builder, NullNotifier())
        ctx = Context(settings=settings, db=db, builder=builder, runner=runner, notifier=runner.notifier,
                      bot_username=settings.bot_username)  # уточнится через getMe, если бот запустится
        app.state.ctx = ctx

        bot = None
        live_build = bool(with_bot and settings.bot_token and not settings.demo_mode)
        tasks: list[asyncio.Task] = []  # фоновые задачи, гасим при остановке
        if settings.dev_mode:
            log.warning("DEV_MODE включён: вход без подписи Telegram разрешён только с localhost")
        if with_bot and settings.bot_token:
            from aiogram.exceptions import TelegramNetworkError

            from .bot import TelegramNotifier, configure_bot, create_bot, create_dispatcher
            from .payments import StarsProvider
            from .reconcile import reconcile_loop

            bot = create_bot(settings)
            if live_build:
                # настоящая сборка с самого начала: имитация не должна «собрать» пак, пока бот подключается
                from .build.builder import TelegramPackBuilder
                from .build.publish import TelegramPublisher
                from .build.render import TemplateRenderer

                builder = TelegramPackBuilder(db, settings, TemplateRenderer(), TelegramPublisher(bot), ctx.bot_username)
                ctx.builder = runner.builder = builder

            def bot_off() -> None:
                ctx.bot = None
                ctx.payments = None
                ctx.notifier = runner.notifier = NullNotifier()

            async def start_bot() -> None:
                """Подключение к Telegram в фоне: веб-часть уже работает. Нет связи с api.telegram.org
                (бывает при смене сети, VPN или туннеля) — повторяем с растущей паузой; другие ошибки
                (например, неверный токен) повторять бессмысленно."""
                delay = 5
                while True:
                    try:
                        ctx.bot = bot
                        ctx.notifier = runner.notifier = TelegramNotifier(bot, ctx)
                        ctx.payments = StarsProvider(bot, settings.brand_name)
                        await configure_bot(bot, ctx)
                        break
                    except (TelegramNetworkError, OSError, asyncio.TimeoutError) as exc:
                        bot_off()
                        log.warning("нет связи с api.telegram.org (%s), повтор через %s с", exc, delay)
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, 60)
                    except Exception:
                        bot_off()
                        log.exception("бот не запустился (проверь BOT_TOKEN). Работает только веб-часть")
                        return
                builder.set_bot_username(ctx.bot_username)
                if live_build:
                    log.info("сборка паков: настоящие наборы Telegram")
                    await runner.recover()  # паки, ждавшие подключения к Telegram
                    tasks.append(asyncio.create_task(runner.watch(), name="packs-watch"))
                dp = create_dispatcher(ctx)
                polling = asyncio.create_task(
                    dp.start_polling(bot, handle_signals=False, allowed_updates=dp.resolve_used_update_types()),
                    name="bot-polling",
                )
                polling.add_done_callback(lambda task: _on_polling_stopped(task, ctx))
                tasks.append(polling)
                # баннер для /start собираем заранее и не в цикле событий (Pillow блокирует)
                from .bot import banner as _banner

                asyncio.create_task(asyncio.to_thread(_banner.get_banner, settings), name="banner-warmup")
                if settings.payments_live:
                    tasks.append(asyncio.create_task(reconcile_loop(ctx), name="stars-reconcile"))
                    if settings.demo_mode:
                        log.warning(
                            "DEMO_MODE=1: выпуск счетов и подтверждение оплаты Stars заблокированы. "
                            "Для работы с реальными платежами нужна настоящая сборка паков."
                        )

            bot_off()  # пока не подключились, оплаты и уведомлений нет
            tasks.append(asyncio.create_task(start_bot(), name="bot-start"))
        elif with_bot:
            log.warning("BOT_TOKEN не задан: работает только веб-часть (без бота и оплаты Stars)")

        from . import runtime

        await runtime.load(ctx)  # правки настроек из админки в чате, техработы, админы
        mismatches = await _check_ledger(db)
        if mismatches:
            log.error("БАЛАНС НЕ СХОДИТСЯ С ЖУРНАЛОМ у пользователей: %s", mismatches)
        watcher = None
        if not live_build:  # в живом режиме дособираем после подключения к Telegram (см. start_bot)
            await runner.recover()
            watcher = asyncio.create_task(runner.watch(), name="packs-watch")
        url_watcher = None
        if settings.public_url_file:
            from pathlib import Path

            from .public_url import watch_public_url

            url_watcher = asyncio.create_task(watch_public_url(ctx, Path(settings.public_url_file)), name="public-url")
        tasks += [t for t in (watcher, url_watcher) if t is not None]
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
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers.setdefault("Content-Security-Policy", CSP)
        if not path.startswith("/api/") and "Cache-Control" not in response.headers:
            # html/js/css всегда ревалидируем, тяжёлые ассеты кэшируем
            heavy = path.endswith((".svg", ".jpg", ".jpeg", ".png", ".webp", ".woff2"))
            response.headers["Cache-Control"] = "public, max-age=3600" if heavy else "no-cache"
        return response

    # самый внешний слой: тело режется до того, как его начнут разбирать маршруты и multipart
    app.add_middleware(BodyLimitMiddleware)

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api/"):
            detail = getattr(exc, "detail", None)
            return JSONResponse({"detail": detail if isinstance(detail, dict) else {"code": "not_found"}}, status_code=404)
        return PlainTextResponse("not found", status_code=404)

    app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")
    return app


def _on_polling_stopped(task: asyncio.Task, ctx: Context) -> None:
    """Поллинг умер (отозван токен, сеть): без него никто не ответит на pre_checkout, поэтому счета больше не выдаём."""
    if task.cancelled():
        return
    exc = task.exception()
    log.error("поллинг бота остановился: %r — выдача счетов отключена до перезапуска", exc)
    ctx.payments = None


async def _check_ledger(db: Database):
    from . import services

    return await services.ledger_mismatches(db)
