#!/usr/bin/env python3
"""
discover.py — Verificador de plataformas para nuevas tiendas
-------------------------------------------------------------
Lee data/candidates.json (lista de {"name": ..., "domain": ...}) y, para
cada dominio, comprueba en orden:

  1. Shopify      -> https://DOMINIO/products.json?limit=1 devuelve JSON
  2. WooCommerce  -> https://DOMINIO/wp-json/wc/store/v1/products?per_page=1
  3. PrestaShop   -> la portada contiene la firma "prestashop"

Imprime el resultado con el <title> de la web (para que compruebes a ojo
que el dominio es LA TIENDA CORRECTA y no otra con nombre parecido) y
genera en data/discover_result.md las líneas listas para pegar en
data/stores.json.

Uso:  python discover.py
      (o lanza el workflow "Discover store platforms" en la pestaña
      Actions — el resultado sale en el resumen del job)

Las entradas con "domain": "" se ignoran: son la plantilla del Grupo 4
pendiente de rellenar.
"""

import json
import re
import sys
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent
CANDIDATES_FILE = BASE_DIR / "data" / "candidates.json"
RESULT_FILE = BASE_DIR / "data" / "discover_result.md"

TIMEOUT = 10
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PokeStockBot-discover/1.0)"}


def get(session, url):
    try:
        return session.get(url, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        return None


def page_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:80] if m else "(sin título)"


def detect(session, domain: str) -> tuple:
    """Devuelve (plataforma, título_de_la_web)."""
    # 1) Shopify
    r = get(session, f"https://{domain}/products.json?limit=1")
    if r is not None and r.status_code == 200:
        try:
            if "products" in r.json():
                home = get(session, f"https://{domain}")
                return "shopify", page_title(home.text if home else "")
        except ValueError:
            pass

    # 2) WooCommerce (Store API pública)
    r = get(session, f"https://{domain}/wp-json/wc/store/v1/products?per_page=1")
    if r is not None and r.status_code == 200:
        try:
            data = r.json()
            if isinstance(data, list):
                home = get(session, f"https://{domain}")
                return "woocommerce", page_title(home.text if home else "")
        except ValueError:
            pass

    # 3) PrestaShop (firma en la portada)
    home = get(session, f"https://{domain}")
    if home is not None and home.status_code == 200:
        if "prestashop" in home.text.lower():
            return "prestashop", page_title(home.text)
        return "desconocida", page_title(home.text)

    return "sin-respuesta", ""


def main():
    candidates = json.load(open(CANDIDATES_FILE, encoding="utf-8"))
    session = requests.Session()
    session.headers.update(HEADERS)

    ok_lines = []
    report = ["# Resultado de discover.py\n",
              "| Tienda | Dominio | Plataforma | Título de la web (verifica que es la tienda correcta) |",
              "|---|---|---|---|"]

    for c in candidates:
        name, domain = c.get("name", "?"), (c.get("domain") or "").strip()
        if not domain:
            continue
        platform, title = detect(session, domain)
        report.append(f"| {name} | {domain} | **{platform}** | {title} |")
        print(f"{name:25s} {domain:30s} -> {platform:12s} | {title}")

        if platform in ("shopify", "woocommerce", "prestashop"):
            ok_lines.append(
                f'  {{ "name": "{name}", "domain": "{domain}", "platform": "{platform}" }},'
            )

    report.append("\n## Líneas listas para pegar en data/stores.json\n")
    report.append("```json")
    report.extend(ok_lines if ok_lines else ["(ninguna verificada)"])
    report.append("```")
    report.append(
        "\n⚠️ Antes de pegar, comprueba en la columna de títulos que cada "
        "dominio corresponde de verdad a la tienda (y no a otra web con "
        "nombre parecido). Las 'desconocida' necesitan revisión manual; "
        "las 'sin-respuesta' pueden tener anti-bot o el dominio mal."
    )

    RESULT_FILE.write_text("\n".join(report), encoding="utf-8")
    print(f"\n✅ Informe guardado en {RESULT_FILE}")
    print(f"   Verificadas: {len(ok_lines)}")


if __name__ == "__main__":
    sys.exit(main())
