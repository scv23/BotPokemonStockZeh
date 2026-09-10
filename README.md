# PokéStock Bot (versión gratuita, open-source)

Bot de restocks de Pokémon TCG para Telegram, inspirado en StockTCG /
@pokestockES_bot, pero gratis y auto-alojado en GitHub Actions.

**Cobertura de esta versión:** tiendas Shopify (tienen stock en tiempo real
vía su endpoint público `/products.json`, sin scraping frágil ni bloqueos
anti-bot). Es un punto de partida sólido y gratuito — la lista de tiendas
en `data/stores.json` se puede ampliar sin tocar el código.

## 1. Crear el bot de Telegram

1. Habla con [@BotFather](https://t.me/BotFather) en Telegram.
2. Envía `/newbot`, elige un nombre y un username (debe acabar en `bot`).
3. Guarda el **token** que te da (algo como `123456789:AAExxxxxxx...`).

## 2. Crear el canal donde llegarán las alertas

1. Crea un canal nuevo en Telegram (puede ser privado o público).
2. Añade tu bot como **administrador** del canal.
3. Para saber el `chat_id` del canal:
   - Si el canal es público (tiene @usuario), puedes usar directamente
     `@tu_canal` como chat_id.
   - Si es privado, manda cualquier mensaje al canal y visita:
     `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
     Ahí verás un `"chat":{"id":-1001234567890,...}` — ese número
     (con el signo negativo) es tu chat_id.

## 3. Subir este proyecto a GitHub

1. Crea un repositorio nuevo en GitHub (puede ser público, así los
   Actions son gratis e ilimitados).
2. Sube estos archivos (`bot.py`, `requirements.txt`, `data/`,
   `.github/workflows/check_stock.yml`).

## 4. Configurar los secretos

En tu repo de GitHub: **Settings → Secrets and variables → Actions →
New repository secret**, añade:

- `TELEGRAM_BOT_TOKEN` → el token de BotFather
- `TELEGRAM_CHAT_ID` → el chat_id o @usuario del canal

## 5. Activar el workflow

El workflow ya está programado para ejecutarse cada 10 minutos
automáticamente (`.github/workflows/check_stock.yml`). También puedes
lanzarlo a mano desde la pestaña **Actions** de tu repo →
"Check Pokémon TCG Stock" → **Run workflow**.

La primera ejecución **no envía ningún restock** — solo construye el
"mapa" inicial del stock de cada tienda. A partir de la segunda
ejecución, cualquier producto que pase de agotado a disponible generará
un mensaje en tu canal.

## 6. Añadir más tiendas

El bot soporta tres plataformas. Cada tienda en `data/stores.json` lleva
un campo `"platform"`:

```json
{ "name": "Nombre tienda", "domain": "dominio.com", "platform": "shopify" }
```

- **`shopify`**: usa `/products.json`. Compruébalo visitando
  `https://DOMINIO.com/products.json` — si ves JSON con productos,
  funciona.
- **`woocommerce`**: usa la Store API pública de WooCommerce.
  Compruébalo visitando
  `https://DOMINIO.com/wp-json/wc/store/v1/products?per_page=3` — si
  ves JSON con productos y un campo `is_in_stock`, funciona. Algunas
  tiendas WooCommerce desactivan esta API; si da 404/403, esa tienda no
  se puede monitorizar así sin más trabajo.
- **`prestashop`**: es el método más frágil. Busca URLs de producto en
  `https://DOMINIO.com/sitemap.xml` y lee el microdato schema.org de
  cada página de producto. Es más lento (una petición por producto,
  limitado a `PRESTASHOP_MAX_PRODUCTS` en `bot.py`) y depende de que el
  tema de la tienda use ese microdato estándar (la mayoría lo hace por
  SEO, pero no todos).

Consulta `data/coverage_report.md` para ver qué tiendas de StockTCG
tienen ya dominio y plataforma identificados, y cuáles faltan todavía.

## Limitaciones de esta versión (y cómo seguir escalando)

- **66 de 158 tiendas ya integradas** (35 Shopify + 21 WooCommerce + 10
  PrestaShop). El resto están pendientes de identificar su dominio o
  su plataforma — ver `data/coverage_report.md`.
- El scraper de **PrestaShop es el más lento y frágil** de los tres: una
  petición HTTP por producto, y depende del microdato schema.org del
  tema. Si una tienda PrestaShop no devuelve nada, probablemente su
  tema no incluye ese microdato y necesitaría un scraper a medida.
- **Amazon, Carrefour, El Corte Inglés, etc.** tienen protecciones
  anti-bot serias. Monitorizarlas de forma fiable normalmente requiere
  servicios de pago (proxies rotativos, resolución de captchas) — es la
  parte que StockTCG cobra por resolver a esa escala.
- **Frecuencia real:** GitHub Actions gratis no garantiza el cron al
  segundo; en la práctica tendrás alertas con un retraso de entre 0 y
  ~15 minutos, no el "~1 minuto" del servicio de pago. Para bajar ese
  retraso necesitarías un servidor/VPS propio corriendo el script en
  bucle continuo (dejaría de ser gratis, pero sería barato: 3-5€/mes).
- **Estado compartido:** el estado se guarda haciendo commit de
  `data/state.json` en cada ejecución. Funciona bien hasta unos
  cientos de tiendas; a partir de ahí conviene mover el estado a una
  base de datos gratuita (ej. Supabase, tier gratuito).

## Próximos pasos sugeridos

1. Prueba esto con las ~14 tiendas de ejemplo y confirma que llegan
   alertas correctamente.
2. Añade el resto de tiendas Shopify de tu lista de 158 (dime cuáles
   son y te ayudo a identificarlas).
3. Cuando quieras, seguimos con scrapers específicos para las tiendas
   no-Shopify.
