#!/usr/bin/env python3
"""
PokéStock Bot (versión open-source / gratuita)
------------------------------------------------
Revisa el stock de tiendas de Pokémon TCG (Shopify, WooCommerce y
PrestaShop) y publica en un canal/chat de Telegram cuando un producto
pasa de "agotado" a "disponible" (restock).

Cómo funciona:
1. Lee la lista de tiendas desde data/stores.json. Cada tienda tiene un
   campo "platform": "shopify" | "woocommerce" | "prestashop".
2. Según la plataforma, usa un método distinto para leer el stock:
   - shopify: endpoint público /products.json?limit=250
   - woocommerce: endpoint público /wp-json/wc/store/v1/products
     (API de la "Store API" de WooCommerce Blocks, sin autenticación)
   - prestashop: descubre URLs de producto vía sitemap.xml y lee el
     microdato schema.org "availability" de cada página de producto
     (más lento: una petición por producto, con límite de seguridad)
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
import re
import sys
import time
from pathlib import Path
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
STORES_FILE = BASE_DIR / "data" / "stores.json"
STATE_FILE = BASE_DIR / "data" / "state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

REQUEST_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; PokeStockBot/1.0; +https://github.com/)"

# Límite de productos que se revisan por tienda PrestaShop en cada
# ejecución (es lento porque es 1 petición HTTP por producto). Ajusta
# si tienes tiempo de sobra en tu runner.
PRESTASHOP_MAX_PRODUCTS = 150


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


def fetch_woocommerce_products(store: dict) -> list:
    """Descarga el catálogo de una tienda WooCommerce vía la Store API
    pública (/wp-json/wc/store/v1/products). No requiere API key: es la
    misma API que usa el propio carrito/bloques de WooCommerce en la
    tienda. Si la tienda la tiene desactivada, esto no devolverá nada.
    """
    domain = store["domain"]
    base_url = f"https://{domain}/wp-json/wc/store/v1/products"
    headers = {"User-Agent": USER_AGENT}
    items = []

    try:
        page = 1
        while True:
            resp = requests.get(
                base_url,
                headers=headers,
                params={"per_page": 100, "page": page},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                break
            products = resp.json()
            if not isinstance(products, list) or not products:
                break

            for product in products:
                product_id = product.get("id")
                title = product.get("name", "")
                permalink = product.get("permalink", "")
                variations = product.get("variations") or []
                prices = product.get("prices", {})
                price_raw = prices.get("price")
                # WooCommerce da el precio en la unidad mínima (céntimos)
                # multiplicado por 10^minor_unit; lo normalizamos a euros.
                minor_unit = prices.get("currency_minor_unit", 2)
                price = None
                if price_raw not in (None, ""):
                    try:
                        price = round(int(price_raw) / (10 ** minor_unit), 2)
                    except (ValueError, TypeError):
                        price = None

                if variations:
                    # Producto con variaciones: cada una puede tener su
                    # propio stock. La Store API de listado no siempre
                    # trae el stock por variación, así que usamos el
                    # estado general del producto como aproximación.
                    is_in_stock = bool(product.get("is_in_stock", False))
                    for var in variations:
                        items.append(
                            {
                                "id": f"{domain}:{product_id}:{var.get('attributes')}",
                                "store": store["name"],
                                "product_title": title,
                                "variant_title": ", ".join(
                                    a.get("value", "")
                                    for a in (var.get("attributes") or [])
                                ),
                                "price": price,
                                "available": is_in_stock,
                                "url": permalink,
                            }
                        )
                else:
                    items.append(
                        {
                            "id": f"{domain}:{product_id}",
                            "store": store["name"],
                            "product_title": title,
                            "variant_title": "",
                            "price": price,
                            "available": bool(product.get("is_in_stock", False)),
                            "url": permalink,
                        }
                    )

            page += 1
            if page > 20:  # tope de seguridad
                break
            time.sleep(0.3)

    except requests.RequestException as e:
        print(f"❌ Error consultando {domain} (WooCommerce): {e}")

    return items


def fetch_prestashop_products(store: dict) -> list:
    """Para PrestaShop no hay endpoint público de stock sin API key, así
    que: 1) leemos el sitemap.xml para sacar URLs de producto, y 2)
    visitamos cada página y leemos el microdato schema.org "availability"
    (lo usan casi todos los temas de PrestaShop por SEO). Más lento y
    más frágil que Shopify/WooCommerce: 1 petición HTTP por producto.
    """
    domain = store["domain"]
    headers = {"User-Agent": USER_AGENT}
    items = []

    product_urls = _discover_prestashop_product_urls(domain, headers)
    product_urls = product_urls[:PRESTASHOP_MAX_PRODUCTS]

    for url in product_urls:
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")

            # Título: og:title o <h1>
            title_tag = soup.find("meta", property="og:title")
            title = title_tag["content"] if title_tag else (
                soup.h1.get_text(strip=True) if soup.h1 else url
            )

            # Precio: meta itemprop="price" (schema.org Offer)
            price = None
            price_tag = soup.find(attrs={"itemprop": "price"})
            if price_tag:
                price_val = price_tag.get("content") or price_tag.get_text(strip=True)
                match = re.search(r"[\d.,]+", price_val or "")
                if match:
                    price = match.group(0).replace(",", ".")

            # Disponibilidad: meta itemprop="availability" (schema.org)
            available = False
            avail_tag = soup.find(attrs={"itemprop": "availability"})
            if avail_tag:
                avail_val = (avail_tag.get("content") or avail_tag.get_text()).lower()
                available = "instock" in avail_val or "in_stock" in avail_val

            items.append(
                {
                    "id": f"{domain}:{url}",
                    "store": store["name"],
                    "product_title": title,
                    "variant_title": "",
                    "price": price,
                    "available": available,
                    "url": url,
                }
            )
            time.sleep(0.3)

        except requests.RequestException as e:
            print(f"❌ Error consultando producto de {domain}: {e}")

    return items


def _discover_prestashop_product_urls(domain: str, headers: dict) -> list:
    """Busca URLs de producto en el/los sitemap.xml de la tienda."""
    urls = []
    sitemap_candidates = [
        f"https://{domain}/sitemap.xml",
        f"https://{domain}/sitemap_index.xml",
    ]

    for sitemap_url in sitemap_candidates:
        try:
            resp = requests.get(sitemap_url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue
            root = ElementTree.fromstring(resp.content)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

            # Si es un índice de sitemaps, entra en los que parezcan de productos
            sub_sitemaps = [
                loc.text for loc in root.findall(".//sm:sitemap/sm:loc", ns)
            ]
            if sub_sitemaps:
                for sub_url in sub_sitemaps:
                    if not sub_url or "product" not in sub_url.lower():
                        continue
                    try:
                        sub_resp = requests.get(
                            sub_url, headers=headers, timeout=REQUEST_TIMEOUT
                        )
                        sub_root = ElementTree.fromstring(sub_resp.content)
                        urls.extend(
                            loc.text
                            for loc in sub_root.findall(".//sm:url/sm:loc", ns)
                            if loc.text
                        )
                    except (requests.RequestException, ElementTree.ParseError):
                        continue
            else:
                urls.extend(
                    loc.text for loc in root.findall(".//sm:url/sm:loc", ns) if loc.text
                )

            if urls:
                break  # ya encontramos un sitemap que funciona

        except (requests.RequestException, ElementTree.ParseError):
            continue

    return urls


def check_store(store: dict, state: dict) -> list:
    """Devuelve una lista de restocks nuevos detectados para esta tienda."""
    restocks = []
    platform = store.get("platform", "shopify")

    if platform == "woocommerce":
        products = fetch_woocommerce_products(store)
    elif platform == "prestashop":
        products = fetch_prestashop_products(store)
    else:
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
