import json
import os
import tempfile
from decimal import Decimal as D

from omnipay import Switch
from app.config import Settings
from app.paypal_fake import FakePayPal
from app.platform import DEMO_PRICES, Platform

Switch.PIN_ROUNDS = 1_000


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def mk(**kw):
    """A fresh Platform on a temp DB with a fake clock, simulated PayPal and fast password hashing."""
    clock = kw.pop("clock", Clock())
    path = os.path.join(tempfile.mkdtemp(), "omnipay.db")
    cfg = Settings(db_path=path, paypal_webhook_id="WH-TEST", **kw.pop("cfg", {}))
    pp = kw.pop("paypal", FakePayPal(clock))
    pf = Platform(cfg, clock=clock, paypal=pp, prices=DEMO_PRICES, scrypt_n=2 ** 4, **kw)
    pf.clock_obj = clock
    return pf


def seed_users(pf):
    for h, region, settle in (("alice@omni", "US", "USD"), ("bob@omni", "US", "USD"), ("dana@omni", "DE", "EUR")):
        pf.signup(h, "correct-horse-1", "1234", region=region, settle_in=settle, email=h)
    for c, a in (("USD", "1000000"), ("EUR", "1000000"), ("GBP", "1000000")):
        pf.sw.fund_treasury(c, a)
    pf.ledger.conn().execute("UPDATE credentials SET payout_email='alice-payout@example.com' WHERE handle='alice@omni'")
    return pf


HDR = {"PAYPAL-TRANSMISSION-SIG": "sig", "PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://x", "PAYPAL-TRANSMISSION-ID": "t", "PAYPAL-TRANSMISSION-TIME": "now"}


def webhook(pf, etype, resource, eid):
    return pf.wallet.handle_webhook(HDR, json.dumps({"id": eid, "event_type": etype, "resource": resource}).encode())


def fund(pf, handle, cur, amt):
    o = pf.wallet.create_funding_order(handle, cur, amt)
    pf.paypal.approve(o["paypal_order_id"])
    return o, pf.wallet.capture_order(handle, o["order_id"])
