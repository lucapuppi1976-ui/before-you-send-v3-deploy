"""Paddle (sandbox in the test copy, live once Paddle approves bys.bb1studio.com): create what Before You Send sells,
with the API key of .env.

- One product with a one-time price, taxes included: the pack of 200 analyses for 9,99 € (Luca, 22/10/2026). It is
  recognised by custom_data.bb1 («bys-200», as the sandbox catalogue made on 22/10), so a second run creates nothing.
- A client-side token for Paddle.js, when .env has none.

Same Paddle account as Plantatam and Vistobueno: its default payment link is already set for the account, so this
script leaves it alone. It prints the lines for .env (price id and client token are public by nature); with
--write-env it writes them into .env. The API key is never printed. The same script as Plantatam's (3/11/2026).
  python tools/paddle_catalogo.py [--write-env]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional, TextIO

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import ConfigError, Settings, load_env_file, load_settings  # noqa: E402
from backend.payments import API_URLS  # noqa: E402

KEY = "bys-200"  # as the sandbox catalogue of 22/10/2026
PRICE_ENV = "BYS_PADDLE_PRICE_PACK"
TOKEN_ENV = "BYS_PADDLE_CLIENT_TOKEN"
# One pack per checkout: without a maximum of 1 Paddle shows a − 1 + stepper next to the pack (seen on 1/11).
ONE_PACK = {"minimum": 1, "maximum": 1}

# (method, path, body) -> (HTTP status, decoded JSON body or None)
Call = Callable[[str, str, Optional[dict[str, Any]]], tuple[int, Optional[dict[str, Any]]]]


def catalogue(settings: Settings) -> dict[str, dict[str, Any]]:
    n = settings.pack_analyses
    return {
        "product": {"name": f"Before You Send · {n} análisis",
                    "description": f"{n} análisis de mensajes en la app Before You Send. Pago único, sin "
                                   "suscripción: los análisis se suman a tu código y no caducan."},
        "price": {"name": f"{n} análisis", "description": f"Paquete de {n} análisis de Before You Send, pago único"},
    }


def http_call(base_url: str, api_key: str) -> Call:
    def call(method: str, path: str, body: Optional[dict[str, Any]] = None) -> tuple[int, Optional[dict[str, Any]]]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(f"{base_url}{path}", data=data, method=method, headers={
            "Authorization": f"Bearer {api_key}", "Paddle-Version": "1", "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            except ValueError:
                return exc.code, None

    return call


def problem(status: int, body: Optional[dict[str, Any]]) -> str:
    error = (body or {}).get("error") or {}
    return f"{status} {error.get('code', '')}: {error.get('detail', '')}".strip()


def ours(items: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    return next((item for item in items if (item.get("custom_data") or {}).get("bb1") == KEY), None)


def write_env(path: Path, values: dict[str, str]) -> None:
    """Replace the given keys in .env, or add them at the end, keeping every other line as it is."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    done = set()
    for index, line in enumerate(lines):
        name = line.split("=", 1)[0].strip()
        if name in values and not line.lstrip().startswith("#"):
            lines[index] = f"{name}={values[name]}"
            done.add(name)
    lines += [f"{name}={value}" for name, value in values.items() if name not in done]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[list[str]] = None, out: TextIO = sys.stdout, call: Optional[Call] = None,
         env_path: Path = ROOT / ".env") -> int:
    parser = argparse.ArgumentParser(prog="paddle_catalogo.py", description="Create the Before You Send pack on Paddle.")
    parser.add_argument("--write-env", action="store_true", help="write the price id and the client token into .env")
    args = parser.parse_args(argv)
    load_env_file(env_path)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if call is None:
        if not settings.paddle_api_key:
            print("error: BYS_PADDLE_API_KEY is not set in .env", file=sys.stderr)
            return 2
        call = http_call(API_URLS[settings.paddle_environment], settings.paddle_api_key)
    print(f"Paddle {settings.paddle_environment}", file=out)
    items = catalogue(settings)
    cents = str(settings.pack_price_cents)

    status, body = call("GET", "/products?status=active&per_page=200", None)
    if status != 200:
        print(f"error: products {problem(status, body)}", file=sys.stderr)
        return 1
    product = ours(body.get("data", []))
    status, body = call("GET", "/prices?status=active&per_page=200", None)
    if status != 200:
        print(f"error: prices {problem(status, body)}", file=sys.stderr)
        return 1
    price = ours(body.get("data", []))

    if product is None:
        status, body = call("POST", "/products", {**items["product"], "tax_category": "standard",
                                                   "custom_data": {"bb1": KEY}})
        if status not in (200, 201):
            print(f"error: product {problem(status, body)}", file=sys.stderr)
            return 1
        product = body["data"]
        print(f"  product created: {product['id']} {items['product']['name']}", file=out)
    if price is None:
        status, body = call("POST", "/prices", {
            **items["price"], "product_id": product["id"], "tax_mode": "internal", "quantity": ONE_PACK,
            "unit_price": {"amount": cents, "currency_code": settings.currency}, "custom_data": {"bb1": KEY},
        })
        if status not in (200, 201):
            print(f"error: price {problem(status, body)}", file=sys.stderr)
            return 1
        price = body["data"]
        print(f"  price created: {price['id']} {int(cents) / 100:.2f} {settings.currency}, taxes included", file=out)
    else:
        if (price.get("unit_price") or {}).get("amount") != cents:
            print(f"  warning: {price['id']} costs {price['unit_price']['amount']} cents on Paddle, the app shows "
                  f"{cents}", file=out)
        if (price.get("quantity") or {}).get("maximum") != 1:
            status, body = call("PATCH", f"/prices/{price['id']}", {"quantity": ONE_PACK})
            if status != 200:
                print(f"error: price quantity {problem(status, body)}", file=sys.stderr)
                return 1
            print(f"  price {price['id']}: one pack per checkout", file=out)
    values = {PRICE_ENV: price["id"]}

    if settings.paddle_client_token:
        print("  client-side token: already in .env", file=out)
    else:
        status, body = call("POST", "/client-tokens", {"name": "Before You Send app",
                                                        "description": "Paddle.js in the Before You Send app"})
        if status not in (200, 201):
            print(f"error: client-side token {problem(status, body)}", file=sys.stderr)
            return 1
        values[TOKEN_ENV] = body["data"]["token"]
        print("  client-side token created", file=out)

    if args.write_env:
        write_env(env_path, values)
        print(f"Written into .env: {', '.join(values)}", file=out)
    else:
        print("For .env:", file=out)
        for name, value in values.items():
            print(f"{name}={value}", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
