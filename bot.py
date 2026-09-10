#!/usr/bin/env python3
"""
PokéStock Bot (versión open-source / gratuita) — v2 corregida
--------------------------------------------------------------
Cambios respecto a v1:

1. FILTRO DE POKÉMON: solo se avisa de productos cuyo título, tipo,
   tags o categorías contengan alguna palabra clave de POKEMON_KEYWORDS.
   Las tiendas marcadas con "all_pokemon": true en stores.json se
   saltan el filtro (venden solo Pokémon y algunos productos no llevan
   la palabra en el título, ej. "151 Booster Bundle").

2. BASELINE POR PRODUCTO (no global): un producto que el bot ve por
   PRIMERA VEZ se registra en silencio, nunca dispara alerta. Así,
   añadir una tienda nueva (o que una tienda responda por primera vez
   tras fallar) ya no manda su catálogo entero como "restocks".
   Solo hay alerta cuando un producto YA CONOCIDO pasa de agotado a
   disponible. Opcionalmente (NOTIFY_NEW_PRODUCTS = True) se puede
   avisar de productos nuevos en tiendas que ya tenían estado.

3. IDs ESTABLES en WooCommerce: se usa el id numérico de la variación
   en lugar del repr del dict de atributos (que podía cambiar de orden
   entre ejecuciones y provocar falsos restocks).

4. PRESTASHOP: si una página no trae el microdato de disponibilidad
   (tema sin schema.org, página anti-bot, etc.) el producto se OMITE
   en esa ejecución en vez de registrarse como "agotado", evitando el
   flip-flop agotado→disponible que generaba falsas alertas.

5. LIMPIEZA DE ESTADO: se eliminan del state.json las entradas de
   dominios que ya no están en stores.json.
"""

import json
import os
import re
import sys
import time
import unicodedata
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
USER_AGENT = "Mozilla/5.0 (compatible; PokeStockBot/2.0; +https://github.com/)"

# Límite de productos revisados por tienda PrestaShop en cada ejecución.
PRESTASHOP_MAX_PRODUCTS = 150

# Palabras clave (en minúsculas y sin acentos) que identifican un
# producto de Pokémon. Se comparan contra título + tipo + tags +
# categorías, todo normalizado. Añade las que necesites.
POKEMON_KEYWORDS = [
    "pokemon",   # cubre también "pokémon" tras normalizar acentos
    "pokeball",
    "poke ball",
    "pikachu",
    "charizard",
    "eevee",
]

# Si True, avisa también de productos NUEVOS (que aparecen por primera
# vez ya disponibles) en tiendas que ya tenían estado previo. Útil para
# lanzamientos, pero puede generar ruido si una tienda devuelve
# catálogos parciales de forma intermitente. Empieza con False.
NOTIFY_NEW_PRODUCTS = False

# Máximo de alertas por tienda y ejecución (cortafuegos anti-spam por
# si algo sale mal: nunca deberías recibir 200 restocks reales de golpe
# de la misma tienda en 10 minutos).
MAX_ALERTS_PER_STORE = 15


def normalize(text: str) -> str:
    """minúsculas + sin acentos, para comparar palabras clave."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def is_pokemon_item(item: dict, store: dict) -> bool:
    if store.get("all_pokemon"):
        return True
    haystack = normalize(" ".join(filter(None, [
        item.get("product_title", ""),
        item.get("variant_title", ""),
        item.get("extra_text", ""),   # tipo/tags/categorías según plataforma
        item.get("url", ""),
    ])))
    return any(kw in haystack for kw in POKEMON_KEYWORDS)


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
    domain = store["domain"]
    url = f"https://{domain}/products.json"
    headers = {"User-Agent": USER_AGENT}
    items = []

    try:
        page = 1
        while True:
            resp = requests.get(
                url,
                headers=headers,
                params={"limit": 250, "page": page},
                timeout=REQUEST_TIMEOUT,
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
                # tipo y tags ayudan al filtro de Pokémon aunque el
                # título no lleve la palabra
                extra = " ".join(filter(None, [
                    product.get("product_type", ""),
                    " ".join(product.get("tags", []))
                    if isinstance(product.get("tags"), list)
                    else str(product.get("tags", "")),
                ]))
                for variant in product.get("variants", []):
                    items.append(
                        {
                            "id": f"{domain}:{variant['id']}",
                            "store": store["name"],
                            "product_title": title,
                            "variant_title": variant.get("title", ""),
                            "extra_text": extra,
                            "price": variant.get("price"),
                            "available": bool(variant.get("available")),
                            "url": f"https://{domain}/products/{handle}",
                        }
                    )

            page += 1
            if page > 20:  # tope de seguridad (20 * 250 = 5000 productos)
                break
            time.sleep(0.3)

    except requests.RequestException as e:
        print(f"❌ Error consultando {domain}: {e}")

    return items


def fetch_woocommerce_products(store: dict) -> list:
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
                extra = " ".join(
                    c.get("name", "")
                    for c in (product.get("categories") or [])
                ) + " " + " ".join(
                    t.get("name", "")
                    for t in (product.get("tags") or [])
                )

                prices = product.get("prices", {})
                price_raw = prices.get("price")
                minor_unit = prices.get("currency_minor_unit", 2)
                price = None
                if price_raw not in (None, ""):
                    try:
                        price = round(int(price_raw) / (10 ** minor_unit), 2)
                    except (ValueError, TypeError):
                        price = None

                if variations:
                    is_in_stock = bool(product.get("is_in_stock", False))
                    for var in variations:
                        # ID estable: id numérico de la variación (antes
                        # se usaba el repr del dict de atributos, que
                        # podía cambiar de orden → falsos restocks)
                        var_id = var.get("id")
                        if var_id is None:
                            continue
                        items.append(
                            {
                                "id": f"{domain}:{product_id}:{var_id}",
                                "store": store["name"],
                                "product_title": title,
                                "variant_title": ", ".join(
                                    str(a.get("value") or "")
                                    for a in (var.get("attributes") or [])
                                    if a.get("value")
                                ),
                                "extra_text": extra,
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
                            "extra_text": extra,
                            "price": price,
                            "available": bool(product.get("is_in_stock", False)),
                            "url": permalink,
                        }
                    )

            page += 1
            if page > 20:
                break
            time.sleep(0.3)

    except requests.RequestException as e:
        print(f"❌ Error consultando {domain} (WooCommerce): {e}")

    return items


def fetch_prestashop_products(store: dict) -> list:
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

            title_tag = soup.find("meta", property="og:title")
            title = title_tag["content"] if title_tag else (
                soup.h1.get_text(strip=True) if soup.h1 else url
            )

            price = None
            price_tag = soup.find(attrs={"itemprop": "price"})
            if price_tag:
                price_val = price_tag.get("content") or price_tag.get_text(strip=True)
                match = re.search(r"[\d.,]+", price_val or "")
                if match:
                    price = match.group(0).replace(",", ".")

            # Disponibilidad: si NO encontramos el microdato, saltamos el
            # producto en esta ejecución. Registrarlo como "agotado" sin
            # estar seguros provocaba falsos restocks cuando la página se
            # parseaba bien más tarde (flip-flop).
            avail_tag = soup.find(attrs={"itemprop": "availability"})
            if not avail_tag:
                continue
            avail_val = (avail_tag.get("content") or avail_tag.get_text()).lower()
            available = "instock" in avail_val or "in_stock" in avail_val

            items.append(
                {
                    "id": f"{domain}:{url}",
                    "store": store["name"],
                    "product_title": title,
                    "variant_title": "",
                    "extra_text": "",
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
                break

        except (requests.RequestException, ElementTree.ParseError):
            continue

    return urls


def check_store(store: dict, state: dict) -> tuple:
    """Devuelve (restocks, nuevos) detectados para esta tienda.

    - restock: producto YA registrado en el estado que pasa de agotado
      a disponible.
    - nuevo: producto visto por primera vez que ya está disponible, en
      una tienda que YA tenía estado previo (solo se notifica si
      NOTIFY_NEW_PRODUCTS = True).
    Los productos vistos por primera vez en una tienda SIN estado previo
    (tienda recién añadida o que responde por primera vez) se registran
    siempre en silencio.
    """
    restocks = []
    new_products = []
    platform = store.get("platform", "shopify")
    domain = store["domain"]

    if platform == "woocommerce":
        products = fetch_woocommerce_products(store)
    elif platform == "prestashop":
        products = fetch_prestashop_products(store)
    else:
        products = fetch_shopify_products(store)

    # ¿Esta tienda ya tenía algún producto registrado?
    store_prefix = f"{domain}:"
    store_had_state = any(k.startswith(store_prefix) for k in state)

    for item in products:
        item_id = item["id"]
        known = item_id in state
        was_available = state.get(item_id, {}).get("available", False)
        now_available = item["available"]

        if known:
            if now_available and not was_available and is_pokemon_item(item, store):
                restocks.append(item)
        else:
            # Producto nuevo: solo candidato a aviso si la tienda ya
            # tenía baseline y el flag está activado.
            if (
                NOTIFY_NEW_PRODUCTS
                and store_had_state
                and now_available
                and is_pokemon_item(item, store)
            ):
                new_products.append(item)

        state[item_id] = {
            "available": now_available,
            "price": item["price"],
            "product_title": item["product_title"],
        }

    return restocks, new_products


def format_message(item: dict, kind: str = "restock") -> str:
    name = item["product_title"]
    if item["variant_title"] and item["variant_title"] != "Default Title":
        name += f" – {item['variant_title']}"
    price = f"{item['price']} €" if item["price"] else "precio no disponible"
    header = "🔔 <b>RESTOCK</b>" if kind == "restock" else "🆕 <b>NUEVO PRODUCTO</b>"
    return (
        f"{header}\n"
        f"🏪 {item['store']}\n"
        f"📦 {name}\n"
        f"💰 {price}\n"
        f"🔗 {item['url']}"
    )


def prune_state(state: dict, stores: list) -> dict:
    """Elimina entradas de dominios que ya no están en stores.json."""
    active_domains = {s["domain"] for s in stores}
    return {
        k: v for k, v in state.items()
        if k.split(":", 1)[0] in active_domains
    }


def main():
    stores = load_json(STORES_FILE, [])
    if not stores:
        print("⚠️  data/stores.json está vacío. Añade tiendas antes de ejecutar.")
        sys.exit(0)

    state = load_json(STATE_FILE, {})
    state = prune_state(state, stores)

    total_alerts = 0
    for store in stores:
        print(f"🔍 Revisando {store['name']} ({store['domain']})...")
        try:
            restocks, new_products = check_store(store, state)
        except Exception as e:
            # Una tienda con datos raros no debe tumbar el resto
            print(f"❌ Error inesperado en {store['name']}: {e}")
            continue

        alerts = [(item, "restock") for item in restocks]
        alerts += [(item, "new") for item in new_products]

        if len(alerts) > MAX_ALERTS_PER_STORE:
            print(
                f"⚠️  {store['name']}: {len(alerts)} alertas en una sola "
                f"ejecución — se envían solo {MAX_ALERTS_PER_STORE} "
                f"(cortafuegos anti-spam). Revisa si es un falso positivo."
            )
            alerts = alerts[:MAX_ALERTS_PER_STORE]

        for item, kind in alerts:
            send_telegram_message(format_message(item, kind))
            total_alerts += 1
            time.sleep(1)  # rate limit de Telegram

    save_json(STATE_FILE, state)
    print(f"✅ Revisión completa. Alertas enviadas: {total_alerts}")


if __name__ == "__main__":
    main()
