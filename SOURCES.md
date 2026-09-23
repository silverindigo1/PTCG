# PokeArb: kildegodkendelse (source permission register)

Check date for every row below: **2026-09-22**. Every URL in this file was
fetched in the session that produced it. Nothing is carried forward from
memory or from a previous run.

Re-check cadence: every 90 days, and always before a shop is added to the
pipeline. Shopify and WooCommerce shops can swap in a default terms template
at any time, and the default Shopify template *does* contain an anti-scraping
clause (see De Broergrot below), so a clean check today is not permanent.

Method per shop:

1. Fetch `/robots.txt`. Record every `User-agent` block, the `Disallow` and
   `Allow` lines, and any `Crawl-delay`. Decide whether the exact path the
   pipeline will call is permitted for `User-agent: *`.
2. Fetch the terms page (handelsbetingelser / AGB / algemene voorwaarden /
   köpvillkor / terms of service) and search for: scraping, crawling, robot,
   robotter, Roboter, spider, edderkop, data mining, automatiseret,
   automatisiert, geautomatiseerd, automatiserad, bot, automated access.
3. Confirm a machine-readable catalogue endpoint responds.
4. Confirm the shop ships to Denmark.

A shop enters the pipeline only if all four pass.

---

## A. Approved: all four checks pass

| Shop | Country | Platform | Endpoint | Currency | Ships to DK |
|---|---|---|---|---|---|
| The TCG Plug | NL | Shopify | `/products.json` | EUR | yes |
| Energy Vault | BE | Shopify | `/products.json` | EUR | yes |
| God of Cards | DE | Shopify | `/products.json` | EUR | yes |
| TCGViert | DE | Shopify | `/products.json` | EUR | yes |
| TCGshoppers | NL | Shopify | `/products.json` | EUR | yes |
| PokeTalk | SE | Shopify | `/products.json` | SEK | yes |
| Matraws | DK | Shopify | `/products.json` | DKK | yes (DK only) |
| Musen og Slottet | DK | Shopify | `/products.json` | DKK | yes (DK only) |
| Pocket Monster | DK | WooCommerce | Store API v1 | DKK | yes (DK only) |

### The TCG Plug (thetcgplug.nl), Netherlands

Added 2026-09-22.

- robots.txt: https://www.thetcgplug.nl/robots.txt
  Standard Shopify block. `/products.json`, `/collections/` and `/policies/`
  are all permitted for `User-agent: *`. No `Crawl-delay`.
- Terms: https://www.thetcgplug.nl/policies/terms-of-service
  Dutch terms in 16 articles, Definities through Pre-orders en slotbepalingen.
  No mention of scrapen, crawlen, spiders, robots, geautomatiseerd or bots.
- Endpoint: https://www.thetcgplug.nl/products.json?limit=2 returns a valid
  Shopify feed.
- Shop metadata: https://www.thetcgplug.nl/meta.json gives `currency: EUR` and
  a `ships_to_countries` array containing `DK`.
- Shipping: https://www.thetcgplug.nl/policies/shipping-policy states only
  "De verzendkosten worden tijdens het afrekenen duidelijk weergegeven", so no
  rate to Denmark is published. The site uses its assumption and says so.

### Energy Vault (energy-vault.be), Belgium

Added 2026-09-22. The first Belgian shop in the set.

- robots.txt: https://energy-vault.be/robots.txt
  Standard Shopify block, `Allow: /`. `/products.json` and `/collections/` are
  permitted. No `Crawl-delay`. The file also carries an agent instruction
  pointing at a UCP/MCP endpoint and prohibiting automated checkout, which
  PokeArb never performs.
- Terms: https://energy-vault.be/policies/terms-of-service
  Dutch terms in 17 sections, Identiteit van de onderneming through Contact.
  No clause on scraping, crawling, spiders, robots or automated access.
- Endpoint: https://energy-vault.be/products.json?limit=2 returns a valid feed.
- Shop metadata: https://energy-vault.be/meta.json gives `currency: EUR` and
  a `ships_to_countries` array containing `DK`.
- Shipping: https://energy-vault.be/policies/shipping-policy names standard and
  insured options but publishes no rate.

### God of Cards (godofcards.com), Germany

- robots.txt: https://godofcards.com/robots.txt
  Standard Shopify block. `User-agent: *` has `Allow: /`. Disallowed paths are
  `/admin`, `/cart/`, `/checkout`, `/checkouts/`, `/orders`, `/account`,
  `/services`, `/sf_*`, `/cart.js`, `/recommendations/products`,
  `/cdn/wpm/*.js`, plus collection sort/filter crawl traps. No `Crawl-delay`.
  `/products.json` is not disallowed.
- Terms: https://godofcards.com/policies/terms-of-service
  German AGB. Sections: Geltungsbereich, Vertragsabschluss, Preise und
  Versandkosten, Zahlung, Zahlungsverzug, Schadens- und Wertersatz,
  Zurückbehaltungsrecht, Lieferung, Günstige Versandart bei Rücksendung,
  Eigentumsvorbehalt, Mängelrechte, Haftung, Gerichtsstand, Jugendschutz,
  Streitbeilegung, Schlussbestimmungen. No clause on scraping, crawling,
  Roboter, Spider, data mining, automatisiert or bots.
- Endpoint: https://godofcards.com/products.json?limit=2 returns a valid
  Shopify products feed.
- Shop metadata: https://godofcards.com/meta.json
  `currency: EUR`; `ships_to_countries` includes `DK`.

### TCGViert (tcgviert.com), Germany

- robots.txt: https://tcgviert.com/robots.txt
  Standard Shopify block, same shape as above. `/products.json` not
  disallowed, `/collections/` allowed, no `Crawl-delay`.
- Terms: https://tcgviert.com/policies/terms-of-service
  German AGB. Sections: Geltungsbereich; Vertragspartner, Vertragsschluss,
  Korrekturmöglichkeiten; Liefer- und Versandbedingungen; Bezahlung;
  Eigentumsvorbehalt; Gewährleistung und Garantien; Streitbeilegung;
  Widerrufsbelehrung. No automated-access clause.
- Endpoint: https://tcgviert.com/products.json?limit=2 returns a valid feed.
- Shop metadata: https://tcgviert.com/meta.json
  `currency: EUR`; `ships_to_countries` includes `DK`.

### TCGshoppers (tcgshoppers.nl), Netherlands

- robots.txt: https://www.tcgshoppers.nl/robots.txt
  Standard Shopify block. `/products.json` not disallowed. No `Crawl-delay`.
- Terms: https://www.tcgshoppers.nl/policies/terms-of-service
  Dutch terms covering purchase, product information, delivery, returns and
  dispute resolution. No clause on scrapen, crawlen, spiders, robots,
  geautomatiseerd or bots.
- Endpoint: https://www.tcgshoppers.nl/products.json?limit=2 returns a valid
  feed.
- Shop metadata: https://www.tcgshoppers.nl/meta.json
  `currency: EUR`; `ships_to_countries` includes `DK`.

### PokeTalk (poketalk.se), Sweden

- robots.txt: https://www.poketalk.se/robots.txt
  Standard Shopify block. `/products.json` not disallowed. No `Crawl-delay`.
- Terms: https://www.poketalk.se/policies/terms-of-service
  No clause on scraping, crawling, robot, spider, data mining, automatiserad
  or bots.
- Endpoint: https://www.poketalk.se/products.json?limit=2 returns a valid
  feed.
- Shop metadata: https://www.poketalk.se/meta.json
  `currency: SEK`; `ships_to_countries` includes `DK`.

### Matraws (matraws.dk), Denmark

- robots.txt: https://matraws.dk/robots.txt
  Standard Shopify block. `/products.json` not disallowed. No `Crawl-delay`.
- Terms: https://matraws.dk/policies/terms-of-service
  Danish handelsbetingelser, roughly 2,100 words. Sections: Generelle
  oplysninger, Levering og afhentning, Reklamationsret, Refusion, Returret,
  Returnering, Varens stand ved returnering, Tilbagebetaling, Varer undtaget
  fortrydelsesretten, Klagemuligheder. No "Forbudt brug" section and no
  clause on scraping, robotter, edderkopper or automatiseret adgang.
- Endpoint: https://matraws.dk/products.json?limit=2 returns a valid feed.
- Shop metadata: https://matraws.dk/meta.json
  `currency: DKK`; `ships_to_countries: ["DK"]`; 25,001 published products.

### Musen og Slottet (musenogslottet.dk), Denmark

- robots.txt: https://www.musenogslottet.dk/robots.txt
  Standard Shopify block. `/products.json` not disallowed.
- Terms: https://www.musenogslottet.dk/policies/terms-of-service
  No clause on scraping, crawling, robotter, spiders, data mining,
  automatiseret indsamling or bots.
- Endpoint: https://www.musenogslottet.dk/products.json?limit=2 returns a
  valid feed.
- Shop metadata: https://www.musenogslottet.dk/meta.json
  `currency: DKK`; `ships_to_countries: ["DK"]`; 2,920 published products.
  General toy shop, so the Pokémon share of the catalogue is small.

### Pocket Monster (pocketmonster.dk), Denmark

- robots.txt: https://pocketmonster.dk/robots.txt
  `User-agent: *`, `Disallow: /wp-admin/`, `Allow: /wp-admin/admin-ajax.php`,
  `Sitemap: https://pocketmonster.dk/sitemap_index.xml`. Nothing else is
  disallowed, so `/wp-json/` is permitted.
- Terms: https://pocketmonster.dk/handelsbetingelser/
  Covers payment, shipping, ordering, returns, complaints, privacy and
  dispute resolution. No clause on scraping, robotter, spiders, data mining
  or automatiseret adgang.
- Endpoint: https://pocketmonster.dk/wp-json/wc/store/v1/products?per_page=2
  returns a WooCommerce Store API list. First product carried
  `{"price":"14900","regular_price":"14900","sale_price":"14900","currency_code":"DKK"}`
  and `stock_status: "in-stock"`. The Store API gives currency and stock
  status directly, which Shopify's `/products.json` does not.

---

## B. Conditionally approved: one check unresolved, needs a decision

### TcgReus (tcgreus.nl), Netherlands, Shopify

Robots and terms both pass, feed works, but shipping coverage is ambiguous.

- robots.txt: https://www.tcgreus.nl/robots.txt (standard Shopify, clean)
- Terms: https://www.tcgreus.nl/policies/terms-of-service (no automated-access
  clause)
- Endpoint: https://www.tcgreus.nl/products.json?limit=2 (valid feed)
- Shop metadata: https://www.tcgreus.nl/meta.json
  `ships_to_countries: ["*","BE","NL"]`. The `*` entry is a rest-of-world
  wildcard rather than an explicit DK entry, so Denmark delivery is not
  confirmed from the metadata alone. Needs a manual check of the shop's
  shipping page or a test basket before it goes live.

### Bulk Paradise TCG (bulkparadise-tcg.de), Germany, WooCommerce

Robots, terms and feed all pass. Shipping to Denmark is not documented.

- robots.txt: https://bulkparadise-tcg.de/robots.txt
  `User-agent: *` disallows only `/wp-content/uploads/wc-logs/`,
  `/wp-content/uploads/woocommerce_transient_files/`,
  `/wp-content/uploads/woocommerce_uploads/`, `/*?add-to-cart=`,
  `/*?*add-to-cart=` and `/wp-admin/`, with `Allow:
  /wp-admin/admin-ajax.php`. A second Yoast block has an empty `Disallow:`.
  `/wp-json/` is permitted. No `Crawl-delay`.
- Terms: https://bulkparadise-tcg.de/agb/
  Sections: Geltungsbereich, Vertragsschluss, Widerrufsrecht, Preise und
  Zahlungsbedingungen, Liefer- und Versandbedingungen, Eigentumsvorbehalt,
  Mängelhaftung, Haftung, Einlösung von Aktionsgutscheinen, Anwendbares
  Recht, Alternative Streitbeilegung. No automated-access clause.
- Endpoint: https://bulkparadise-tcg.de/wp-json/wc/store/v1/products?per_page=2
  returns a Store API list with `currency_code: EUR` and stock status.
- Shipping: https://bulkparadise-tcg.de/versand/ states only "Versand aus
  Deutschland" and "Gratis Versand ab 150€!" with no country list. Denmark
  delivery unconfirmed.

### TCG Company (tcgcompany.nl), Netherlands, WooCommerce

Robots, terms and feed all pass. Shipping to Denmark is not documented.

- robots.txt: https://tcgcompany.nl/robots.txt
  `User-agent: *` disallows `/app/uploads/wc-logs/`,
  `/app/uploads/woocommerce_transient_files/`,
  `/app/uploads/woocommerce_uploads/`, `/*?add-to-cart=`, `/*?*add-to-cart=`
  and `/wp/wp-admin/`, with `Allow: /wp/wp-admin/admin-ajax.php`. Second
  Yoast block has an empty `Disallow:`. No `Crawl-delay`.
- Terms: https://tcgcompany.nl/algemene-voorwaarden/
  Covers payment, returns, liability, guarantees and intellectual property.
  No clause on scrapen, crawlen, spiders, robots or geautomatiseerd gebruik.
- Endpoint: https://tcgcompany.nl/wp-json/wc/store/v1/products?per_page=2
  returns a Store API list with `currency_code: EUR` and stock status.
- Shipping: https://tcgcompany.nl/verzending-en-retour/ returns HTTP 404, so
  the shipping policy could not be read. Denmark delivery unconfirmed.

### Kelz0r (kelz0r.dk), Denmark

- robots.txt: https://www.kelz0r.dk/robots.txt
  `User-agent: *` disallows only `/magic/admin/`, `/magic/includes/`,
  `/magic/cgi-bin/`, `/magic/banned/`, `/magic/blocked.php`,
  `/magic/login.php`, `/magic/pennyworth/` and
  `/magic/advanced_search_result.php`, so the product pages are permitted.
  However the file also carries `User-agent: ClaudeBot` with `Disallow: /`
  and `User-agent: AhrefsBot` with `Disallow: /`, plus per-agent
  `Crawl-delay` values of 1 to 10 seconds for Applebot, bingbot, msnbot,
  msnbot-media, yandex and FacebookBot.
  The letter of the file permits a named PokeArb agent, but the shop is
  clearly declining bots it does not recognise as search engines. This is a
  judgement call, not a compliance blocker.
- No machine-readable catalogue. The shop runs an osCommerce-style storefront
  with `.php` category pages, so it would need HTML parsing, which is more
  fragile and heavier on the shop than a JSON feed.
- Terms not yet checked.

---

## C. Rejected

### De Broergrot (debroergrot.nl), Netherlands: terms forbid scraping

- Terms: https://www.debroergrot.nl/policies/terms-of-service
  Article 12 (Verboden Gebruik) lists prohibited use
  "voor spam, phishing, pharming, pretexting, spiders, crawling of scrapen".
  Article 2 adds: "je stemt ermee in geen enkel deel van de Dienst, het
  gebruik van de Dienst of toegang tot de Dienst of element op de website
  waarmee de Dienst wordt verstrekt, te reproduceren, dupliceren, kopiëren,
  verkopen, door te verkopen of te exploiteren zonder onze uitdrukkelijke
  schriftelijke toestemming".
- Its robots.txt (https://www.debroergrot.nl/robots.txt) is permissive, which
  is exactly why the terms check is not optional.
- Verdict: excluded. Only revisit with written permission from the shop.

### Hobbykort (hobbykort.se / hobbykort.eu), Sweden: terms unverifiable

- robots.txt: https://hobbykort.se/robots.txt
  `User-agent: *` disallows `/policies/` and `/*/policies/`, among others.
  (https://hobbykort.eu/robots.txt issues a 302 to the .se domain.)
- Terms: https://hobbykort.se/policies/terms-of-service could not be fetched,
  because the shop's own robots.txt disallows that path.
- Verdict: excluded. The terms check cannot be completed without breaching
  robots.txt. Revisit only if the shop publishes terms on an allowed path.

### Tradingtoys (tradingtoys.de), Germany: terms unverifiable

- robots.txt: https://www.tradingtoys.de/robots.txt
  `User-agent: *` disallows `/policies/` and `/search`, among others. Also
  blocks Nutch entirely and sets `Crawl-delay: 10` for AhrefsBot and
  AhrefsSiteAudit.
- Terms: https://www.tradingtoys.de/policies/terms-of-service could not be
  fetched, because the shop's own robots.txt disallows that path.
- Verdict: excluded, same reason as Hobbykort.

### Tcgstore.se, Sweden: does not ship to Denmark

- robots.txt: https://tcgstore.se/robots.txt is a clean standard Shopify
  block, and it carries an explicit agent instruction:
  "Checkouts are for humans. DO NOT complete checkout, payment, or order
  placement automatically." PokeArb never places orders, so this is not a
  conflict, but it is on the record.
- Terms: https://tcgstore.se/policies/terms-of-service has 14 sections
  (Företagsinformation through Ändringar av villkor) and no automated-access
  clause.
- Endpoint: https://tcgstore.se/products.json?limit=2 returns a valid feed.
- Shop metadata: https://tcgstore.se/meta.json gives
  `currency: SEK` and `ships_to_countries: ["SE"]`.
- Verdict: excluded on the shipping criterion only. Everything else passes,
  so re-check if they open up Nordic delivery.

### Cardhome (cardhome.at), Austria: terms forbid scraping

- robots.txt: https://cardhome.at/robots.txt is a clean standard Shopify block
  that permits `/products.json`, `/collections/` and `/policies/`.
- Terms: https://cardhome.at/policies/terms-of-service, section 13,
  "Verbotene Nutzungen", prohibits
  "Spam-, Phishing-, Pharm-, Pretext-, Spider-, Crawl- oder Scrape-Aktivitaeten
  zu betreiben".
- Verdict: excluded. The second shop in this register whose permissive
  robots.txt sits over a prohibiting terms page, which is why the terms check
  is not optional.

### TCG Vault (tcgvault.be), Belgium: no reachable feed or terms

- robots.txt: https://tcgvault.be/robots.txt permits everything relevant, and
  notably grants explicit permission to GPTBot, PerplexityBot, ClaudeBot and
  ChatGPT-User.
- Shop metadata: https://tcgvault.be/meta.json confirms Shopify, EUR, and a
  `ships_to_countries` array containing `DK`.
- But https://tcgvault.be/products.json?limit=2 returns HTTP 404, and both
  https://tcgvault.be/policies/terms-of-service and
  https://tcgvault.be/pages/algemene-voorwaarden return HTTP 404.
- Verdict: excluded. Willing host, but neither the catalogue nor the terms
  could be read. Worth revisiting.

### TCG-kauppa (tcgkauppa.fi), Finland: the API is disallowed

- robots.txt: https://www.tcgkauppa.fi/robots.txt contains
  `Disallow: /wp-json/` and `Disallow: /?rest_route=`, which is exactly the
  WooCommerce Store API the pipeline would use.
- Verdict: excluded. The shop pages themselves are permitted, but HTML parsing
  a shop that has explicitly closed its API is not in the spirit of the check.

### PokePulls (pokepulls.fi), Finland: terms page disallowed

- robots.txt: https://pokepulls.fi/robots.txt contains
  `Disallow: /sivu/kayttoehdot`, the terms of use page.
- Verdict: excluded, same reason as Hobbykort and Tradingtoys.

### PikaShop (pikashop.pl), Poland: API blocked at the edge

- robots.txt: https://pikashop.pl/robots.txt permits `/wp-json/`.
- Terms: https://pikashop.pl/regulamin/ has 17 sections and no clause on
  scraping, crawling, roboty, pajaki or zautomatyzowany dostep.
- But https://pikashop.pl/wp-json/wc/store/v1/products?per_page=2 and the
  older `/wp-json/wc/store/products` path both return HTTP 403, so something
  in front of the site refuses the request regardless of what robots.txt says.
- Verdict: excluded. Permission is fine; access is not.

### Flash Cards (flash-cards.be), Belgium: ambiguous terms, blocked API

- robots.txt: https://flash-cards.be/robots.txt is a standard WooCommerce
  block with nothing relevant disallowed.
- Terms: https://flash-cards.be/algemene-voorwaarden/ section 8.3c lists
  "Gebruik van geautomatiseerde bots" as grounds for cancelling an order.
  That reads as aimed at bots buying stock rather than at reading prices, but
  it is close enough to warrant caution.
- The Store API returns HTTP 403 in any case.
- Verdict: excluded on both counts.

### Austrian shops with no machine-readable catalogue

- https://www.butticards.at/robots.txt permits the relevant paths and carries a
  non-standard `Content-Signal` directive, but https://www.butticards.at/meta.json
  and the terms page both fail (403 and 404), so nothing could be verified.
- https://www.tcg-dealers.at/robots.txt is permissive, but
  https://www.tcg-dealers.at/products.json?limit=2 returns HTTP 400, so it is
  not Shopify.
- https://www.sammelkarten-shop.at/robots.txt sets `Crawl-Delay: 5` and permits
  the shop, but https://www.sammelkarten-shop.at/products.json?limit=2 returns
  HTTP 404.
- Verdict: all three excluded for want of a feed.

### Sapphire Cards (sapphire-cards.de), Germany: robots.txt unreachable

- https://sapphire-cards.de/robots.txt failed with a connect timeout, so the
  permission check could not be started.
- Verdict: excluded until robots.txt can be read.

### Shops with no robots.txt (HTTP 404)

A missing robots.txt means no crawl restrictions are published, which under
RFC 9309 is full allow. None of these has a confirmed machine-readable
catalogue, so none is worth the parsing cost yet.

- https://www.nextlevelgames.dk/robots.txt returns 404. PrestaShop storefront.
  https://www.nextlevelgames.dk/content/3-handelsbetingelser also returns 404,
  so the terms page still has to be located.
- https://davidptcg.nl/robots.txt returns 404.
- https://epicpanda.dk/robots.txt returns 404.
- https://www.mugglealley.dk/robots.txt returns a file containing only
  `User-agent: *` with no directives.

### Marketplaces not assessed

Cardmarket and eBay are marketplaces, not shops. Cardmarket has an official
app-registered API with its own terms, and it is a different data model
(seller listings rather than shop prices). Out of scope for this shortlist.


---

## E. Second sweep, 2026-09-23

47 further candidates, screened first with Shopify's `/meta.json` (platform,
currency and `ships_to_countries` in one call), then given the full robots.txt
and terms check. 12 passed. Every URL below was fetched on 2026-09-23. Each
passing shop's catalogue feed was then confirmed by the pipeline itself in a
live run the same day.

### E1. Added

| Shop | Country | Platform | Ships to DK | Notes |
|---|---|---|---|---|
| MtgwebshopDK | DK | Shopify | yes | |
| Rogerz | DK | Shopify | yes | lists items twice, "Alm. moms" and "Brugtmoms" |
| SealedCardzz | DE | Shopify | yes | |
| Yonko TCG | DE | Shopify | yes | strong Chinese and Japanese range |
| Cardify | NL | Shopify | yes | |
| Lichcards | NL | Shopify | yes | shipping 9.95 EUR to other EU, free from 75 EUR |
| Bescards | NL | Shopify | yes | |
| DavidPTCG | NL | WooCommerce | **unconfirmed** | terms mention only Dutch shipping |
| Spelparken | SE | Shopify | yes | |
| Tiger Cards | ES | Shopify | yes | Spanish titles |
| Pokemillon | ES | Shopify | yes | Spanish titles, lists damaged boxes separately |
| BaruZcard | IT | Shopify | yes | **judgment call**, see below |

Evidence per shop (robots.txt, terms, shop metadata):

- **MtgwebshopDK.**
  https://mtgwebshop.dk/robots.txt is a standard Shopify block; nothing
  relevant is disallowed. https://mtgwebshop.dk/policies/terms-of-service has
  sections INFORMATIONER, Bestillinger, Reklamation, Fragt & Levering,
  Persondata, Andre vilkår, Politikker, with no automated-access clause.
  https://mtgwebshop.dk/meta.json: DKK, `ships_to_countries` includes DK.
  https://mtgwebshop.dk/policies/shipping-policy gives delivery time, no price.
- **Rogerz.**
  https://rogerz.dk/robots.txt permits `/collections/` explicitly and
  disallows nothing relevant. https://rogerz.dk/policies/terms-of-service has
  no automated-access clause. https://rogerz.dk/meta.json: DKK, ships to DK.
  https://rogerz.dk/policies/shipping-policy shows no rates.
- **SealedCardzz.**
  https://sealedcardzz.com/robots.txt permits `/collections/` and disallows
  nothing relevant. https://sealedcardzz.com/policies/terms-of-service has 12
  sections, Geltungsbereich through Online-Streitbeilegung, with no
  automated-access clause. https://sealedcardzz.com/meta.json: EUR, ships to DK.
- **Yonko TCG.**
  https://yonko-tcg.de/robots.txt disallows nothing relevant.
  https://yonko-tcg.de/policies/terms-of-service (AGB plus
  Kundeninformationen) has no automated-access clause.
  https://yonko-tcg.de/meta.json: EUR, ships to DK.
- **Cardify.**
  https://cardifytcg.nl/robots.txt permits `/collections/`.
  https://cardifytcg.nl/policies/terms-of-service, sections 3.1 to 3.9, has no
  automated-access clause. https://cardifytcg.nl/meta.json: EUR, ships to DK.
- **Lichcards.**
  https://lichcards.nl/robots.txt permits `/collections/`.
  https://lichcards.nl/policies/terms-of-service, 17 articles, has no
  automated-access clause. https://lichcards.nl/meta.json: EUR, ships to DK,
  25,001 products, so the crawl uses the collection `alle-tcg-producten`
  found via https://lichcards.nl/collections.json?limit=250.
  https://lichcards.nl/policies/shipping-policy: other EU countries 9.95 EUR
  standard, "Vanaf €75 gratis".
- **Bescards.**
  https://www.bescards.com/robots.txt disallows nothing relevant.
  https://www.bescards.com/policies/terms-of-service, 18 articles plus B2B
  terms, has no automated-access clause. https://www.bescards.com/meta.json:
  EUR, ships to DK. https://www.bescards.com/policies/shipping-policy: FedEx
  International, cost calculated at checkout.
- **DavidPTCG.**
  https://davidptcg.nl/robots.txt returned 404 on 2026-09-22, which under
  RFC 9309 means nothing is disallowed.
  https://davidptcg.nl/wp-json/wc/store/v1/products?per_page=1 returns a
  WooCommerce Store API list in EUR. https://davidptcg.nl/algemene-voorwaarden/,
  18 articles, has no automated-access clause and mentions only free shipping
  within the Netherlands, so delivery to Denmark is flagged unconfirmed.
- **Spelparken.**
  https://spelparken.se/robots.txt permits `/collections/`.
  https://spelparken.se/policies/terms-of-service, 8 sections, has no
  automated-access clause. https://spelparken.se/meta.json: SEK, ships to DK.
- **Tiger Cards.**
  https://www.tigercards.es/robots.txt permits `/collections/`.
  https://www.tigercards.es/policies/terms-of-service, 12 sections, has no
  automated-access clause. https://www.tigercards.es/meta.json: EUR, ships to DK.
- **Pokemillon.**
  https://www.pokemillon.com/robots.txt permits `/collections/`.
  https://www.pokemillon.com/policies/terms-of-service, 7 sections, has no
  automated-access clause. https://www.pokemillon.com/meta.json: EUR, ships to DK.
- **BaruZcard.**
  https://baruzcard.it/robots.txt disallows nothing relevant.
  https://baruzcard.it/meta.json: EUR, ships to DK.
  https://baruzcard.it/policies/terms-of-service has no clause on scraping,
  crawling, bots or automated access. Art. 1.10 says "Salvo specifica
  autorizzazione scritta del Venditore, è vietata la riproduzione, anche
  parziale", covering reproduction, distribution and publication of site
  content. PokeArb shows a price, a stock flag and a link, never the shop's
  text or images, so this is read as a copyright clause rather than a ban on
  reading prices. Art. 13 is a disclosure about the shop's own use of AI
  tools, not a restriction on visitors. Included, flagged as a judgment call;
  remove it from `config.py` if you read Art. 1.10 more strictly.

### E2. Rejected in this sweep

Terms forbid scraping, although robots.txt permits it. The same Shopify
template clause, in three languages:

- **Play-Maniac (DE).** https://www.play-maniac.de/policies/terms-of-service,
  section 10 "Verbot automatisierter Zugriffe und Bot-Nutzung": "Der Einsatz
  von automatisierten Systemen oder Software (z. B. „Bots", „Scripts",
  „Crawler", „Spiders", „Scraper"), um Daten aus der Website auszulesen".
- **PokeDealTCG (ES).** https://www.pokedealtcg.es/policies/terms-of-service,
  section 13 "Usos prohibidos": "cometer delitos de araña, o cometer rastreo o
  scraping web".

Terms page disallowed by the shop's own robots.txt, so it cannot be read:

- **ADLR Poké-Shop (DK).** https://shop.adlr.dk/robots.txt disallows `/policies/`.
- **cardcosmos (DE).** https://cardcosmos.de/robots.txt disallows `/policies/`.
- **Iberian Collect (ES).** https://iberiancollect.com/robots.txt disallows `/policies/`.
- **Otakura (IT).** https://otakura.com/robots.txt disallows `/policies/`,
  although it explicitly allows GPTBot, Claude and PerplexityBot.

Do not ship to Denmark, per their own `/meta.json`:

- OneStopTCG, https://onestoptcg.com/meta.json: US only.
- Toy Treasure, https://toy-treasure.com/meta.json: no DK in the list.
- Trinket Mage, https://trinket-mage.eu/meta.json: CH, DE, GB only.
- tcgcardgameshop.nl, https://www.tcgcardgameshop.nl/meta.json: BE and NL only.
- Monpokestore, https://www.monpokestore.fr/meta.json: BE and FR only.
- The Booster Box, https://theboosterbox.es/meta.json: ES only.
- TodoHits, https://todohits.com/meta.json: AD, ES, FR, PT.
- JJCollection, https://www.jjcollection.es/meta.json: no DK in the list.
- Obsidia TCG, https://obsidia-tcg.store/meta.json: GB only, and outside the
  EU in any case.

No reachable machine-readable catalogue (meta.json 404, 403 or timeout, and no
Store API found): pokemons.dk, pokedrop.dk (robots.txt 503), kaartkamer.nl,
catchandsleeve.nl, evokort.se (503), swepoke.se (403), shinycards.se (403),
blazingtail.fr, lordtcg.fr, shop-tcg.fr (timeout), plazatcg.com,
tcgtraders.eu, tcgeuropa.com (timeout), eurotcg.com (serves HTML, not the
Shopify metadata), tcgboxshop.com, tcgmarket24.com, estokeo.com (timeout),
en.destocktcg.fr, card-corner.de.

Magento, with product and category views disallowed:

- playingcardshop.eu, https://www.playingcardshop.eu/robots.txt.

### E3. What the new shops changed in the classifier

The Spanish and Italian shops exposed gaps that had been silently mislabelling
products, all now covered by recorded-title tests:

- The bare code "de" was read as German, so "ETB Evoluciones de Paldea" was
  tagged German. Two-letter codes now count only as a bracketed or trailing
  tag.
- Spanish "Caja sellada 30 sobres" and Italian "Box Display 36 Buste" are now
  recognised as booster boxes, and Spanish, Italian and French words for ETB,
  bundle and UPC are known.
- Spanish and Italian are now languages in their own right, and a matched
  localised set name implies its language when the shop gives no tag.
- Listings naming two product types ("Booster Bundle Display", "Display + Set
  Allenatore") are rejected as cases or combinations.
- Damage words in Dutch, Spanish, Italian and French, including Pokemillon's
  "(Dañada)", now reject the listing.
- WooCommerce titles are HTML-decoded, which had produced a set called
  "Amp Darkness Ablaze" from "Sword &amp; Shield".

---

## D. Shipping to Denmark

Ranking on the item price alone points at the wrong shop whenever the parcel
costs more than the price gap. Every price on the site is therefore a landed
cost: item plus delivery to Denmark.

Most shops publish no per-country rate at all; the figure appears only at
checkout. Rather than invent numbers, the pipeline uses the shop's own rate
where it is published and a clearly labelled assumption everywhere else. Every
assumed figure is marked "anslaaet" on the page and can be changed by the
reader, which re-ranks every table on the spot.

| Shop | Rate to DK | Source | Checked |
|---|---|---|---|
| Matraws | 39 DKK, no threshold stated | https://matraws.dk/policies/shipping-policy | 2026-09-22 |
| Musen og Slottet | 49 DKK pakkeshop, free over 699 DKK | https://www.musenogslottet.dk/policies/shipping-policy | 2026-09-22 |
| Pocket Monster | 36 DKK PostNord pakkeboks, free over 1000 DKK | https://pocketmonster.dk/handelsbetingelser/ | 2026-09-22 |
| TCGshoppers | 14.95 EUR to other EU countries | https://www.tcgshoppers.nl/policies/shipping-policy | 2026-09-22 |
| Lichcards | 9.95 EUR to other EU countries, free from 75 EUR | https://lichcards.nl/policies/shipping-policy | 2026-09-23 |

Shops that publish nothing usable, all checked 2026-09-22:

- God of Cards: https://godofcards.com/policies/shipping-policy states only
  free shipping to Germany from 150 EUR.
- TCGViert: https://tcgviert.com/policies/shipping-policy states free shipping
  to Germany from 150 EUR and Austria from 200 EUR, nothing for Denmark.
- Bulk Paradise: https://bulkparadise-tcg.de/versand/ states only
  "Gratis Versand ab 150 EUR" with no country list.
- TcgReus: https://www.tcgreus.nl/policies/shipping-policy gives delivery terms
  but no rates.
- The TCG Plug: https://www.thetcgplug.nl/policies/shipping-policy says the
  cost is shown at checkout.
- Energy Vault: https://energy-vault.be/policies/shipping-policy names the
  options but no rates.
- PokeTalk: https://www.poketalk.se/policies/shipping-policy covers Sweden only.
- TCG Company: https://tcgcompany.nl/klantenservice/verzendkosten/ returns 404.
- Kelz0r: no shipping page found.

---

## D2. Supporting APIs

### TCGdex, canonical set list

- Data licence: https://github.com/tcgdex/cards-database states the
  cards-database repository is MIT licensed, with the disclaimer "This
  database is not produced, endorsed, supported or affiliated with Nintendo
  or The Pokémon Company".
- Usage: https://tcgdex.dev/faq states "No. The TCGdex API is free to use and
  requires no API key" and "There are no published hard rate limits, but
  please be considerate. For bulk data needs, cache responses locally rather
  than fetching the same data repeatedly."
- Overview: https://tcgdex.dev/ describes it as "a 14 languages API for the
  Pokémon TCG, having detailed data on cards, sets and others" and notes
  "The API is costly to run".
- Open item: `https://api.tcgdex.net/v2/en/sets` and
  `https://api.tcgdex.net/robots.txt` could not be fetched in this session;
  the fetch tool reported the URLs disallowed by robots.txt. The endpoint
  response therefore remains unverified, and the host's robots policy is
  unknown. Two options: call the documented API directly from the pipeline as
  the API's own documentation intends, or vendor the MIT-licensed
  cards-database repository into PokeArb and refresh it on a schedule. The
  second avoids the question entirely and honours the FAQ's "cache responses
  locally" guidance, so it is the recommended route.

### ECB, currency conversion

- Reuse terms:
  https://www.ecb.europa.eu/services/disclaimer/html/index.en.html states
  "Copyright © for the entire content of this website: European Central Bank,
  Frankfurt am Main, Germany", and permits free use provided that "When such
  information is distributed or reproduced, it must appear accurately and the
  ECB must be cited as the source", that sellers tell buyers the information
  "may be obtained free of charge through this website", and that "If the
  information is modified by the user (e.g. by seasonal adjustment of
  statistical data or calculation of growth rates) this must be stated
  explicitly."
- API documentation: https://data.ecb.europa.eu/help/api/data describes the
  RESTful query structure `protocol://wsEntryPoint/resource/flowRef/key?parameters`
  and formats including CSV, JSON and SDMX-ML. It references
  `https://data-api.ecb.europa.eu/service/Dataflow` for metadata queries. The
  page states no rate limits, fair-use policy or client-identification
  requirement.
- Open item: `https://data-api.ecb.europa.eu/service/data/EXR/D.DKK.EUR.SP00.A`
  and `https://data-api.ecb.europa.eu/robots.txt` could not be fetched in this
  session (503 on robots.txt, then a parse failure), so the exact series
  response is unverified. The DKK, SEK and EUR daily reference-rate series
  keys need a live confirmation before the FX module is written.
- Attribution to carry on the site: "Source: European Central Bank" plus a
  note that PokeArb converts published reference rates to DKK, which is a
  modification within the meaning of the ECB reuse terms.

---

## E. Standing crawl policy for every approved source

- One named User-Agent across the whole pipeline, with a contact URL, e.g.
  `PokeArbBot/1.0 (+https://<github-pages-url>/om; kontakt: <email>)`.
- Minimum 2.0 s between requests to the same host, single-threaded per host.
- Honour HTTP 429 and `Retry-After`. On 429 without `Retry-After`, back off
  exponentially from 60 s and abandon the host for the day after three
  failures.
- Honour any per-agent `Crawl-delay` when it is longer than 2.0 s.
- Conditional requests (`If-None-Match`, `If-Modified-Since`) wherever the
  host sends validators.
- Never request cart, checkout, account or order paths. Never place an order.
- On a failed run, retain the shop's last good prices and stamp them with the
  date they were captured, rather than dropping the shop from the site.
- Full run budget: roughly 10 shops, paginated feeds, which at 2 s per request
  is well inside a single daily GitHub Actions job.
