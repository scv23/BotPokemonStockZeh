#!/usr/bin/env python3
"""
PokéStock Bot (versión open-source / gratuita)
------------------------------------------------
Revisa el stock de una lista de tiendas Shopify de Pokémon TCG y publica
en un canal/chat de Telegram cuando un producto pasa de "agotado" a
"disponible" (restock).

Cómo funciona:
1. Lee la lista de tiendas desde data/stores.json (solo dominios Shopify:
   tienen un endpoint público /products.json?limit=250 con el stock real).
2. Para cada tienda, descarga el catálogo y mira el campo "available"
   de cada variante (talla/versión de producto).
3. Compara contra el último estado guardado en data/state.json.
4. Si algo pasó de no-disponible -> disponible, manda un mensaje a Telegram.
5. Guarda el nuevo estado para la siguiente ejecución.

Pensado para ejecutarse periódicamente vía GitHub Actions (gratis),
que hace commit de data/state.json después de cada ejecución para que
el estado persista entre ejecuciones (los runners de GitHub Actions no
tienen disco persistente por sí solos).
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent
STORES_FILE = BASE_DIR / "data" / "stores.json"
STATE_FILE = BASE_DIR / "data" / "state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

REQUEST_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; PokeStockBot/1.0; +https://github.com/)"


def load_json(path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID, no se envía el mensaje:")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            print(f"❌ Error enviando a Telegram: {r.status_code} {r.text}")
    except requests.RequestException as e:
        print(f"❌ Excepción enviando a Telegram: {e}")


def fetch_shopify_products(store: dict) -> list:
    """Descarga el catálogo de una tienda Shopify vía /products.json.
    Devuelve una lista de dicts: {product_title, variant_id, variant_title,
    price, available, url}
    """
    domain = store["domain"]
    url = f"https://{domain}/products.json?limit=250"
    headers = {"User-Agent": USER_AGENT}
    items = []

    try:
        page = 1
        while True:
            resp = requests.get(
                url, headers=headers, params={"page": page}, timeout=REQUEST_TIMEOUT
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            products = data.get("products", [])
            if not products:
                break

            for product in products:
                handle = product.get("handle", "")
                title = product.get("title", "")
                for variant in product.get("variants", []):
                    items.append(
                        {
                            "id": f"{domain}:{variant['id']}",
                            "store": store["name"],
                            "product_title": title,
                            "variant_title": variant.get("title", ""),
                            "price": variant.get("price"),
                            "available": bool(variant.get("available")),
                            "url": f"https://{domain}/products/{handle}",
                        }
                    )

            page += 1
            if page > 20:  # tope de seguridad (20 * 250 = 5000 productos)
                break
            time.sleep(0.3)  # ser educado con el servidor de la tienda

    except requests.RequestException as e:
        print(f"❌ Error consultando {domain}: {e}")

    return items


def check_store(store: dict, state: dict) -> list:
    """Devuelve una lista de restocks nuevos detectados para esta tienda."""
    restocks = []
    products = fetch_shopify_products(store)

    for item in products:
        item_id = item["id"]
        was_available = state.get(item_id, {}).get("available", False)
        now_available = item["available"]

        if now_available and not was_available:
            restocks.append(item)

        state[item_id] = {
            "available": now_available,
            "price": item["price"],
            "product_title": item["product_title"],
        }

    return restocks


def format_message(item: dict) -> str:
    name = item["product_title"]
    if item["variant_title"] and item["variant_title"] != "Default Title":
        name += f" – {item['variant_title']}"
    price = f"{item['price']} €" if item["price"] else "precio no disponible"
    return (
        f"🔔 <b>RESTOCK</b>\n"
        f"🏪 {item['store']}\n"
        f"📦 {name}\n"
        f"💰 {price}\n"
        f"🔗 {item['url']}"
    )


def main():
    stores = load_json(STORES_FILE, [])
    if not stores:
        print("⚠️  data/stores.json está vacío. Añade tiendas antes de ejecutar.")
        sys.exit(0)

    state = load_json(STATE_FILE, {})
    is_first_run = len(state) == 0

    total_restocks = 0
    for store in stores:
        print(f"🔍 Revisando {store['name']} ({store['domain']})...")
        restocks = check_store(store, state)
        total_restocks += len(restocks)

        # En la primera ejecución no avisamos de "restocks" (sería todo el
        # catálogo entero), solo construimos el estado inicial.
        if not is_first_run:
            for item in restocks:
                send_telegram_message(format_message(item))
                time.sleep(1)  # evitar el rate limit de Telegram

    save_json(STATE_FILE, state)

    if is_first_run:
        print(f"✅ Estado inicial guardado ({len(state)} variantes). "
              f"A partir de la próxima ejecución se avisará de restocks reales.")
    else:
        print(f"✅ Revisión completa. Restocks detectados: {total_restocks}")


if __name__ == "__main__":
    main()
