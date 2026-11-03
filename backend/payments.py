"""The pack of analyses, paid with Paddle, which sells it as Merchant of Record: it charges, applies and pays the taxes
of every country and sends the receipt. The app opens Paddle's checkout over the page, so card data never reach us.

After paying, the app sends the transaction id. The server asks Paddle for that transaction and adds the analyses only
when it is paid, at the price of the pack and for the code that asked (custom data). Each transaction adds them once
(table payments, backend/codes.py). The app keeps the id until the server has answered, so a page closed right after
paying confirms at the next opening; Paddle's signed webhook (/api/paddle/webhook, with the public address) adds them
even if the app is never opened again. The same code as backend/payments.py of Plantatam (3/11/2026); the API key
stays on the server and never reaches the logs.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from .config import Settings

log = logging.getLogger("bys.payments")

API_URLS = {"sandbox": "https://sandbox-api.paddle.com", "live": "https://api.paddle.com"}
PAID_STATUSES = ("paid", "completed")
PENDING_STATUSES = ("draft", "ready", "billed")
CODE_FIELD = "bys_code"  # the id of the access code in the custom data of the checkout
TRANSACTION_ID = re.compile(r"txn_[a-z0-9]{10,60}")
WEBHOOK_TOLERANCE_SECONDS = 300  # older signatures are refused: a captured notification cannot be replayed later

# (url, headers, timeout) -> (HTTP status, decoded JSON body or None)
Fetch = Callable[[str, Mapping[str, str], float], tuple[int, Optional[dict[str, Any]]]]


class PaymentUnavailable(Exception):
    """Paddle is not configured, could not be reached, or refused our key: try again later."""


@dataclass(frozen=True)
class PaymentCheck:
    status: str  # "paid", "pending" or "mismatch"
    reason: str = ""  # for the logs only
    packs: int = 1  # how many packs the transaction pays for (the checkout shows no quantity, but Paddle could)


def get_json(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, Optional[dict[str, Any]]]:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            body = None
        return exc.code, body


def transaction_prices(transaction: Mapping[str, Any]) -> set[str]:
    prices = set()
    for item in transaction.get("items") or []:
        price = item.get("price") if isinstance(item.get("price"), dict) else {}
        prices.update(value for value in (item.get("price_id"), price.get("id")) if value)
    return prices


def packs_in(transaction: Mapping[str, Any], price_id: str) -> int:
    """The quantity of the pack in the transaction: each pack adds its analyses."""
    packs = 0
    for item in transaction.get("items") or []:
        price = item.get("price") if isinstance(item.get("price"), dict) else {}
        if price_id in (item.get("price_id"), price.get("id")):
            quantity = item.get("quantity", 1)
            packs += quantity if isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0 else 1
    return packs


class Paddle:
    def __init__(
        self,
        *,
        environment: str,
        api_key: str,
        client_token: str,
        price_id: str,
        analyses: int,
        price_cents: int,
        currency: str,
        webhook_secret: str = "",
        timeout_seconds: float = 15.0,
        fetch: Optional[Fetch] = None,
    ) -> None:
        self.environment = environment
        self.client_token = client_token
        self.price_id = price_id
        self.analyses = analyses
        self.price_cents = price_cents
        self.currency = currency
        self._api_key = api_key
        self._webhook_secret = webhook_secret
        self._timeout = timeout_seconds
        self._fetch = fetch or get_json

    @classmethod
    def from_settings(cls, settings: Settings, fetch: Optional[Fetch] = None) -> Paddle:
        return cls(
            environment=settings.paddle_environment,
            api_key=settings.paddle_api_key,
            client_token=settings.paddle_client_token,
            price_id=settings.paddle_price_pack,
            analyses=settings.pack_analyses,
            price_cents=settings.pack_price_cents,
            currency=settings.currency,
            webhook_secret=settings.paddle_webhook_secret,
            timeout_seconds=settings.paddle_timeout_seconds,
            fetch=fetch,
        )

    @property
    def enabled(self) -> bool:
        return bool(self._api_key and self.client_token and self.price_id)

    def public_config(self) -> dict[str, Any]:
        """What the app needs to show the pack and open Paddle.js: all of it is public."""
        if not self.enabled:
            return {"enabled": False}
        return {
            "enabled": True,
            "environment": self.environment,
            "clientToken": self.client_token,
            "priceId": self.price_id,
            "analyses": self.analyses,
            "priceCents": self.price_cents,
            "currency": self.currency,
        }

    def transaction(self, transaction_id: str) -> Optional[dict[str, Any]]:
        """The transaction as Paddle sees it, or None when Paddle does not know it."""
        if not self.enabled:
            raise PaymentUnavailable("Paddle is not configured")
        url = f"{API_URLS[self.environment]}/transactions/{urllib.parse.quote(transaction_id)}"
        headers = {"Authorization": f"Bearer {self._api_key}", "Paddle-Version": "1", "Accept": "application/json"}
        try:
            status, body = self._fetch(url, headers, self._timeout)
        except (OSError, ValueError) as exc:  # network errors, timeouts, a body that is not JSON
            log.warning("paddle unreachable: %s", type(exc).__name__)
            raise PaymentUnavailable("Paddle could not be reached") from None
        if status == 404:
            return None
        if status != 200 or not isinstance(body, dict) or not isinstance(body.get("data"), dict):
            code = ((body or {}).get("error") or {}).get("code", "") if isinstance(body, dict) else ""
            log.warning("paddle answered %s %s for a transaction check", status, code)
            raise PaymentUnavailable(f"Paddle answered {status}")
        return body["data"]

    def check(self, transaction: Mapping[str, Any], *, code_id: int) -> PaymentCheck:
        """Whether this transaction pays for a pack of this code: the code in the custom data, the pack price, paid."""
        if str((transaction.get("custom_data") or {}).get(CODE_FIELD, "")) != str(code_id):
            return PaymentCheck("mismatch", "another code")
        if self.price_id not in transaction_prices(transaction):
            return PaymentCheck("mismatch", "another price")
        status = transaction.get("status")
        if status in PAID_STATUSES:
            return PaymentCheck("paid", packs=packs_in(transaction, self.price_id))
        if status in PENDING_STATUSES:
            return PaymentCheck("pending", str(status))
        return PaymentCheck("mismatch", f"status {status}")

    def verify_webhook(self, body: bytes, signature: Optional[str], now: Optional[float] = None) -> bool:
        """Paddle-Signature: ts=<seconds>;h1=<HMAC-SHA256 of "ts:body"> (two h1 while the secret is being rotated)."""
        if not self._webhook_secret or not signature:
            return False
        stamp, signatures = "", []
        for part in signature.split(";"):
            key, _, value = part.strip().partition("=")
            if key == "ts":
                stamp = value
            elif key == "h1":
                signatures.append(value)
        if not stamp.isdigit() or not signatures:
            return False
        if abs((time.time() if now is None else now) - int(stamp)) > WEBHOOK_TOLERANCE_SECONDS:
            return False
        expected = hmac.new(self._webhook_secret.encode("utf-8"), stamp.encode("ascii") + b":" + body, hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, candidate) for candidate in signatures)
