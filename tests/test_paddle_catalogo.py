"""tools/paddle_catalogo.py with a fake Paddle API: the pack is created once, the API key is never printed."""
from __future__ import annotations

import io
from typing import Any, Optional

from tools import paddle_catalogo


class FakeCatalogue:
    def __init__(self, products: Optional[list[dict[str, Any]]] = None, prices: Optional[list[dict[str, Any]]] = None):
        self.products = products or []
        self.prices = prices or []
        self.created: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, method: str, path: str, body: Optional[dict[str, Any]]) -> tuple[int, Any]:
        if method == "GET":
            return 200, {"data": self.products if path.startswith("/products") else self.prices}
        self.created.append((path, body or {}))
        if path == "/products":
            return 201, {"data": {"id": "pro_01new", **(body or {})}}
        if path == "/prices":
            return 201, {"data": {"id": "pri_01new", **(body or {})}}
        if method == "PATCH":
            return 200, {"data": body}
        return 201, {"data": {"token": "test_newclienttoken"}}


def run(monkeypatch, tmp_path, fake: FakeCatalogue, *args: str) -> str:
    for name in ("BYS_PADDLE_CLIENT_TOKEN", "BYS_PADDLE_PRICE_PACK", "BYS_PACK_ANALYSES",
                 "BYS_PACK_PRICE_CENTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BYS_PADDLE_API_KEY", "pdl_sdbx_apikey_secret")
    out = io.StringIO()
    assert paddle_catalogo.main(list(args), out=out, call=fake, env_path=tmp_path / ".env") == 0
    return out.getvalue()


def test_first_run_creates_the_pack_of_200_for_9_99_with_taxes_included(monkeypatch, tmp_path):
    fake = FakeCatalogue()
    printed = run(monkeypatch, tmp_path, fake, "--write-env")
    paths = [path for path, _ in fake.created]
    assert paths == ["/products", "/prices", "/client-tokens"]
    product, price = fake.created[0][1], fake.created[1][1]
    assert product["name"] == "Before You Send · 200 análisis" and product["custom_data"] == {"bb1": "bys-200"}
    assert price["unit_price"] == {"amount": "999", "currency_code": "EUR"} and price["tax_mode"] == "internal"
    assert price["quantity"] == {"minimum": 1, "maximum": 1}  # no − 1 + stepper in the checkout
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "BYS_PADDLE_PRICE_PACK=pri_01new" in env and "BYS_PADDLE_CLIENT_TOKEN=test_newclienttoken" in env
    assert "pdl_sdbx_apikey_secret" not in printed + env


def test_second_run_creates_nothing(monkeypatch, tmp_path):
    existing = {"custom_data": {"bb1": "bys-200"}}
    fake = FakeCatalogue(products=[{"id": "pro_01old", **existing}],
                         prices=[{"id": "pri_01old", "unit_price": {"amount": "999"},
                                  "quantity": {"minimum": 1, "maximum": 1}, **existing}])
    monkeypatch.setenv("BYS_PADDLE_CLIENT_TOKEN", "test_existing")
    out = io.StringIO()
    monkeypatch.setenv("BYS_PADDLE_API_KEY", "pdl_sdbx_apikey_secret")
    assert paddle_catalogo.main([], out=out, call=fake, env_path=tmp_path / ".env") == 0
    assert fake.created == []
    assert "BYS_PADDLE_PRICE_PACK=pri_01old" in out.getvalue()


def test_a_price_that_allows_more_packs_is_set_to_one(monkeypatch, tmp_path):
    existing = {"custom_data": {"bb1": "bys-200"}}
    fake = FakeCatalogue(products=[{"id": "pro_01old", **existing}],
                         prices=[{"id": "pri_01old", "unit_price": {"amount": "999"},
                                  "quantity": {"minimum": 1, "maximum": 100}, **existing}])
    monkeypatch.setenv("BYS_PADDLE_CLIENT_TOKEN", "test_existing")
    monkeypatch.setenv("BYS_PADDLE_API_KEY", "pdl_sdbx_apikey_secret")
    assert paddle_catalogo.main([], out=io.StringIO(), call=fake, env_path=tmp_path / ".env") == 0
    assert fake.created == [("/prices/pri_01old", {"quantity": {"minimum": 1, "maximum": 1}})]
