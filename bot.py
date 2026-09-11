#!/usr/bin/env python3
"""
PokéStock Bot (versión open-source / gratuita) — v3 optimizada
---------------------------------------------------------------
Cambios de rendimiento respecto a v2 (de ~35 min a ~2-3 min por run):

A. TIENDAS EN PARALELO: las 66 tiendas se consultan con un pool de
   hilos (MAX_WORKERS a la vez). Cada tienda sigue siendo secuencial
   por dentro, así que ningún servidor individual recibe ráfagas.

B. PRE-FILTRO DE URLs EN PRESTASHOP: en vez de visitar hasta 150
   páginas de producto por tienda (el 90% del tiempo de la v2), se
   filtran las URLs del sitemap por palabras clave de Pokémon y solo
   se visitan esas. Las tiendas con "all_pokemon": true no filtran
   por URL (todo su catálogo es Pokémon) pero mantienen el límite.

C. SESIONES HTTP (keep-alive) y timeout 15s -> 10s.

La lógica de detección es la misma de la v2:
- Filtro de Pokémon por palabras clave en todas las tiendas.
- Baseline por producto: lo nunca visto se registra en silencio.
- IDs estables en WooCommerce (id numérico de variación).
- PrestaShop omite productos sin microdato de disponibilidad.
- Cortafuegos MAX_ALERTS_PER_STORE.
- try/except por tienda: una tienda rota no tumba el resto.
"""

import json
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
STORES_FILE = BASE_DIR / "data" / "stores.json"
STATE_FILE = BASE_DIR / "data" / "state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

REQUEST_TIMEOUT = 10
USER_AGENT = "Mozilla/5.0 (compatible; PokeStockBot/3.0; +https://github.com/)"

# Tiendas consultadas en paralelo. 12 es un buen equilibrio en un
# runner de GitHub Actions (2 vCPU, tarea 100% de red).
MAX_WORKERS = 12

# Límite de páginas de producto visitadas por tienda PrestaShop y por
# ejecución, DESPUÉS de filtrar las URLs por palabras clave.
PRESTASHOP_MAX_PRODUCTS = 60

# Pausa entre peticiones consecutivas a la MISMA tienda (cortesía).
POLITE_SLEEP = 0.15

# Palabras clave (en minúsculas y sin acentos) que identifican un
# producto de Pokémon. Se comparan contra título + tipo + tags +
# categorías + URL, todo normalizado. Añade las que necesites.
POKEMON_KEYWORDS = [
    "pokemon",   # cubre también "pokémon" tras normalizar acentos
    "pokeball",
    "poke-ball",
    "poke ball",
    "pikachu",
    "charizard",
    "eevee",
]

# Si True, avisa también de productos NUEVOS (que aparecen por primera
# vez ya disponibles) en tiendas que ya tenían estado previo.
NOTIFY_NEW_PRODUCTS = False

# Máximo de alertas por tienda y ejecución (cortafuegos anti-spam).
MAX_ALERTS_PER_STORE = 15


def normalize(text: str) -> str:
    """minúsculas + sin acentos, para comparar palabras clave."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def matches_keywords(text: str) -> bool:
    norm = normalize(text)
    return any(kw in norm for kw in POKEMON_KEYWORDS)


def is_pokemon_item(item: dict, store: dict) -> bool:
    if store.get("all_pokemon"):
        return True
    # En tiendas "generic" las URLs las eliges tú a mano: no filtramos.
    if store.get("platform") == "generic":
        return True
    return matches_keywords(" ".join(filter(None, [
        item.get("product_title", ""),
        item.get("variant_title", ""),
        item.get("extra_text", ""),
        item.get("url", ""),
    ])))


def load_json(path, default):
    """Carga JSON con tolerancia a corrupción: si el fichero no parsea
    (p. ej. marcadores de conflicto de git commiteados por error), se
    aparta a .corrupt y se devuelve el valor por defecto. Gracias al
    baseline por producto, un estado vacío se reconstruye en silencio
    sin avalancha de alertas."""
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        backup = path.with_suffix(path.suffix + ".corrupt")
        try:
            path.replace(backup)
            print(f"⚠️  {path.name} corrupto ({e}). Apartado a {backup.name}; "
                  f"se reconstruye el estado desde cero en silencio.")
        except OSError:
            print(f"⚠️  {path.name} corrupto ({e}). Se ignora y se reconstruye.")
        return default


def save_json(path, data):
    """Escritura atómica: primero a un temporal y luego os.replace, para
    que un proceso interrumpido a mitad nunca deje un fichero truncado."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


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


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def fetch_shopify_products(store: dict, session: requests.Session) -> list:
    domain = store["domain"]
    url = f"https://{domain}/products.json"
    items = []

    try:
        page = 1
        while True:
            resp = session.get(
                url,
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
            time.sleep(POLITE_SLEEP)

    except requests.RequestException as e:
        print(f"❌ Error consultando {domain}: {e}")

    return items


def fetch_woocommerce_products(store: dict, session: requests.Session) -> list:
    domain = store["domain"]
    base_url = f"https://{domain}/wp-json/wc/store/v1/products"
    items = []

    try:
        page = 1
        while True:
            resp = session.get(
                base_url,
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
            time.sleep(POLITE_SLEEP)

    except requests.RequestException as e:
        print(f"❌ Error consultando {domain} (WooCommerce): {e}")

    return items


def fetch_prestashop_products(store: dict, session: requests.Session) -> list:
    """OPTIMIZACIÓN CLAVE: las URLs de producto de PrestaShop llevan el
    nombre del producto, así que filtramos por palabras clave ANTES de
    visitarlas. De ~150 peticiones por tienda pasamos a las 10-40 que
    de verdad son de Pokémon."""
    domain = store["domain"]
    items = []

    product_urls = _discover_prestashop_product_urls(domain, session)

    # Pre-filtro por URL (salvo tiendas 100% Pokémon, que no lo necesitan)
    if not store.get("all_pokemon"):
        product_urls = [u for u in product_urls if matches_keywords(u)]

    product_urls = product_urls[:PRESTASHOP_MAX_PRODUCTS]

    for url in product_urls:
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
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

            # Sin microdato de disponibilidad -> omitimos el producto en
            # esta ejecución (evita el flip-flop agotado/disponible).
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
            time.sleep(POLITE_SLEEP)

        except requests.RequestException as e:
            print(f"❌ Error consultando producto de {domain}: {e}")

    return items


def _discover_prestashop_product_urls(domain: str, session: requests.Session) -> list:
    urls = []
    sitemap_candidates = [
        f"https://{domain}/sitemap.xml",
        f"https://{domain}/sitemap_index.xml",
    ]

    for sitemap_url in sitemap_candidates:
        try:
            resp = session.get(sitemap_url, timeout=REQUEST_TIMEOUT)
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
                        sub_resp = session.get(sub_url, timeout=REQUEST_TIMEOUT)
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


def fetch_generic_products(store: dict, session: requests.Session) -> list:
    """Plataforma "generic": para tiendas sin catálogo consultable
    (grandes superficies como El Corte Inglés, Carrefour, GAME...).
    Vigila una lista FIJA de URLs de producto definida en stores.json:

      { "name": "GAME", "domain": "game.es", "platform": "generic",
        "urls": ["https://www.game.es/...producto1...", "..."] }

    De cada URL lee el JSON-LD schema.org (script application/ld+json,
    @type Product) y, si no lo hay, el microdato itemprop. Es el método
    más universal, pero estas webs tienen anti-bot: espera 403s desde
    GitHub Actions (desde una IP residencial funciona mejor).
    """
    domain = store["domain"]
    items = []

    for url in store.get("urls", []):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                print(f"⚠️  {store['name']}: HTTP {resp.status_code} en {url}")
                continue
            soup = BeautifulSoup(resp.text, "html.parser")

            title, price, available = None, None, None

            # 1) JSON-LD (application/ld+json con @type Product)
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")
                except (json.JSONDecodeError, TypeError):
                    continue
                candidates = data if isinstance(data, list) else [data]
                # a veces viene envuelto en @graph
                for c in list(candidates):
                    if isinstance(c, dict) and "@graph" in c:
                        candidates.extend(c["@graph"])
                for c in candidates:
                    if not isinstance(c, dict):
                        continue
                    if str(c.get("@type", "")).lower() != "product":
                        continue
                    title = c.get("name") or title
                    offers = c.get("offers") or {}
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    price = offers.get("price") or price
                    avail = str(offers.get("availability", "")).lower()
                    if avail:
                        available = "instock" in avail.replace("_", "")
                    break
                if title is not None:
                    break

            # 2) Fallback: microdato itemprop (como en PrestaShop)
            if available is None:
                avail_tag = soup.find(attrs={"itemprop": "availability"})
                if avail_tag:
                    avail_val = (avail_tag.get("content") or avail_tag.get_text()).lower()
                    available = "instock" in avail_val.replace("_", "")
                if not title:
                    title_tag = soup.find("meta", property="og:title")
                    title = title_tag["content"] if title_tag else (
                        soup.h1.get_text(strip=True) if soup.h1 else url
                    )
                if price is None:
                    price_tag = soup.find(attrs={"itemprop": "price"})
                    if price_tag:
                        price_val = price_tag.get("content") or price_tag.get_text(strip=True)
                        match = re.search(r"[\d.,]+", price_val or "")
                        if match:
                            price = match.group(0).replace(",", ".")

            # Sin dato de disponibilidad -> omitimos (mismo criterio que
            # PrestaShop: nunca registrar "agotado" sin estar seguros).
            if available is None:
                print(f"⚠️  {store['name']}: sin datos de disponibilidad en {url} "
                      f"(¿página anti-bot?)")
                continue

            items.append(
                {
                    "id": f"{domain}:{url}",
                    "store": store["name"],
                    "product_title": title or url,
                    "variant_title": "",
                    "extra_text": "",
                    "price": price,
                    "available": available,
                    "url": url,
                }
            )
            time.sleep(POLITE_SLEEP)

        except requests.RequestException as e:
            print(f"❌ Error consultando {url}: {e}")

    return items


def fetch_store(store: dict) -> list:
    """Descarga el catálogo de una tienda (se ejecuta en un hilo del
    pool). Solo hace RED, no toca el estado compartido."""
    platform = store.get("platform", "shopify")
    session = make_session()
    try:
        if platform == "woocommerce":
            return fetch_woocommerce_products(store, session)
        if platform == "prestashop":
            return fetch_prestashop_products(store, session)
        if platform == "generic":
            return fetch_generic_products(store, session)
        return fetch_shopify_products(store, session)
    finally:
        session.close()


def diff_against_state(store: dict, products: list, state: dict) -> tuple:
    """Compara el catálogo descargado con el estado y devuelve
    (restocks, nuevos). Muta `state`. Se ejecuta SIEMPRE en el hilo
    principal, así que no necesita locks."""
    restocks = []
    new_products = []
    domain = store["domain"]

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
    t0 = time.monotonic()
    stores = load_json(STORES_FILE, [])
    if not stores:
        print("⚠️  data/stores.json está vacío. Añade tiendas antes de ejecutar.")
        sys.exit(0)

    state = load_json(STATE_FILE, {})
    state = prune_state(state, stores)

    # FASE 1 (paralela): descargar los catálogos de todas las tiendas.
    results = {}  # domain -> lista de productos
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_store, store): store for store in stores}
        for future in as_completed(futures):
            store = futures[future]
            try:
                products = future.result()
                results[store["domain"]] = products
                print(f"✅ {store['name']}: {len(products)} productos/variantes")
            except Exception as e:
                print(f"❌ Error inesperado en {store['name']}: {e}")

    # FASE 2 (secuencial): comparar con el estado y enviar alertas.
    total_alerts = 0
    for store in stores:
        products = results.get(store["domain"])
        if not products:
            continue  # tienda caída o vacía: no tocamos su estado

        restocks, new_products = diff_against_state(store, products, state)

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
    elapsed = time.monotonic() - t0
    print(f"✅ Revisión completa en {elapsed:.0f}s. Alertas enviadas: {total_alerts}")


if __name__ == "__main__":
    main()
