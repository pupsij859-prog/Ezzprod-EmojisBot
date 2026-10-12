from types import SimpleNamespace as NS

from app import services
from app.context import Context
from app.notify import NullNotifier
from app.reconcile import payment_from_transaction, reconcile_stars


def tx(tx_id, amount, payload, user_id, kind="invoice_payment", source_type="user"):
    source = NS(type=source_type, transaction_type=kind, invoice_payload=payload, user=NS(id=user_id))
    return NS(id=tx_id, amount=amount, source=source, receiver=None)


class FakeBot:
    def __init__(self, txs):
        self.txs = txs
        self.calls = []

    async def get_star_transactions(self, offset=0, limit=100):
        self.calls.append((offset, limit))
        return NS(transactions=self.txs[offset:offset + limit])


class Spy(NullNotifier):
    def __init__(self):
        self.paid = []
        self.alerts = []

    async def payment_ok(self, user_id, qty, balance):
        self.paid.append((user_id, qty, balance))

    async def admin_alert(self, text):
        self.alerts.append(text)


def test_only_incoming_invoice_payments_are_taken():
    assert payment_from_transaction(tx("a", 120, "p1", 5)).charge_id == "a"
    assert payment_from_transaction(tx("a", 120, "p1", 5, kind="gift")) is None
    assert payment_from_transaction(tx("a", 120, "p1", 5, source_type="fragment")) is None
    assert payment_from_transaction(tx("a", 0, "p1", 5)) is None
    assert payment_from_transaction(tx("a", 120, "", 5)) is None
    assert payment_from_transaction(NS(id="r", amount=120, source=None, receiver=NS())) is None  # возврат


async def test_missed_payment_is_credited_once(db, settings):
    await services.upsert_user(db, settings, {"id": 1, "first_name": "a"})
    pid = await services.create_payment(db, 1, 20, 120)
    spy = Spy()
    runner = None
    ctx = Context(settings=settings, db=db, builder=None, runner=runner, notifier=spy)
    ctx.bot = FakeBot([tx("charge-9", 120, pid, 1)])

    assert await reconcile_stars(ctx) == 1                 # апдейт был потерян — доначислили
    assert (await services.get_user(db, 1))["balance"] == 21
    assert spy.paid == [(1, 20, 21)]
    assert await reconcile_stars(ctx) == 0                 # повторная сверка ничего не меняет
    assert (await services.get_user(db, 1))["balance"] == 21


async def test_reconcile_does_not_double_credit_after_normal_delivery(db, settings):
    await services.upsert_user(db, settings, {"id": 1, "first_name": "a"})
    pid = await services.create_payment(db, 1, 20, 120)
    await services.settle_payment(db, settings, pid, "charge-1", 120, 1)   # обычная доставка успела
    ctx = Context(settings=settings, db=db, builder=None, runner=None, notifier=Spy())
    ctx.bot = FakeBot([tx("charge-1", 120, pid, 1)])
    assert await reconcile_stars(ctx) == 0
    assert (await services.get_user(db, 1))["balance"] == 21


async def test_unknown_payment_alerts_admin(db, settings):
    await services.upsert_user(db, settings, {"id": 1, "first_name": "a"})
    spy = Spy()
    ctx = Context(settings=settings, db=db, builder=None, runner=None, notifier=spy)
    ctx.bot = FakeBot([tx("charge-x", 120, "unknown-payload", 1)])
    assert await reconcile_stars(ctx) == 0
    assert len(spy.alerts) == 1 and "charge-x" in spy.alerts[0]
    assert (await services.get_user(db, 1))["balance"] == 1
