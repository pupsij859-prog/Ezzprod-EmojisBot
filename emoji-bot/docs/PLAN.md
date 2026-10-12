# План: EzzProd bot (имитация референса, этап 1)

## Задача пользователя
- Максимально точно повторить бота: чат, Mini App, эмодзи и стикеры, весь каталог (категории и счётчики как у референса).
- Этап 1: **без реальной генерации** — шаблоны это заглушки (процедурный SVG), «создание пака» — имитация (прогресс → «готово»). Реальную генерацию и создание наборов в Telegram добавим позже → нужны чёткие интерфейсы-швы.
- Оплата: **только Telegram Stars (XTR)**, позже заменим на свою → платёжный провайдер за интерфейсом.
- Онбординг: при ПЕРВОМ входе в Mini App — окно-приветствие с информацией о сервисе (1 раз), затем инструкция с картинками (карусель). Из профиля инструкцию можно открыть снова. По умолчанию показывается один раз.
- «Оформи всё красиво». Бренд — **EzzProd**. Язык — русский, строчные, как у референса.

## Стек
- Python 3.14 (venv), **FastAPI** + uvicorn, **aiogram 3** (бот, polling в том же процессе), **aiosqlite** (SQLite), Pillow (баннер, проверка логотипа).
- Фронтенд — **без сборки**: ванильные ES-модули + CSS, отдаётся FastAPI. Один процесс, один `run.bat`.
- Mini App требует публичный HTTPS → `WEBAPP_URL` в `.env` (туннель вроде cloudflared/ngrok, пользователь ставит сам). Для разработки — открытие `http://localhost:8000` в браузере с `DEV_MODE=1` (шим Telegram.WebApp + фейковый пользователь).

## Структура
```
emoji-bot/
  run.bat, .env.example, requirements.txt, README.md
  app/ main.py config.py db.py auth.py catalog.py pricing.py services.py packs.py payments.py
       api/ (routes)   bot/ (handlers.py banner.py texts.py)
  webapp/ index.html  css/  js/ (api, tg, state, router, views/*, components/*)  assets/
  tests/
```

## Модель данных (SQLite)
- `users(id PK=tg_id, username, first_name, last_name, balance, plan 'free'|'pro', trial_exports_left=1, onboarded=0, ref_code UNIQUE, referred_by, referral_rewarded=0, purchased_total=0, created_at)`
- `ledger(id, user_id, delta, reason, ref, created_at, UNIQUE(reason, ref))` — каждое изменение баланса; идемпотентность по (reason, ref).
- `packs(id, user_id, type 'emoji'|'sticker', title, status 'processing'|'ready'|'failed', count, items JSON, params JSON, link, duration_s, created_at, ready_at)`
- `saved_styles(id, user_id, name, params JSON, created_at)`
- `payments(id PK uuid, user_id, qty, stars, status 'pending'|'paid'|'failed', charge_id UNIQUE, created_at, paid_at)`
- `logos(id, user_id, path, created_at)`

Баланс = users.balance, меняется ТОЛЬКО в транзакции вместе с записью в ledger.

## API (`/api`, авторизация: заголовок `Authorization: tma <initData>`)
- `GET /me` → профиль, баланс, plan, trial, onboarded, flags, ref_link, brand
- `POST /onboarding/complete`
- `GET /categories` → [{id,name,count}]
- `GET /templates?category=&page=&size=30` → {items:[{id,name}], page, pages, total}
- `GET /templates/{id}/preview.svg?text=&fill=&stroke=` (без авторизации, кэшируемый)
- `POST /logos` (multipart png/jpg/webp ≤2 МБ, перекодируется Pillow) → {id,url}
- `POST /packs` {type, template_ids, text, logo_id, fill, stroke} → 201 {pack} | 402 {need}
- `GET /packs`, `GET /packs/{id}` (прогресс)
- `GET/POST/DELETE /styles` — только plan=pro (иначе 403 {code:'pro_required'})
- `GET /pricing` ; `POST /billing/invoice` {qty} → {url} (Stars `createInvoiceLink`)
- `GET /referral` → {link, invited, created_pack, earned, reward}
- DEV (только DEV_MODE): `POST /dev/pay` {qty} — эмуляция оплаты.

## Бот (aiogram)
- `/start [ref_<code>]`: регистрация, реферал (только для НОВОГО пользователя, не сам себе), баннер-фото + inline web_app «открыть редактор». `/help` — кратко + кнопка. Menu button «открыть» (web_app) выставляется на старте.
- Платежи: `pre_checkout_query` (ответ < 10 с, сверка с pending-платежом и ценой), `successful_payment` → начисление идемпотентно по `telegram_payment_charge_id`, сообщение пользователю.
- Уведомления: «пак готов» (кнопка «открыть пак»), реферальная награда пригласившему.

## Ключевые решения / допущения (отмечено пользователю)
- 1 генерация = 1 эмодзи/стикер в паке. Стоимость пака = число выбранных шаблонов. Лимиты Telegram: эмодзи-пак ≤ 200, стикерпак ≤ 120.
- Новичок: 1 генерация. «Пробный экспорт» — информационный счётчик (1 → 0 после первого готового пака).
- Цены в Stars (конфигурируемо): база `STARS_PER_GEN`; ступени скидки: ≥30 шт −10%, ≥50 шт — фикс. цена. Скидочная таблица референса неизвестна → допущение.
- Pro: открывается после первой покупки генераций (допущение, чтобы «сохранённые стили» были достижимы). Free видит замок + объяснение.
- Реферал: код — непрозрачный `ref_<8 симв.>`, не Telegram ID. Награда +3 за приглашённого, когда его первый пак перешёл в `ready`; суточный лимит наград на пригласившего (конфиг).
- Имитация генерации: пак `processing` 3–5 с → `ready`, ссылка-заглушка. Шов: `PackBuilder` (позже — рендер + `createNewStickerSet`).
- Заглушки шаблонов: детерминированный SVG (≈14 форм × палитры), текст бренда на превью.

## Единый BackButton (исправляем референс)
Telegram BackButton показан ВСЕГДА, когда открыт любой оверлей/шторка/подэкран, скрыт только на корне вкладок. Стек оверлеев, закрытие по BackButton, ×, свайпу шторки, Esc.

## Этапы
1. Бэкенд + бот + тесты. 2. Дизайн-спека → фронтенд. 3. Онбординг + инструкция. 4. Проверка в браузере (мобильный вьюпорт), критик-ревью кода, правки. 5. README, запуск.
