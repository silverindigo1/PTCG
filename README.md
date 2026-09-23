# PokeArb

Dansk prissammenligning for **forseglede** Pokemon TCG-produkter: Elite Trainer
Boxes, Pokemon Center ETB'er, booster boxes og displays, booster bundles og
Ultra Premium Collections. For hvert produkt vises den billigste europaeiske
butik der sender til Danmark, med alle priser omregnet til kroner.

Priserne er **samlede priser**: vare plus fragt til Danmark. At rangere paa
varens pris alene peger paa den forkerte butik hver gang pakken koster mere end
prisforskellen, hvilket over en graense som regel er tilfaeldet.

Statisk site, bygget en gang i doegnet af en GitHub Actions-workflow og
udgivet paa GitHub Pages. Prishistorikken ligger som JSON i selve repoet, saa
der er ingen database og ingen server at passe paa.

PokeArb er et uafhaengigt projekt. Det er hverken produceret, stoettet af eller
tilknyttet Nintendo, Creatures, Game Freak eller The Pokemon Company.

---

## Kom i gang

```bash
git clone <dit-repo> pokearb && cd pokearb
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests -q
```

Byg hele sitet, med live hentning fra alle butikker:

```bash
python -m pokearb.cli build --verbose
```

Med 25 butikker tager en fuld koersel omkring et kvarter. Det meste er ventetid: der gaar mindst to sekunder mellem
hver forespoergsel til den samme butik.

Under udvikling vil du naesten altid hellere have en enkelt butik:

```bash
python -m pokearb.cli build --only tcgviert        # ca. 20 sekunder
python -m pokearb.cli check-shop godofcards        # hent og vis, gem intet
python -m pokearb.cli render                       # byg kun sitet igen
```

Se resultatet lokalt. Brug en rigtig HTTP-server, ikke `file://`: siden
"Min samling" henter en JSON-fil, og browsere blokerer det over `file://`.

```bash
python -m http.server 8000 --directory site
```

### Kommandoer

| Kommando | Hvad den goer |
|---|---|
| `build` | henter priser og bygger `site/` |
| `build --offline` | bygger `site/` af de sidst gemte priser, uden netvaerk |
| `build --only a,b` | kun de navngivne butikker |
| `scrape` | henter priser, skriver `data/latest.json`, bygger ikke |
| `render` | bygger `site/` ud fra `data/latest.json` |
| `check-shop <noegle>` | henter en butik og viser hvad klassifikatoren gjorde ved hver titel |
| `scripts/smoke_test.py a b c` | koerer hele kaeden mod udvalgte butikker uden TCGdex og ECB |
| `probe-kelz0r` | tjekker om HTML-selektorerne til Kelz0r stadig passer |

---

## Saadan haenger det sammen

```
pokearb/
  config.py        butiksregistret. En Shop pr. butik, intet andet sted
  http.py          hoeflig HTTP-klient: User-Agent, pauser, 429, Retry-After
  sets.py          saetlisten, hentet live fra TCGdex
  fx.py            ECB-referencekurser, omregning til DKK
  classify.py      titel -> (saet, produkttype, sprog). Hjertet i projektet
  adapters/        en adapter pr. butiksteknologi, ikke pr. butik
  pipeline.py      orkestrering: hent, klassificer, omregn, gem
  history.py       daglig prishistorik som JSON i repoet
  render.py        statisk sitegenerering med Jinja2
  cli.py           kommandolinjen
templates/         Jinja2-skabeloner
static/            stylesheet, logo, frontend-JavaScript, kurv-optimering (basket.js)
data/              genereret: priser, historik, caches. Commitet med vilje
tests/             klassifikatortests med rigtige, optagede butikstitler
```

### Sider paa sitet

| Side | Hvad den viser |
|---|---|
| `index.html` | noegletal, "Bedste koeb lige nu", soegning, filtre og sortering (nyeste saet, stoerste rabat, flest butikker, pris) |
| `besparelser.html` | alle gode koeb og de stoerste prisforskelle |
| `liste.html` | Min liste: billigste samlede kurv for de overvaagede produkter |
| `butikker.html`, `butik/<noegle>.html` | pr. butik: varer paa lager, hvor tit den er billigst, prisindeks, fragtregel |
| `saet/<saet>.html`, `produkt/<noegle>.html` | alle butikker sorteret paa samlet pris, markedspris, prisspaend og prisgraf |
| `data/offers.json` | varer paa lager og fragtregler i DKK, laest af Min liste |

### Markedspris og gode koeb

Markedsprisen er **medianen** af de samlede priser hos butikker der har varen
paa lager, og den findes kun naar mindst tre butikker har den
(`MIN_SHOPS_FOR_MARKET` i `models.py`). Et godt koeb er en vare hvor den
billigste ligger mindst 5 % under (`render.deals`). Medianen er valgt fordi en
enkelt butik med en skaev pris saa ikke kan flytte den og skabe et falsk tilbud.

### Min liste

`static/basket.js` finder den billigste fordeling af flere varer paa tvaers af
butikker, med en fragt pr. butik og fri fragt over butikkens graense. Er der
hoejst 50.000 kombinationer, proeves de alle. Ellers bruges lokal soegning med
start i baade "hver vare hvor den er billigst" og "alt fra en butik", saa
resultatet aldrig er daarligere end de to. Butikker uden oplyst sats faar
brugerens egne fragttal. Testene ligger i `tests/basket_test.js` og koeres af
pytest via Node, hvis Node er installeret.

### Data der ligger i repoet

| Fil | Indhold | Slettes den? |
|---|---|---|
| `data/history/<aar>.json` | billigste pris pr. produkt pr. dag | nej, det er hele pointen |
| `data/last_good/<butik>.json` | seneste vellykkede hentning pr. butik | genskabes automatisk |
| `data/latest.json` | resultatet af seneste koersel | genskabes |
| `data/sets_cache.json` | saetdetaljer fra TCGdex, hentet en gang pr. saet | genskabes, men saa hentes alt igen |
| `data/fx.json` | seneste ECB-kurser | genskabes |

Sletter du `data/history/`, er prisgraferne vaek for altid. Der er ingen kopi.

---

## Tilfoej en ny butik

Tilladelsen kommer foerst. Koden er den nemme del.

### 1. Tjek robots.txt

Hent `https://<butik>/robots.txt` og find blokken for `User-agent: *`. Den
praecise sti du vil hente skal vaere tilladt, ikke bare forsiden. Notér ogsaa
enhver `Crawl-delay`.

Ingen robots.txt, altsaa HTTP 404, betyder at intet er forbudt. Det er et
gyldigt resultat, ikke en fejl. En robots.txt du ikke kan hente er derimod
ikke et gyldigt resultat: saa er butikken ikke tjekket, og den kommer ikke med.

### 2. Tjek handelsbetingelserne

Find betingelserne og soeg efter disse ord, paa alle de sprog butikken kan
taenkes at bruge:

> scraping, crawling, robot, robotter, Roboter, spider, edderkop, data mining,
> automatiseret, automatisiert, geautomatiseerd, automatiserad, bot,
> automated access

Dette trin er ikke til forhandling. Shopifys standardskabelon til
handelsbetingelser indeholder en klausul der forbyder "spiders, crawling of
scrapen", og butikker med en fuldstaendig aaben robots.txt kan udmaerket have
den skabelon liggende. De Broergrot i Holland er praecis det tilfaelde, og er
derfor holdt ude. Forbyder betingelserne automatiseret adgang, er butikken
ude, uanset hvad robots.txt siger.

Kan du ikke laese betingelserne, fordi butikkens egen robots.txt spaerrer for
`/policies/`, er butikken heller ikke tjekket. Hobbykort og Tradingtoys er ude
af netop den grund.

### 3. Find et maskinlaesbart katalog

I prioriteret raekkefoelge:

| Platform | Endpoint | Test |
|---|---|---|
| Shopify | `/products.json?limit=250&page=N` | `curl -s https://butik/products.json?limit=1` |
| WooCommerce | `/wp-json/wc/store/v1/products?per_page=100` | samme |
| Magento | GraphQL | skriv en ny adapter |
| Affiliate-feed | butikkens eget produktfeed | skriv en ny adapter |

HTML-parsing er sidste udvej. Den gaar i stykker naar butikken skifter tema,
og den er tungere for butikken end et feed.

Shopify oplyser sin valuta og sine leveringslande paa `/meta.json`. Tjek
`ships_to_countries` for `DK`.

### 4. Skriv det ned

Tilfoej en post i `SOURCES.md` med butik, land, platform, den praecise adresse
paa robots.txt og betingelserne, hvad du fandt, og datoen. Uden den post er
butikken ikke dokumenteret, og saa kan ingen senere afgoere om den stadig maa
vaere der.

### 5. Tilfoej den i koden

```python
Shop(
    key="minbutik",
    name="Min Butik",
    country="DE",
    currency="EUR",
    adapter="shopify",              # shopify | woocommerce | kelz0r
    base_url="https://minbutik.de",
    ships_to_dk=True,
    shipping_confirmed=True,        # False hvis butikken ikke oplyser landene
    shipping=Shipping(              # kun hvis butikken selv oplyser satsen
        amount=6.95,                # i butikkens egen valuta
        free_over=100.0,
        source_url="https://minbutik.de/versand",
        checked="2026-09-22",
        verified=True,
    ),
    collections=("pokemon",),       # narrow crawlet paa store generalistbutikker
),
```

Kontrollér derefter hvad klassifikatoren goer ved butikkens titler:

```bash
python -m pokearb.cli check-shop minbutik
```

Kig paa listen over afviste varer. Sorterer den noget fra den burde beholde,
er det `classify.py` der skal rettes, og en ny test i
`tests/fixtures/recorded_titles.json` der skal skrives, med den rigtige titel
kopieret ordret ind.

---

## Koer testene

```bash
python -m pytest tests -q          # alle
python -m pytest tests -v          # med navnet paa hvert enkelt tilfaelde
python -m pytest tests -k recorded # kun de optagede butikstitler
node tests/basket_test.js          # kun kurv-optimeringen
```

`tests/fixtures/recorded_titles.json` har to grupper, og forskellen betyder
noget. **`recorded`** er titler laest ordret fra butikkernes egne katalogfeeds,
med butik, endpoint og dato paa hver enkelt. Det er dem der er bevis for
hvordan butikker faktisk skriver. **`constructed`** er titler skrevet i haanden
for at daekke typer og sprog som stikproeven tilfaeldigvis ikke indeholdt.
Bland dem ikke sammen. Naar du retter klassifikatoren, saa tilfoej en rigtig
titel til `recorded`, ikke en du selv har fundet paa.

---

## Hvad den foerste fulde koersel afsloerede

Kaeden blev koert mod alle butikker 22-09-2026: 14.668 varianter ind, 786
priser accepteret, 505 produkter ud, 150 af dem hos to eller flere butikker.
Koerslen fandt syv fejl, som alle nu har en test i
`tests/test_classify.py` under "Regressions found by running all 13 shops":

1. **Saet blev slaaet sammen.** `token_set_ratio` ignorerer ord kandidaten har
   og saetnavnet ikke har, saa "Mega Evolution Chaos Rising", "Pitch Black",
   "Perfect Order", "Phantasmal Flames" og "Enhanced" matchede alle sammen
   saettet "Mega Evolution". Sitet viste en besparelse paa 44 % mellem to
   produkter, der ikke er det samme. Nu afvises et match, saa snart kandidaten
   har ord, saetnavnet ikke har.
2. **Et enkelt ord kaprede et saetnavn.** "Violet ex" mistede "violet" til
   stoejlisten, og "ex" alene matchede "Shiny Treasure ex". Nu skal et match
   daekke mindst 60 % af saetnavnets egne ord.
3. **Halve displays.** En "Halve Booster Box" til 1.288 kr stod i samme tabel
   som hele bokse til 3.000 kr.
4. **Kasser med flere bokse.** En "Booster Box Case" til 10.935 kr stod som en
   enkelt boks.
5. **Volumennumre forsvandt.** "Gem Pack Vol. 4" blev til "gem pack vol", som
   derefter matchede "Gem Pack Vol. 5".
6. **Antal over 19.** "60X Packs" slap igennem, fordi moensteret kun daekkede
   2 til 19.
7. **Kosmetisk skade.** "Kosmetisk skade" i titlen blev ikke set som en
   beskadiget vare.

Derudover: Matraws har 25.001 produkter, og at hente hele kataloget tog over
20 minutter. Butikken hentes nu kun via samlingerne `alt-pokemon` og
`pokemon-collection-boxes`, hvilket tager 16 sekunder og er langt lettere for
butikken. Det samme greb boer bruges paa enhver stor generalistbutik.

## Anden butiksrunde, 23-09-2026

47 kandidater mere blev tjekket, og 12 kom igennem. Resten, og hvorfor, staar
i `SOURCES.md`, afsnit E. To blev afvist fordi deres betingelser forbyder
scraping, selvom robots.txt tillader det (Play-Maniac og PokeDealTCG), fire
fordi deres robots.txt spaerrer for netop den side, betingelserne staar paa,
og resten fordi de ikke sender til Danmark eller ikke har et katalog der kan
laeses maskinelt.

De spanske og italienske butikker afsloerede fejl i klassifikatoren, som nu er
rettet og testet med deres egne titler: det spanske "de" blev laest som tysk,
"Caja sellada 30 sobres" blev ikke genkendt som en booster box, italienske
varer blev maerket engelske, og "(Dañada)" gik igennem som en ubeskadiget aeske.

## Hvordan klassifikatoren taenker

Raekkefoelgen er det vigtigste ved den:

1. **Er det overhovedet Pokemon?** Lorcana, Yu-Gi-Oh, One Piece, Topps og et
   par dusin andre spil ryger ud her. "Topps Match Attax UCC 2026/27 Booster
   Box" rammer ellers "booster box" perfekt.
2. **Er det noget vi ikke vil have?** Tilbehoer, enkeltkort, gradede kort,
   kasser med flere bokse, tomme aesker og beskadigede varer. Dette trin ligger
   *foer* typebestemmelsen, saa "Acrylic Display Case for Pokemon Elite Trainer
   Box" bliver tilbehoer og ikke en ETB.
3. **Hvilken af de fem typer?** Mest specifikke moenster vinder, saa
   "Ultra Premium Collection" slaar "Collection", og "Pokemon Center Elite
   Trainer Box" slaar den almindelige ETB.
4. **Hvilket sprog?** Engelsk, japansk, koreansk, kinesisk, tysk, fransk,
   spansk eller italiensk, fra ord i titlen (`Japansk`, `Coreano`, `Chino`,
   `ITA`, `Deutsch`) eller maerkater. En bar tobogstavskode som `de`, `es`
   eller `it` taeller kun som maerkat i parentes eller sidst i titlen: det
   spanske "Evoluciones de Paldea" er ikke tysk. Siger titlen intet, bruges
   sproget paa det saetnavn der matchede, og til sidst engelsk.
5. **Hvilket saet?** Stoej fjernes, og resten matches fuzzy mod TCGdex-listen
   med `token_set_ratio` og en taerskel paa 88. Rammer den ikke, beholdes
   navnet ordret og produktet markeres "uden match". Der gaettes aldrig.

Saetlisten hentes paa engelsk, tysk, fransk, hollandsk, spansk, italiensk og
portugisisk, saa "Rivalen am Abgrund" og "Surging Sparks" ender som det samme
produkt. De japanske, koreanske og kinesiske saet staar med latinske navne paa
den engelske liste, og det er dem butikkerne trykker, saa der skal ikke hentes
japansk eller koreansk.

Alle ordlister matches paa hele ord. Det er ikke pedanteri: substrengsmatch
fik hollandsk "lege", tom, til at ramme inde i "Legends".

---

## Kelz0r kraever et ord for sig

Kelz0r har intet maskinlaesbart katalog, saa adapteren parser HTML. Den blev
skrevet uden adgang til den levende DOM, saa **selektorerne i
`pokearb/adapters/kelz0r.py` er kvalificerede gaet og skal verificeres**:

```bash
python -m pokearb.cli probe-kelz0r
```

Kommandoen viser hvor mange produktblokke der blev fundet, hvilken selektor
der virkede, de foerste fem parsede varer, og, hvis intet blev parset, den
raa HTML saa du kan se den faktiske struktur. Ret `SELECTORS` oeverst i filen
til den passer.

Finder adapteren nul produkter, kaster den en fejl. Det er med vilje: en
tavs nulparse ville fjerne butikken fra sitet uden at nogen opdagede det.
I stedet beholder pipelinen butikkens sidste gode priser og markerer dem med
datoen.

---

## Hoeflighed over for butikkerne

Det her er reglerne, og de er haandhaevet i `http.py`, ikke overladt til den
der skriver en ny adapter.

- **En aerlig User-Agent** med en kontaktadresse. Saet `POKEARB_CONTACT_URL`.
- **Mindst 2 sekunder mellem forespoergsler til samme vaert**, en ad gangen.
  Kelz0r koerer paa 5 sekunder, fordi butikken saetter `Crawl-delay` op til 10
  for de bots den navngiver.
- **HTTP 429 respekteres.** `Retry-After` foelges naar den er der. Ellers
  eksponentiel backoff fra 60 sekunder, og efter tre forsoeg opgives vaerten
  for i dag.
- **Betingede forespoergsler** med `If-None-Match` og `If-Modified-Since` naar
  butikken sender en validator.
- **Kurv, checkout, login og bestilling roeres aldrig.** PokeArb laegger ikke
  ordrer.
- **Fejler en butik, beholdes dens sidste gode priser**, stemplet med den dato
  de blev hentet og markeret paa sitet. Butikken forsvinder ikke.

---

## Fragt

Hver butik i `config.py` har et `Shipping`-objekt. Er `verified=True`, er
satsen butikkens egen og `source_url` peger paa siden den staar paa. Er den
`False`, oplyser butikken den ikke, og sitet bruger en antagelse fra
`ASSUMED_SHIPPING_DKK`.

Antagelserne er PokeArbs egne pladsholdere. De praesenteres aldrig som
butikkens satser: hver gang en bruges, staar der "anslaaet" ved siden af
beloebet, og laeseren kan rette tallet i "Fragtsatser" oeverst paa siden.
Retter man det, regnes alle tabeller om og sorteres igen med det samme, og
valget gemmes i browseren.

Fire af tretten butikker oplyser en sats, alle fire tjekket 22-09-2026. De
staar i `SOURCES.md`, afsnit D, sammen med hvad de ni andre rent faktisk
skriver paa deres forsendelsessider.

Fri fragt-graenser sammenlignes i butikkens egen valuta, ikke i kroner, fordi
det er saadan butikken selv regner.

## Valuta

Omregning sker med ECB's daglige referencekurser fra
`data-api.ecb.europa.eu`. ECB opgiver alle kurser som enheder af den
fremmede valuta pr. 1 euro, saa en krydskurs er `kurs_DKK / kurs_valuta`.
Butikkens egen pris vises ved siden af kronebeloebet.

ECB's genbrugsvilkaar kraever at ECB angives som kilde, at det oplyses at
data kan hentes gratis hos ECB, og at bearbejdning oplyses eksplicit. En
omregning er en bearbejdning. Alle tre ting staar i sidefoden paa hver side.
Fjerner du dem, overholder sitet ikke laengere vilkaarene.

Svarer ECB ikke, genbruges seneste kurser, og sitet skriver hvilken dag de er
fra.

---

## Udgivelse

Workflowen i `.github/workflows/daily.yml` koerer 05:15 UTC hver dag og kan
startes i haanden fra Actions-fanen, eventuelt kun for udvalgte butikker.
Den koerer testene foerst: en oedelagt klassifikator ville skrive forkerte
priser ind i den permanente historik, og det kan ikke rulles tilbage bagefter.

Saadan saettes det op foerste gang:

1. **Settings, Pages, Source: GitHub Actions.**
2. **Settings, Actions, General, Workflow permissions: Read and write.**
   Workflowen committer dagens priser tilbage til repoet.
3. Koer workflowen i haanden en gang og se at `data/history/` faar en fil.

Prisgraferne har brug for mindst to koersler foer de dukker op. De foerste par
dage ser produktsiderne tomme ud paa det punkt, og det er som det skal vaere.

---

## Hvad sitet ikke ved

Staar der ogsaa paa siden "Om og kilder", og det boer det blive ved med.

- **Fragt er kun sikker hvor butikken oplyser den.** Resten er PokeArbs
  antagelse pr. land, markeret "anslaaet" og kan rettes af brugeren.
- **Told og gebyrer er ikke med.**
- **Levering til Danmark er tjekket hvor butikken oplyser det.** Tre butikker
  oplyser det ikke, og de er markeret baade i `config.py` og paa sitet.
- **Lagerstatus er butikkens eget felt.** Shopify oplyser kun ja eller nej,
  ikke antal.
- **Samlingens vaerdi er butikspris inklusive fragt, ikke salgspris.** Det du
  kan saelge for, ligger under.
- **"Faldet i pris" kraever tre maalinger.** Listen er tom de foerste dage
  efter en ny start, og det er som det skal vaere.
- **Overvaagningslisten ligger kun i din browser.** Der er ingen mail-besked og
  ingen konto.
- **Markedsprisen er butikkernes median, ikke hvad varen handles for.**
  Den siger hvor en pris ligger blandt butikkerne, ikke hvad eftermarkedet betaler.
- **Min liste kender kun fragt pr. ordre.** Vaegtbaseret fragt og rabatkoder
  er ikke med.

---

## Produktbilleder

Der er ingen. Ingen af de butikker, hvis betingelser er tjekket, giver lov til
at genbruge deres billeder, saa sitet kopierer ikke et eneste af dem. I stedet
tegner `templates/_tile.html` sin egen grafik ud fra produktnoeglen: samme
produkt giver samme flise hver gang, saa siden ikke flimrer mellem koersler.
Vil du have rigtige billeder, skal der foerst indhentes tilladelse, og saa boer
kilden og datoen skrives i `SOURCES.md` som alt andet.

## Tilladelser

`SOURCES.md` er registret over hvilke butikker der er tjekket, hvad der blev
fundet, og hvornaar. Hver post har de praecise adresser der blev hentet.
Tjek igen hver 90. dag. Betingelser aendrer sig uden varsel, og en butik der
var i orden i september kan have skiftet skabelon til december.
