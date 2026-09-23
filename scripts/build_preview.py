"""Bundle the rendered site into one self-contained HTML file.

The real site is several hundred static pages. A single-file preview can be
opened anywhere without a server, so this script renders the site exactly as
the pipeline would, then folds every page into a <template> and routes between
them on the URL hash. Nothing is re-implemented: the markup, the stylesheet and
the front-end script are the site's own.

What differs from production, and is stated on the page itself:
  * FX rates come from Danmarks Nationalbank, fetched once, not the ECB feed
  * the set list is the local test stub, not TCGdex
  * it is a frozen snapshot, not refreshed daily

    python scripts/build_preview.py out.html
"""

from __future__ import annotations

import base64
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bs4 import BeautifulSoup  # noqa: E402

from pokearb import history, render  # noqa: E402
from pokearb.classify import SetMatcher, normalise  # noqa: E402
from pokearb.config import ASSUMED_SHIPPING_DKK, SHOPS, STATIC_DIR  # noqa: E402
from pokearb.fx import FxRates  # noqa: E402
from pokearb.pipeline import _dedupe_within_shop, build_products, load_last_good  # noqa: E402
from smoke_test import STUB_SETS  # noqa: E402

# Danmarks Nationalbank, rate date 2026-09-21, fetched from
# https://www.nationalbanken.dk/api/currencyratesxml?lang=da
# The feed quotes DKK per 100 units: EUR 747,55 and SEK 66,30.
# Converted to the ECB convention the pipeline uses (units per 1 EUR):
DKK_PER_EUR = 7.4755
DKK_PER_SEK = 0.6630
FX_AS_OF = "2026-09-21"
FX = FxRates({"EUR": 1.0, "DKK": DKK_PER_EUR, "SEK": DKK_PER_EUR / DKK_PER_SEK}, FX_AS_OF)


def route_for(rel: str) -> str:
    """Map a site path to a hash-safe route token (letters, digits, - _ . ~)."""
    rel = rel.replace("\\", "/")
    if rel == "index.html":
        return "home"
    if rel.startswith("produkt/"):
        return "p-" + rel[len("produkt/"):-5]
    if rel.startswith("saet/"):
        return "s-" + rel[len("saet/"):-5]
    if rel.startswith("butik/"):
        return "b-" + rel[len("butik/"):-5]
    return rel[:-5]  # besparelser, samling, om, liste, butikker


def rewrite_links(soup: BeautifulSoup) -> None:
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("http://", "https://", "#", "mailto:")):
            continue
        clean = re.sub(r"^(\.\./)+", "", href)
        if clean.endswith(".html"):
            token = route_for(clean)
            a["href"] = "#" if token == "home" else "#" + token


def main(out_path: str) -> int:
    offers = []
    run_log = []
    for shop in SHOPS:
        shop_offers = load_last_good(shop)
        for offer in shop_offers:
            offer.stale = False  # captured in this run
        offers.extend(shop_offers)
        run_log.append(
            {
                "shop": shop.key,
                "name": shop.name,
                "status": "ok" if shop_offers else "failed",
                "count": len(shop_offers),
                "captured_at": shop_offers[0].captured_at if shop_offers else None,
            }
        )

    # The local list stands in for TCGdex only so titles can be matched. Its
    # dates are not verified, so none are shown: in production the release
    # dates come from TCGdex.
    stub = [dict(s, releaseDate=None) for s in STUB_SETS]
    matcher = SetMatcher({normalise(s["name"]): s for s in stub})
    products, stats = build_products(offers, matcher, FX)
    _dedupe_within_shop(products)
    products = [p for p in products if p.offers]
    history.record(products)

    payload = {
        "generated_at": offers[0].captured_at if offers else FX_AS_OF,
        "fx": {"as_of": FX_AS_OF, "stale": False, "rates": FX.rates},
        "assumed_shipping_dkk": dict(ASSUMED_SHIPPING_DKK),
        "shops": run_log,
        "stats": stats,
    }

    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp)
        render.SITE_DIR = site
        render.render_site(payload, products)

        pages = sorted(p for p in site.rglob("*.html"))
        index_soup = BeautifulSoup((site / "index.html").read_text("utf-8"), "lxml")

        # Chrome shared by every page: header, shipping bar, footer.
        header = index_soup.select_one("header.site-header")
        shipbar = index_soup.select_one("div.shipping-bar")
        footer = index_soup.select_one("footer.site-footer")
        for part in (header, shipbar, footer):
            rewrite_links(part)

        # In production the footer credits the ECB and TCGdex. This snapshot
        # used neither, so it says what it did use.
        for para in footer.find_all("p"):
            text = para.get_text(" ", strip=True)
            if text.startswith("Kilde for valutakurser"):
                para.clear()
                para.append(
                    "Kilde for valutakurser i denne forhåndsvisning: Danmarks "
                    f"Nationalbank, {render.dk_date(FX_AS_OF)}. I drift bruger PokeArb "
                    "ECB's referencekurser. Sætlisten er en lokal testliste, ikke TCGdex."
                )

        # The viewer provides the theme, so the page's own toggle is removed
        # rather than left to fight it.
        toggle = header.select_one("#theme-toggle")
        if toggle:
            toggle.decompose()

        logo_b64 = base64.b64encode((STATIC_DIR / "logo.svg").read_bytes()).decode()
        for img in header.find_all("img"):
            img["src"] = f"data:image/svg+xml;base64,{logo_b64}"

        templates = []
        for page in pages:
            rel = page.relative_to(site).as_posix()
            soup = BeautifulSoup(page.read_text("utf-8"), "lxml")
            main = soup.select_one("main#indhold")
            title = soup.title.get_text(strip=True) if soup.title else "PokeArb"
            rewrite_links(main)
            inner = "".join(str(child) for child in main.children)
            token = route_for(rel)
            templates.append(
                f'<template id="pg-{token}" data-title="{title.replace(chr(34), "&quot;")}">{inner}</template>'
            )

        search_index = (site / "data" / "search-index.json").read_text("utf-8")
        offers_json = (site / "data" / "offers.json").read_text("utf-8")

    css = (STATIC_DIR / "style.css").read_text("utf-8")
    js = (STATIC_DIR / "app.js").read_text("utf-8")
    basket_js = (STATIC_DIR / "basket.js").read_text("utf-8")

    # --- adapt the site's own script to run once per route ----------------
    js = js.replace("(function () {\n  \"use strict\";", "window.paInit = function () {\n  \"use strict\";", 1)
    js = re.sub(r"\}\)\(\);\s*$", "};\n", js)
    # The host owns the theme; drop the stored-theme block.
    js = re.sub(
        r"/\* -{16} theme -{16} \*/.*?/\* -{16} watchlist",
        "/* ---------------- watchlist",
        js,
        flags=re.S,
    )
    # The collection page reads the embedded index instead of fetching it.
    js = js.replace(
        'fetch("data/search-index.json")\n      .then(function (r) { return r.json(); })',
        "Promise.resolve(window.PA_INDEX)",
    )
    # alert() never shows in the viewer; report inline instead.
    js = js.replace(
        'alert("Produktet blev ikke fundet. Vælg et fra listen.");',
        'var msg = document.getElementById("c-msg"); if (msg) msg.textContent = "Produktet blev ikke fundet. Vælg et fra listen.";',
    )
    js = js.replace(
        '\'<th scope="row"><a href="produkt/\' + encodeURIComponent(item.key) + \'.html">\'',
        '\'<th scope="row"><a href="#p-\' + encodeURIComponent(item.key) + \'">\'',
    )

    # Basket rows link to product pages by route, not by file.
    js = js.replace(
        "'<a href=\"' + root + 'produkt/' + encodeURIComponent(it.key) + '.html\">'",
        "'<a href=\"#p-' + encodeURIComponent(it.key) + '\">'",
    )
    assert "#p-' + encodeURIComponent(it.key)" in js, "basket link rewrite did not apply"

    shop_count = sum(1 for r in run_log if r["status"] == "ok")
    failed = [r["name"] for r in run_log if r["status"] != "ok"]
    matched = sum(1 for p in products if p.set_matched)
    multi = sum(1 for p in products if p.shop_count > 1)

    banner = f"""
<div class="preview-banner" role="note">
  <div class="wrap">
    <p><strong>Forhåndsvisning, ikke det levende site.</strong>
    Bygget af rigtige priser hentet fra {shop_count} butikker {render.dk_date(payload['generated_at'])}
    ({len(products)} produkter, {multi} hos mindst to butikker).
    {('Ingen data fra ' + ' og '.join(failed) + '.') if failed else ''}</p>
    <details>
      <summary>Hvad adskiller den fra drift</summary>
      <ul>
        <li><strong>Valuta:</strong> Danmarks Nationalbanks kurser fra {FX_AS_OF}
            (747,55 kr. pr. 100 EUR, 66,30 kr. pr. 100 SEK). I drift bruger PokeArb
            ECB's referencekurser, som ikke kunne hentes herfra.</li>
        <li><strong>Sætliste:</strong> en lokal testliste på {len(STUB_SETS)} sæt i
            stedet for TCGdex. Derfor står flere produkter "uden match" ({len(products) - matched}
            af {len(products)}) end de vil i drift, og der vises ingen udgivelsesdatoer,
            så sættene står ikke i udgivelsesrækkefølge. Datoerne kommer fra TCGdex i drift.</li>
        <li><strong>Historik:</strong> kun én dags priser, så prisgrafer og
            "Faldet i pris" er tomme. De fyldes op efter et par kørsler.</li>
        <li><strong>Øjebliksbillede:</strong> opdateres ikke. Min liste, samling
            og fragtsatser gemmes i din browser og virker som på det rigtige site.</li>
      </ul>
    </details>
  </div>
</div>"""

    extra_css = """
.preview-banner { background: var(--navy); color: #e8e7e2; font-size: 0.86rem; border-top: 1px solid rgba(255,255,255,0.12); }
.preview-banner .wrap { padding-block: 9px; }
.preview-banner p { margin: 0; }
.preview-banner strong { color: #ffffff; }
.preview-banner summary { cursor: pointer; color: #f2884f; margin-top: 4px; font-weight: 600; }
.preview-banner ul { margin: 6px 0 2px; padding-left: 18px; max-width: 60rem; }
.preview-banner li { margin-bottom: 3px; }
#c-msg { color: var(--bad-text); font-size: 0.88rem; min-height: 1.2em; }
"""

    router = """
(function () {
  var main = document.getElementById("indhold");
  function go() {
    var token = (location.hash || "").replace(/^#/, "") || "home";
    var tpl = document.getElementById("pg-" + token) || document.getElementById("pg-home");
    main.innerHTML = "";
    main.appendChild(tpl.content.cloneNode(true));
    if (token === "samling" && !document.getElementById("c-msg")) {
      var form = document.getElementById("add-form");
      if (form) { var p = document.createElement("p"); p.id = "c-msg"; p.setAttribute("role", "status"); form.after(p); }
    }
    document.title = tpl.getAttribute("data-title") || "PokeArb";
    window.scrollTo(0, 0);
    window.__paFilter = null;
    if (typeof window.paInit === "function") window.paInit();
  }
  window.addEventListener("hashchange", go);
  go();
})();
"""

    html = f"""<title>PokeArb</title>
<meta name="description" content="Forhåndsvisning af PokeArb: billigste europæiske pris på forseglede Pokémon-produkter, med fragt til Danmark.">
<style>
{css}
{extra_css}
</style>
{header}
{banner}
{shipbar}
<main id="indhold" class="wrap"></main>
{footer}
{''.join(templates)}
<script>window.PA_INDEX = {search_index};</script>
<script>window.PA_OFFERS = {offers_json};</script>
<script>
{basket_js}
</script>
<script>
{js}
</script>
<script>
{router}
</script>
"""
    Path(out_path).write_text(html, "utf-8")
    size_mb = len(html.encode("utf-8")) / 1_000_000
    print(f"Skrev {out_path}: {size_mb:.2f} MB, {len(templates)} sider, {len(products)} produkter")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "preview.html"))
