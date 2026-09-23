"""Turn a raw shop title into a canonical (set, product type, language).

The classifier is deliberately conservative. Anything it cannot place is
dropped rather than guessed at, and a set name it cannot match against the
TCGdex list is kept verbatim rather than snapped to the nearest thing.

Order of operations matters:
  1. reject titles that are not Pokemon at all
  2. reject accessories, singles, graded slabs, cases, empties and damaged stock
  3. detect the product type
  4. detect the language
  5. strip the noise and fuzzy-match what is left against the set list
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from .models import Language, ProductType


def normalise(title: str) -> str:
    """Lowercase, strip accents, collapse punctuation into single spaces."""
    text = unicodedata.normalize("NFKD", title)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# 1. Other trading card games and non-Pokemon lines
# --------------------------------------------------------------------------

OTHER_TCG = (
    "lorcana", "magic the gathering", "mtg", "yu gi oh", "yugioh", "yu gi",
    "one piece", "digimon", "dragon ball", "flesh and blood", "naruto",
    "star wars unlimited", "weiss schwarz", "vanguard", "metazoo",
    "match attax", "topps", "panini", "fifa", "uefa", "union arena",
    "gundam", "riftbound", "league of legends", "sorcery", "altered",
    "battle spirits", "duel masters", "grand archive", "shadowverse",
    "disney", "marvel", "garbage pail", "hot wheels", "wow tcg", "keyforge",
)

POKEMON_MARKERS = ("pokemon", "pokmon", "pocket monsters")


def is_pokemon(norm: str) -> bool:
    """A title counts as Pokemon only if it says so and names no rival game.

    Shops frequently omit the word "Pokemon" on set-specific listings, so a
    known set name recovered later can also vouch for a title. That second
    chance is applied by classify(), not here.
    """
    if any(game in norm for game in OTHER_TCG):
        return False
    return any(marker in norm for marker in POKEMON_MARKERS)


# --------------------------------------------------------------------------
# 2. Exclusions
# --------------------------------------------------------------------------

def _boundary_matcher(terms: tuple[str, ...]) -> re.Pattern:
    """Match whole words only. Substring matching gave false positives:
    the Dutch "lege" (empty) fired inside "Legends"."""
    alternatives = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])")


ACCESSORY_TERMS = (
    # sleeves and protection, da/de/nl/sv/en
    "sleeve", "sleeves", "hylster", "hylstre", "hulle", "hullen", "huelle",
    "kartenhulle", "toploader", "top loader", "card saver", "perfect fit",
    "penny sleeve", "protector", "beskyttelse", "schutzhulle", "skyddsfickor",
    # binders and storage
    "binder", "ringordner", "sammelalbum", "portfolio", "mappe", "parm",
    "album", "ordner", "verzamelmap", "opbergmap", "samlemappe", "flip file",
    "storage box", "opbevaringsboks", "aufbewahrungsbox", "card box",
    # play accessories
    "playmat", "play mat", "spillemate", "spielmatte", "speelmat", "spelmatta",
    "deck box", "deckbox", "dice", "wurfel", "tarning", "terning", "munt",
    "coin box", "counter", "damage counter", "sleeve pack",
    # display furniture and grading supplies
    "acryl", "acrylic", "akryl", "plexiglas", "display case", "display stand",
    "displaycase", "stativ", "halter", "houder", "case for", "holder for",
    "magnetic holder", "magnetholder", "one touch", "screwdown",
    # merchandise
    "plush", "bamse", "plusch", "knuffel", "figur", "figure", "funko",
    "keychain", "nogleringe", "schlusselanhanger", "t shirt", "tshirt",
    "mug", "krus", "tasse", "poster", "plakat", "puslespil", "puzzle",
    "sticker", "klistermaerker", "backpack", "rygsaek", "lunch box",
    # gift/promo boxes that are none of the five types but say "display" or
    # "box" in the title, e.g. "Card Display Set Gift Box Eevee (CHN)"
    "gift box", "giftbox", "geschenkbox", "geschenkset", "gaveaeske", "cadeaubox",
)
ACCESSORY_RE = _boundary_matcher(ACCESSORY_TERMS)

GRADED_TERMS = (
    "psa", "psa10", "psa 10", "bgs", "cgc", "ace 10", "graded", "gradet",
    "slab", "slabbed", "beckett", "sgc",
)
GRADED_RE = _boundary_matcher(GRADED_TERMS)

CASE_TERMS = (
    "master case", "sealed case", "case of", "umkarton", "karton mit",
    "6er case", "12er case", "full case", "hel kasse", "hele kasse",
    "bundle of 6", "sampak", "multipack", "multi pack", "lot of",
    "booster box case", "booster case", "box case", "display case of",
    "case", "kasse", "karton", "carton",
    "bundle collezioni", "bundle mazzi", "display completo",
)
CASE_RE_EXTRA = None

# A half display is a different unit from a full one. Left in, TcgReus's
# "Halve Booster Box" at 1.288 kr sat in the same table as full boxes at
# 3.000 kr and invented a 58 percent saving.
PART_UNIT_TERMS = (
    "halve booster box", "half booster box", "halve box", "half box",
    "halbes display", "halbe display", "halv booster box", "halv display",
    "halve display", "half display", "18er display", "quarter display",
    "split display", "loose pack", "single pack", "losse booster",
)
PART_UNIT_RE = None
CASE_RE = _boundary_matcher(CASE_TERMS)

EMPTY_TERMS = (
    "empty", "tom", "tomt", "tom box", "tom aeske", "leer", "leere", "leerbox",
    "lege doos", "no cards", "uden kort", "ohne karten", "zonder kaarten",
    "only box", "kun aesken", "box only", "nur box",
)
EMPTY_RE = _boundary_matcher(EMPTY_TERMS)

DAMAGED_TERMS = (
    "damaged", "beskadiget", "beschadigt", "beschaedigt", "dented", "bulk damaged",
    "b vare", "b ware", "2 wahl", "zweite wahl", "skadad", "skadet", "dinge",
    "creased", "reduced box", "nedsat aeske", "beschadigde", "poor condition",
    "kosmetisk skade", "kosmetiske skader", "cosmetic damage", "cosmetically damaged",
    "kosmetischer schaden", "kosmetische schade", "let skade", "minor damage",
)
DAMAGED_TERMS = DAMAGED_TERMS + (
    "damage", "schade", "beschadiging", "met schade", "deuk", "deukje",
    "imperfect", "imperfection", "imperfections", "imperfecte", "kleine damage",
    # Spanish, Italian, French. Pokemillon lists "(Dañada)" boxes alongside
    # undamaged ones at a lower price.
    "danada", "danado", "caja danada", "defectuosa", "defectuoso", "golpeada",
    "danneggiato", "danneggiata", "ammaccato", "ammaccata", "rovinata",
    "abime", "abimee", "endommage", "endommagee",
)
DAMAGED_RE = _boundary_matcher(DAMAGED_TERMS)

# Sealed products that are none of the five types, but whose titles can still
# contain "display" or "box": a display of mini tins, a checklane blister.
OTHER_FORM_TERMS = (
    "mini tin", "mini tins", "tin", "tins", "blister", "checklane",
    "promo pack", "sleeved booster", "blind box",
)
OTHER_FORM_RE = _boundary_matcher(OTHER_FORM_TERMS)
PART_UNIT_RE = _boundary_matcher(PART_UNIT_TERMS)

# A card number like 085/081 or 25/111 is the tell for a single card.
SINGLE_CARD_NUMBER = re.compile(r"\b\d{1,3}\s*/\s*\d{1,3}\b")
# Explicit quantities of sealed boxes: "6x display", "x6 booster box".
MULTI_QUANTITY = re.compile(r"\b(?:[2-9]|[1-9]\d+)\s*x\b|\bx\s*(?:[2-9]|[1-9]\d+)\b")


@dataclass(frozen=True)
class Rejection:
    reason: str


def rejection_reason(norm: str, raw: str | None = None) -> str | None:
    """`raw` is the untouched title. Pass it whenever you have it: the card
    number test needs the slash that normalise() strips."""
    for label, pattern in (
        ("tilbehoer", ACCESSORY_RE),
        ("gradet kort", GRADED_RE),
        ("kasse med flere bokse", CASE_RE),
        ("tom aeske", EMPTY_RE),
        ("beskadiget", DAMAGED_RE),
        ("halv eller delt enhed", PART_UNIT_RE),
        ("anden produkttype", OTHER_FORM_RE),
    ):
        if hit := pattern.search(norm):
            return f"{label} ({hit.group(0)})"
    if SINGLE_CARD_NUMBER.search(raw if raw is not None else norm):
        return "enkeltkort (kortnummer i titlen)"
    if MULTI_QUANTITY.search(norm):
        return "flere enheder i samme vare"
    return None


# --------------------------------------------------------------------------
# 3. Product type
# --------------------------------------------------------------------------

POKEMON_CENTER_TERMS = ("pokemon center", "pokemoncenter", "pokemon center etb", "pc etb")

# Ordered: the first match wins, so the most specific patterns come first.
# Covers the words shops actually print in Danish, Swedish, German, Dutch,
# English, French, Spanish and Italian.
TYPE_PATTERNS: tuple[tuple[ProductType, tuple[str, ...]], ...] = (
    (
        ProductType.UPC,
        (
            "ultra premium collection", "ultra premium kollektion",
            "ultra premium collectie", "ultra premium samling",
            "ultra premium collection box", "ultra premium", "upc",
            "coleccion ultra premium", "collezione ultra premium",
            "collection ultra premium", "coffret ultra premium",
        ),
    ),
    (
        ProductType.BOOSTER_BUNDLE,
        (
            "booster bundle", "boosterbundle", "booster bundel",
            "boosterbundel", "bundle pack", "booster bundt",
            "bundle 6 buste", "bundle di 6 buste", "bundle di buste",
            "bundle 6 sobres", "bundle de sobres", "bundle da 6 buste",
        ),
    ),
    (
        ProductType.ETB,
        (
            "elite trainer box", "elitetrainerbox", "elite trainerbox",
            "top trainer box", "top trainer boxen", "toptrainerbox",
            "trainer box", "trainerbox", "etb", "ttb",
            "elite trainer", "top trainer",
            # French, Spanish, Italian
            "coffret dresseur d elite", "dresseur d elite",
            "caja de entrenador elite", "entrenador elite",
            "set allenatore fuoriclasse", "allenatore fuoriclasse", "set allenatore",
        ),
    ),
    (
        ProductType.BOOSTER_BOX,
        (
            "booster box", "boosterbox", "booster display", "boosterdisplay",
            "display box", "displaybox", "36er display", "30er display",
            "display", "boosterbrick", "booster brick",
            # Spanish and Italian
            "caja sellada", "caja de sobres", "caja sobres",
            "box di espansione", "box espansione",
        ),
    ),
)

# "Caja 20 Sobres", "Caja sellada 30 sobres", "Box 36 Buste". Full-size
# counts only; 18 packs is a half display and is rejected separately.
BOX_OF_PACKS = re.compile(
    r"(?<![a-z0-9])(?:caja|box|display)(?![a-z0-9]).*?(?<![0-9])(?:20|24|30|36)\s+(?:sobres|buste|bustine)(?![a-z0-9])"
    r"|(?<![0-9])(?:20|24|30|36)\s+(?:sobres|buste|bustine)(?![a-z0-9]).*?(?<![a-z0-9])(?:caja|box|display)(?![a-z0-9])"
)


TYPE_MATCHERS = tuple(
    (product_type, _boundary_matcher(tuple(p.strip() for p in patterns)))
    for product_type, patterns in TYPE_PATTERNS
)
POKEMON_CENTER_RE = _boundary_matcher(POKEMON_CENTER_TERMS)


def detected_types(norm: str) -> list[ProductType]:
    """Every product type the title mentions, in priority order."""
    found = [pt for pt, pattern in TYPE_MATCHERS if pattern.search(norm)]
    if ProductType.BOOSTER_BOX not in found and BOX_OF_PACKS.search(norm):
        found.append(ProductType.BOOSTER_BOX)
    return found


def detect_type(norm: str) -> ProductType | None:
    found = detected_types(norm)
    if not found:
        return None
    first = found[0]
    if first is ProductType.ETB and POKEMON_CENTER_RE.search(norm):
        return ProductType.ETB_POKEMON_CENTER
    return first


# --------------------------------------------------------------------------
# 4. Language
# --------------------------------------------------------------------------

# Spelled-out language words, matched as whole words anywhere in the title.
# Checked in this order, so an Asian-language tag beats an incidental English
# word, and a localised word beats the English default.
LANGUAGE_WORDS: tuple[tuple[Language, tuple[str, ...]], ...] = (
    (Language.JA, ("japansk", "japanese", "japans", "japanisch", "japanska", "japonais",
                   "japanse", "japones", "giapponese", "jpn", "jap", "jp")),
    (Language.KO, ("korean", "koreansk", "koreanisch", "koreaans", "koreanska", "coreano",
                   "coreen", "kor")),
    (Language.ZH, ("chinese", "kinesisk", "chinesisch", "chinees", "kinesiska", "chino",
                   "cinese", "chinois", "simplified chinese", "traditional chinese",
                   "s chn", "t chn", "chn", "cn", "zh")),
    (Language.DE, ("deutsch", "german", "tysk", "duits", "allemand", "tyska", "aleman",
                   "tedesco", "ger")),
    (Language.FR, ("french", "francais", "franzosisch", "fransk", "frans", "frances",
                   "francese", "fra")),
    (Language.ES, ("spanish", "spansk", "spanisch", "spaans", "espanol", "espagnol",
                   "spagnolo", "castellano")),
    (Language.IT, ("italian", "italiensk", "italienisch", "italiaans", "italiano",
                   "italien", "ita")),
    (Language.EN, ("english", "engelsk", "englisch", "engels", "engelska", "ingles",
                   "inglese", "anglais", "eng")),
)
LANGUAGE_WORD_RES = tuple((lang, _boundary_matcher(words)) for lang, words in LANGUAGE_WORDS)

# Bare two-letter codes are ambiguous: "de" is a Spanish, Dutch and French
# preposition, "it" and "es" are ordinary words. They count only when a shop
# has clearly written them as a tag: "(DE)", "[IT]", "- ES" or last in the title.
CODE_LANG = {
    "de": Language.DE, "fr": Language.FR, "es": Language.ES,
    "it": Language.IT, "en": Language.EN,
}
TAG_RE = re.compile(
    r"(?:[\(\[]\s*|\s[-–|/]\s*)(de|fr|es|it|en)\s*(?=[\)\]]|$|\s[-–|/])",
    re.IGNORECASE,
)


def detect_language(norm: str, raw: str | None = None) -> Language | None:
    """The language the title declares, or None when it declares none.

    The caller decides the fallback: an explicit tag wins, then the language
    of the set name that matched, then English.
    """
    for language, pattern in LANGUAGE_WORD_RES:
        if pattern.search(norm):
            return language
    if raw:
        if tag := TAG_RE.search(raw):
            return CODE_LANG[tag.group(1).lower()]
    tokens = norm.split()
    if tokens and tokens[-1] in CODE_LANG:
        return CODE_LANG[tokens[-1]]
    return None


# --------------------------------------------------------------------------
# 5. Set matching
# --------------------------------------------------------------------------

# Words removed before the fuzzy match, so that "Pokemon TCG Surging Sparks
# Booster Display (EN)" reduces to "surging sparks".
NOISE_WORDS = frozenset(
    """
    pokemon pokmon tcg trading card game sammelkartenspiel kortspil kaartspel
    kortspel booster box boxes bundle bundles display displays elite trainer
    top toptrainer trainerbox etb ttb upc ultra premium collection kollektion
    collectie samling new nyhed neu nieuw ny sealed forseglet versiegelt
    verzegeld forseglad preorder forudbestilling vorbestellung
    reservering release udgivelse the of and a an
    en eng english engelsk englisch engels engelska
    de ger deutsch german tysk duits tyska
    jp jpn jap japansk japanese japans japanisch japanska
    kor korean koreansk koreanisch koreaans
    cn chn chinese kinesisk chinesisch chinees
    fr fra french francais franzosisch fransk frans
    scarlet violet karmesin purpur ecarlate
    base basis center centre pakker pakke stk stuck stuecke
    editie edition ausgabe udgave english engelse booster boosters brick
    6er 10er 12er 20er 24er 30er 36er limit kunde kunden max maks stk pr
    alm moms brugtmoms normal sets sellada sobres sobre caja cajas buste bustine
    busta completo coffret dresseur entrenador allenatore fuoriclasse coleccion
    collezione japones coreano chino simplificado simplified tradicional
    traditional ingles inglese espanol italiano ita frances francese giapponese
    cinese spagnolo anglais chinois coreen del la el con da di le et d l it es
    vorbestellung forudbestilling reservering
    sword shield schwert schild zwaard schild svard skold
    """.split()
)

# Set codes shops bolt on to the name. Western: "SV08.5", "KP08", "SWSH12".
# Japanese and Korean: "s6a", "sv4K", "SV9a", "M1L", "SV11B".
# Chinese: "CSV3C", "CS3aC", "CBB5C", "151C".
# normalise() has already turned "SV08.5" into "sv08 5", so the trailing
# fragment is handled by the digit filter below rather than here.
SET_CODE = re.compile(
    r"(?<![a-z0-9])(?:"
    r"c?s(?:v|wsh|m)?\d{1,2}[a-z]?c?"     # sv08, s6a, swsh12, csv3c, cs3ac
    r"|c?bb?\d{1,2}c?"                     # cbb5c, bb3
    r"|kp\d{1,2}"                          # German KP08
    r"|m\d{1,2}[a-z]?"                      # M1L, M2a, M3, M6a
    r"|mbe?\d{1,2}"                         # German MBE4
    r"|\d{2,3}thc"                          # Chinese 30thC
    r"|ev\d{1,2}|xy\d{1,2}|bw\d{1,2}"
    r"|\d{2,3}c"                           # 151C
    r")(?![a-z0-9])"
)


# A number right after one of these belongs to the set name, not to a pack
# count: "Gem Pack Vol. 4" is a different product from "Gem Pack Vol. 5".
ORDINAL_LEAD = frozenset({"vol", "volume", "nr", "no", "part", "serie", "series", "gen"})
# ...and a number right before one of these: "30 Jahre", "30 Anniversario".
ORDINAL_TRAIL = frozenset({"jahre", "anniversario", "aniversario", "anniversary", "anniversaire"})


PRE_ORDER = re.compile(r"(?<![a-z0-9])pre\s?order(?![a-z0-9])")
SCRIPT_TAG = re.compile(r"(?<![a-z0-9])[st]\s+(?:chn|chinese|chinesisch|chino)(?![a-z0-9])")


def extract_set_candidate(norm: str) -> str:
    text = PRE_ORDER.sub(" ", norm)
    text = SET_CODE.sub(" ", text)
    # "[S-CHN]" and "[T-CHN]" leave a stray "s" or "t". Only those go: a
    # single letter can be part of a real name, as in "Inferno X".
    text = SCRIPT_TAG.sub(" ", text)
    words = [w for w in text.split() if w not in NOISE_WORDS]

    kept: list[str] = []
    for i, word in enumerate(words):
        following = words[i + 1] if i + 1 < len(words) else ""
        if word.isdigit() and not (kept and kept[-1] in ORDINAL_LEAD) and following not in ORDINAL_TRAIL:
            continue  # a pack count or a year
        kept.append(word)

    # "151" is a real set, so when the filter removes everything the numbers
    # come back rather than leaving an empty candidate.
    return " ".join(kept if kept else words).strip()


def _word_in(word: str, words: set[str]) -> bool:
    """Exact membership, or a one-letter spelling difference on long words."""
    if word in words:
        return True
    if len(word) < 6:
        return False
    try:
        from rapidfuzz.fuzz import ratio
    except ImportError:  # pragma: no cover
        import difflib

        def ratio(a, b):
            return 100 * difflib.SequenceMatcher(None, a, b).ratio()
    return any(len(other) >= 6 and ratio(word, other) >= 90 for other in words)


class SetMatcher:
    """Fuzzy-match a candidate string against the live TCGdex set list.

    `aliases` maps a normalised set name (in any of the fetched languages) to
    the canonical English set record. Below the threshold nothing is returned,
    and the caller keeps the raw string.
    """

    def __init__(self, aliases: dict[str, dict], threshold: int = 88):
        self.aliases = aliases
        self.threshold = threshold
        self._keys = list(aliases.keys())
        try:
            from rapidfuzz import fuzz, process  # noqa: F401

            self._backend = "rapidfuzz"
        except ImportError:  # pragma: no cover - fallback path
            self._backend = "difflib"

    # A match must cover at least this share of the set name's own words.
    MIN_COVERAGE = 0.6

    def match(self, candidate: str) -> tuple[dict | None, int]:
        if not candidate or not self._keys:
            return None, 0
        # Two-letter leftovers are never a set name on their own.
        if not any(len(word) >= 3 for word in candidate.split()):
            return None, 0

        # An exact hit beats any fuzzy score and costs nothing.
        if candidate in self.aliases:
            return self.aliases[candidate], 100

        if self._backend == "rapidfuzz":
            from rapidfuzz import fuzz, process

            hit = process.extractOne(
                candidate, self._keys, scorer=fuzz.token_set_ratio
            )
            if hit is None:
                return None, 0
            name, score, _ = hit
            score = int(score)
        else:  # pragma: no cover - fallback path
            import difflib

            names = difflib.get_close_matches(candidate, self._keys, n=1, cutoff=0.0)
            if not names:
                return None, 0
            name = names[0]
            score = int(100 * difflib.SequenceMatcher(None, candidate, name).ratio())

        if score < self.threshold:
            return None, score

        # token_set_ratio ignores tokens the candidate has and the set name
        # does not, which is exactly the wrong behaviour here: it matched
        # "Mega Evolution Perfect Order", "Mega Evolution Pitch Black" and
        # three other distinct boxes all onto the set "Mega Evolution", and
        # then reported a 44 percent saving between two different products.
        # Any leftover word means this is not that set.
        candidate_words = set(candidate.split())
        name_words = set(name.split())

        # Any word the set name does not have means a different product. A
        # near-identical long word still counts as the same word, so
        # "Storm Emerald" and "Storm Emeralda", two shops' spellings of one
        # Japanese set, meet; "Pitch Black" and "Perfect Order" still do not.
        leftover = {w for w in candidate_words if not _word_in(w, name_words)}
        if leftover:
            return None, score

        # And the other way round: "ex" alone must not claim "Shiny Treasure ex".
        covered = sum(1 for w in name_words if _word_in(w, candidate_words)) / max(1, len(name_words))
        if covered < self.MIN_COVERAGE:
            return None, score

        return self.aliases[name], score


# --------------------------------------------------------------------------
# The public entry point
# --------------------------------------------------------------------------


@dataclass
class Classification:
    accepted: bool
    reason: str = ""
    product_type: ProductType | None = None
    language: Language | None = None
    set_id: str | None = None
    set_name: str = ""
    set_release_date: str | None = None
    set_matched: bool = False
    match_score: int = 0


# Series names shops print before the set: "Mega Evolution—Delta Reign",
# "Sword & Shield: Brilliant Stars". Three of them are also the name of the
# series' first set, which is why they cannot simply be noise words.
SERIES_NAMES = (
    "heartgold soulsilver", "diamond and pearl", "diamond pearl", "black and white",
    "black white", "sun and moon", "sun moon", "sword and shield", "sword shield",
    "scarlet and violet", "scarlet violet", "mega evolution", "platinum", "xy",
)


def _strip_series(candidate: str) -> str | None:
    for series in SERIES_NAMES:
        if candidate.startswith(series + " "):
            rest = candidate[len(series) + 1:].strip()
            return rest or None
    return None


def _match_allowing_series(candidate: str, matcher: "SetMatcher"):
    record, score = matcher.match(candidate)
    if record is not None:
        return record, score, candidate
    rest = _strip_series(candidate)
    if rest:
        record, score = matcher.match(rest)
        if record is not None:
            return record, score, rest
    return None, score, candidate


def resolve_set(candidate: str, title: str, matcher: "SetMatcher"):
    """Match a candidate that failed as a whole.

    Two routes, both keeping the rule that every word must be accounted for:
      * a leading series name may be dropped when what follows is a set:
        "mega evolution delta reign" is Delta Reign
      * a title with separators is tried segment by segment, and a segment
        match counts only when the words outside it are a series name or
        another name for the same set: "Evoluciones de Paldea | Paldea
        Evolved" is Paldea Evolved, but "Mega Evolution: Pitch Black" is not
        Mega Evolution
    """
    record, score, used = _match_allowing_series(candidate, matcher)
    if record is not None:
        return record, score, used

    full_words = candidate.split()
    for segment in title_segments(title):
        seg = extract_set_candidate(normalise(segment))
        if not seg or seg == candidate:
            continue
        seg_record, seg_score, seg_used = _match_allowing_series(seg, matcher)
        if seg_record is None:
            continue
        remaining = list((Counter(full_words) - Counter(seg_used.split())).elements())
        remaining = [w for w in full_words if w in remaining]  # keep word order
        rest = " ".join(remaining)
        if not rest or rest in SERIES_NAMES:
            return seg_record, seg_score, seg_used
        other, _, _ = _match_allowing_series(rest, matcher)
        if other is not None and other.get("id") == seg_record.get("id"):
            return seg_record, seg_score, seg_used
    return None, score, candidate


SEGMENT_SPLIT = re.compile(r"\s*(?:\||\s[-\u2013\u2014/]\s|[\u2014:])\s*")


def title_segments(title: str) -> list[str]:
    """The parts of a title between separators, longest first."""
    parts = [p.strip() for p in SEGMENT_SPLIT.split(title) if p and p.strip()]
    return sorted(parts, key=len, reverse=True) if len(parts) > 1 else []


# Which Language a set-name alias implies, by the TCGdex list it came from.
ALIAS_LANGUAGES = {
    "de": Language.DE, "fr": Language.FR, "es": Language.ES, "it": Language.IT,
}


def classify(title: str, matcher: SetMatcher | None = None) -> Classification:
    norm = normalise(title)
    if not norm:
        return Classification(False, "tom titel")

    if any(game in norm for game in OTHER_TCG):
        return Classification(False, "andet kortspil")

    if reason := rejection_reason(norm, title):
        return Classification(False, reason)

    types = detected_types(norm)
    if not types:
        return Classification(False, "ikke en af de fem produkttyper")
    # "Booster Bundle Display", "Display 36 Buste + Set Allenatore": a listing
    # that names two product types is a case or a combination, not one box.
    if len(set(types)) > 1:
        return Classification(False, "kombinationsvare (flere produkttyper)")
    product_type = detect_type(norm)

    candidate = extract_set_candidate(norm)
    record, score = (matcher.match(candidate) if matcher else (None, 0))

    if record is None and matcher is not None:
        record, score, candidate = resolve_set(candidate, title, matcher)

    # A title that never says "Pokemon" is accepted only when the set name
    # matched the official list. That keeps "Naruto ... Display" out without
    # dropping "Surging Sparks Display".
    if not is_pokemon(norm) and record is None:
        return Classification(False, "ikke identificeret som Pokemon")

    # An explicit tag wins. Otherwise the language of the set name that
    # matched: "Buio Pesto" is the Italian name, so the box is Italian even
    # when the shop forgot to say so. English only as the last resort.
    language = detect_language(norm, title)
    if language is None and record is not None:
        language = ALIAS_LANGUAGES.get(record.get("_alias_lang", "en"))
    if language is None:
        language = Language.EN

    if record is not None:
        return Classification(
            accepted=True,
            product_type=product_type,
            language=language,
            set_id=record["id"],
            set_name=record["name"],
            set_release_date=record.get("releaseDate"),
            set_matched=True,
            match_score=score,
        )

    # A candidate of only short tokens is noise, not a set name: a bare "Ex"
    # left over from "Terapagos ex" is not something to build a page around.
    if not candidate or not any(len(word) >= 3 for word in candidate.split()):
        return Classification(False, "ingen brugbart saetnavn i titlen")

    return Classification(
        accepted=True,
        product_type=product_type,
        language=language,
        set_id=None,
        set_name=candidate.title(),
        set_release_date=None,
        set_matched=False,
        match_score=score,
    )
