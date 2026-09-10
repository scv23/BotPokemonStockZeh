# Cobertura de las 158 tiendas de StockTCG

Estado a fecha de hoy, tras revisar la web pública de stocktcg.net.

## ✅ Grupo 1 — Shopify, ya en `stores.json` (35 tiendas)

Funcionan YA con el bot tal cual está, sin tocar código:

Alfriki, AllInTCG, CanaryPop, CardCosmos, CardZone, Darizard9, ElBazarTCG,
Elite Store TCG, EpicDeck, GrilleCards, Iberian Collect, JJCollection,
Kame House Cards, La Boveda Friki, La Escotilla, La Nave TCG,
La Tienda de las Cartas, Metamorph, MojoCards, PokeDealTCG, PokedexCards,
Pokemillon, Pokezilla, Saruman Games, Solo Hits, SunnyStore,
TCG Collection, TCG Level, TCG-Universe, Tiger Cards, TodoHits, Toy Planet,
UNSOBREMAS, Universe TCG, Wini Games

## 🟡 Grupo 2 — Dominio conocido, pero NO son Shopify (~38 tiendas)

Tengo su dominio, pero necesitan un scraper distinto porque no usan
Shopify (mayoría WordPress/WooCommerce, algunas PrestaShop). El motor
`/products.json` no les sirve.

**WordPress / WooCommerce:** BimbaCards (bimbacards.com), CardCrack
(cardcrack.com), CardStation (thecardstation.es), Checollect (checollect.es),
ELIUS (eliusweb.com), El Paraiso Friki (elparaisofriki.com), EmpireGames
(empiregames.es), FlashStore (flashstore.es), G3 TCG (g3tcg.es), Gremio de
Dragones (gremiodedragones.es), HeroFreaks (herofreaks.com), HoloPlaza TCG
(holoplazatcg.com), Indústria 61 (industria61.com), Micelion Games
(miceliongames.com), Mundo Distorsión (mundodistorsion.es), Pokebank
(pokebank.es), Pokeiko (pokeiko.com), Pokewoke (pokewoke.store), Reino de
Cartas (reinodecartas.com), SuperCollectors (supercollectors.es), TopDeck
(topdeck.es)

**PrestaShop (patrón de logo detectado):** Distrito Zero (distritozero.es),
Dungeon Marvels (dungeonmarvels.com), Frikimaz (frikimaz.es), Gameria
(gameria.es), La Maquina del Temps (lamaquinadeltemps.com), Metrópolis
Center (metropolis-center.com), Mythic Card TCG (mythocardtcg.com), Pidopop
(pidopop.com — confirmado explícitamente), StarGeek (stargeek.es), TPK
Hobby & Games (tpkhobbygames.com)

**Plataforma sin confirmar (dominio sí conocido):** Manavortex
(manavortex.es), Mola Ser Friki (molaserfriki.com), DRIM (drimjuguetes,
dominio exacto por confirmar)

## 🔴 Grupo 3 — Grandes superficies (5 tiendas)

Dominio conocido, pero con protección anti-bot seria. No son un buen punto
de partida gratis:

Amazon, Carrefour, El Corte Inglés, GAME, Toys R Us

## ⚪ Grupo 4 — Sin dominio identificado todavía (~80 tiendas)

StockTCG no muestra logo/enlace externo para estas en su web pública, así
que no tengo su dominio. Habría que buscarlas una a una (nombre de tienda +
"pokemon tcg" suele bastar) o, más rápido: si tú ya conoces alguna de estas
tiendas, dime su dominio y la añado directamente.

AllDayTCG, Aquitaz, BaruZcard, Battle Bear, Beam Card Shop, Bescards, Break
The Case, Card Treasure, CardX, Cards Hunters, Cardverse, Collect Avenue,
Complete Collections, Dabas, Dany Store, DavidPTCG, De Broergrot, De
Papieren Korf, DeckKingdom, DracauGames, El Duelista, Factory Cards,
Flash-Cards, Freak Kingdom, Fuji Store, Gadget Man Ireland, Gamescape, HYP3
Amsterdam, Hikaru Distribution, Hunters Quest, Ichiban, Irish Poke Finds, JM
Cards, Kairyu, KeepSeven, Kwily TCG, Lotus Valley, LuxCards, MTG Webshop,
Maaxstar, Magic Omens, MagicNerd, ManaTCG, Manaheim, Maximus, Merchfox,
Nickeila, OPPA Cards, Otakura, Outpost Brussels, PKMWinkel, Pack Point,
Pikamon, Plaza TCG, Plugstars, Pocketcards, Poke Hatch, Poke-Geek, PokeBros,
PokeCards Store, PokeFamily, PokeGourou, PokeStore France, PokeStore Italy,
PokeTalk, Pokeca, Pokejotta, Pokeka, Pokemons.dk, Poketrov, Psydeck,
RelicTCG, Sklad Gier, Snooop, TCGViert, TCGdirect, TKCollectibles, Templars
Arena, The Booster Box, The Swedish Fish, TuttoGiappone, VCOLLECT,
VintiCards, Yonko TCG

## Resumen

| Grupo | Nº tiendas | Acción |
|---|---|---|
| 1 — Shopify, ya integradas | 35 | Ninguna, ya funciona |
| 2 — Dominio conocido, otra plataforma | ~38 | Necesita scraper WordPress/PrestaShop |
| 3 — Grandes superficies | 5 | Descartar por ahora (anti-bot) |
| 4 — Sin dominio | ~80 | Buscar dominio antes de nada |

## Próximo paso propuesto

1. Construyo el scraper genérico de WordPress/WooCommerce (cubre ~21
   tiendas del Grupo 2 de golpe).
2. Construyo el scraper de PrestaShop (cubre otras ~10).
3. Vamos buscando dominios del Grupo 4 en tandas (dime si prefieres que
   lo haga yo por nombre, o si me pasas tú los que ya conozcas).
