# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Search languages for the football territories used by Flashscore."""

from html import unescape
from functools import lru_cache
import json
from pathlib import Path
import re
import unicodedata


# Flashscore's English football menu and mobile score headings supply the names.
# Ordered languages reflect local football coverage in multilingual territories.
_COUNTRIES = {
    "albania": ("AL", "sq"),
    "algeria": ("DZ", "ar fr"),
    "andorra": ("AD", "ca es"),
    "angola": ("AO", "pt"),
    "antigua and barbuda": ("AG", "en"),
    "argentina": ("AR", "es"),
    "armenia": ("AM", "hy ru"),
    "aruba": ("AW", "pap nl"),
    "australia": ("AU", "en"),
    "austria": ("AT", "de"),
    "azerbaijan": ("AZ", "az"),
    "bahrain": ("BH", "ar"),
    "bangladesh": ("BD", "bn"),
    "barbados": ("BB", "en"),
    "belarus": ("BY", "ru be"),
    "belgium": ("BE", "nl fr de"),
    "benin": ("BJ", "fr"),
    "bermuda": ("BM", "en"),
    "bhutan": ("BT", "en"),
    "bolivia": ("BO", "es"),
    "bosnia and herzegovina": ("BA", "bs hr sr"),
    "botswana": ("BW", "en"),
    "brazil": ("BR", "pt"),
    "bulgaria": ("BG", "bg"),
    "burkina faso": ("BF", "fr"),
    "burundi": ("BI", "fr sw en"),
    "cambodia": ("KH", "km"),
    "cameroon": ("CM", "fr en"),
    "canada": ("CA", "en fr"),
    "cabo verde": ("CV", "pt"),
    "central african republic": ("CF", "fr"),
    "chad": ("TD", "fr ar"),
    "chile": ("CL", "es"),
    "china": ("CN", "zh"),
    "colombia": ("CO", "es"),
    "comoros": ("KM", "fr ar"),
    "costa rica": ("CR", "es"),
    "croatia": ("HR", "hr"),
    "cuba": ("CU", "es"),
    "cyprus": ("CY", "el tr"),
    "czechia": ("CZ", "cs"),
    "denmark": ("DK", "da"),
    "djibouti": ("DJ", "fr ar so"),
    "dominican republic": ("DO", "es"),
    "dr congo": ("CD", "fr"),
    "ecuador": ("EC", "es"),
    "egypt": ("EG", "ar"),
    "el salvador": ("SV", "es"),
    "england": ("GB", "en"),
    "equatorial guinea": ("GQ", "es fr pt"),
    "estonia": ("EE", "et"),
    "eswatini": ("SZ", "en"),
    "ethiopia": ("ET", "am"),
    "faroe islands": ("FO", "fo da"),
    "fiji": ("FJ", "en"),
    "finland": ("FI", "fi sv"),
    "france": ("FR", "fr"),
    "gabon": ("GA", "fr"),
    "gambia": ("GM", "en"),
    "georgia": ("GE", "ka"),
    "germany": ("DE", "de"),
    "ghana": ("GH", "en"),
    "gibraltar": ("GI", "en"),
    "greece": ("GR", "el"),
    "guadeloupe": ("GP", "fr"),
    "guatemala": ("GT", "es"),
    "guinea": ("GN", "fr"),
    "guinea bissau": ("GW", "pt"),
    "haiti": ("HT", "ht fr"),
    "honduras": ("HN", "es"),
    "hong kong": ("HK", "zh en"),
    "hungary": ("HU", "hu"),
    "iceland": ("IS", "is"),
    "india": ("IN", "hi en bn ml ta"),
    "indonesia": ("ID", "id"),
    "iran": ("IR", "fa"),
    "iraq": ("IQ", "ar ckb"),
    "ireland": ("IE", "en"),
    "israel": ("IL", "he ar"),
    "italy": ("IT", "it"),
    "ivory coast": ("CI", "fr"),
    "jamaica": ("JM", "en"),
    "japan": ("JP", "ja"),
    "jordan": ("JO", "ar"),
    "kazakhstan": ("KZ", "kk ru"),
    "kenya": ("KE", "sw en"),
    "kosovo": ("XK", "sq sr"),
    "kuwait": ("KW", "ar"),
    "kyrgyzstan": ("KG", "ky ru"),
    "laos": ("LA", "lo"),
    "latvia": ("LV", "lv"),
    "lebanon": ("LB", "ar fr"),
    "lesotho": ("LS", "en"),
    "liberia": ("LR", "en"),
    "libya": ("LY", "ar"),
    "liechtenstein": ("LI", "de"),
    "lithuania": ("LT", "lt"),
    "luxembourg": ("LU", "lb fr de"),
    "macao": ("MO", "zh pt"),
    "madagascar": ("MG", "mg fr"),
    "malawi": ("MW", "en"),
    "malaysia": ("MY", "ms en"),
    "mali": ("ML", "fr"),
    "maldives": ("MV", "en"),
    "malta": ("MT", "mt en"),
    "martinique": ("MQ", "fr"),
    "mauritania": ("MR", "ar fr"),
    "mauritius": ("MU", "fr en"),
    "mexico": ("MX", "es"),
    "moldova": ("MD", "ro ru"),
    "mongolia": ("MN", "mn"),
    "montenegro": ("ME", "sr sq"),
    "morocco": ("MA", "ar fr"),
    "mozambique": ("MZ", "pt"),
    "myanmar": ("MM", "my"),
    "nepal": ("NP", "ne"),
    "netherlands": ("NL", "nl"),
    "new caledonia": ("NC", "fr"),
    "new zealand": ("NZ", "en"),
    "nicaragua": ("NI", "es"),
    "niger": ("NE", "fr"),
    "nigeria": ("NG", "en"),
    "north korea": ("KP", "ko"),
    "north macedonia": ("MK", "mk sq"),
    "northern ireland": ("GB", "en"),
    "norway": ("NO", "nb nn"),
    "oman": ("OM", "ar"),
    "pakistan": ("PK", "ur en"),
    "palestine": ("PS", "ar"),
    "panama": ("PA", "es"),
    "paraguay": ("PY", "es"),
    "peru": ("PE", "es"),
    "philippines": ("PH", "fil en"),
    "poland": ("PL", "pl"),
    "portugal": ("PT", "pt"),
    "puerto rico": ("PR", "es en"),
    "qatar": ("QA", "ar"),
    "republic of the congo": ("CG", "fr"),
    "romania": ("RO", "ro"),
    "russia": ("RU", "ru"),
    "rwanda": ("RW", "rw en fr sw"),
    "san marino": ("SM", "it"),
    "saudi arabia": ("SA", "ar"),
    "scotland": ("GB", "en"),
    "senegal": ("SN", "fr"),
    "serbia": ("RS", "sr"),
    "seychelles": ("SC", "en fr"),
    "sierra leone": ("SL", "en"),
    "singapore": ("SG", "en zh ms ta"),
    "slovakia": ("SK", "sk"),
    "slovenia": ("SI", "sl"),
    "somalia": ("SO", "so ar"),
    "south africa": ("ZA", "en af zu"),
    "south korea": ("KR", "ko"),
    "south sudan": ("SS", "en"),
    "spain": ("ES", "es ca gl eu"),
    "sri lanka": ("LK", "si ta en"),
    "sudan": ("SD", "ar en"),
    "suriname": ("SR", "nl"),
    "sweden": ("SE", "sv"),
    "switzerland": ("CH", "de fr it"),
    "syria": ("SY", "ar"),
    "tahiti": ("PF", "fr"),
    "taiwan": ("TW", "zh"),
    "tajikistan": ("TJ", "tg ru"),
    "tanzania": ("TZ", "sw en"),
    "thailand": ("TH", "th"),
    "togo": ("TG", "fr"),
    "trinidad and tobago": ("TT", "en"),
    "tunisia": ("TN", "ar fr"),
    "turkey": ("TR", "tr"),
    "turkmenistan": ("TM", "tk ru"),
    "uganda": ("UG", "en sw"),
    "ukraine": ("UA", "uk"),
    "united arab emirates": ("AE", "ar"),
    "usa": ("US", "en es"),
    "uruguay": ("UY", "es"),
    "uzbekistan": ("UZ", "uz ru"),
    "venezuela": ("VE", "es"),
    "vietnam": ("VN", "vi"),
    "wales": ("GB", "en cy"),
    "yemen": ("YE", "ar"),
    "zambia": ("ZM", "en"),
    "zimbabwe": ("ZW", "en"),
}

_COUNTRY_ALIASES = {
    "antigua barbuda": "antigua and barbuda",
    "bosnia herzegovina": "bosnia and herzegovina",
    "cape verde": "cabo verde",
    "chinese taipei": "taiwan",
    "congo": "republic of the congo",
    "congo dr": "dr congo",
    "cote d ivoire": "ivory coast",
    "czech republic": "czechia",
    "democratic republic of the congo": "dr congo",
    "korea republic": "south korea",
    "macau": "macao",
    "macedonia": "north macedonia",
    "republic of ireland": "ireland",
    "republic of korea": "south korea",
    "swaziland": "eswatini",
    "trinidad tobago": "trinidad and tobago",
    "turkiye": "turkey",
    "uae": "united arab emirates",
    "united kingdom": "england",
    "united states": "usa",
    "united states of america": "usa",
    "us": "usa",
}

# Keep the term as text even when YouTube uses another display language.
# Other entries add match-specific variants for recognition and all scripts
# used by the same language (e.g. Serbian Latin and Cyrillic).
_TERMS = {
    "af": ("wedstrydopsomming", "hoogtepunte"),
    "am": ("የጨዋታው ማጠቃለያ",),
    "ar": ("ملخص المباراة", "ملخص", "أهداف المباراة", "أهداف مباراة"),
    "az": ("oyunun icmalı", "matçın icmalı", "oyunun xülasəsi"),
    "be": ("агляд матча", "агляд гульні"),
    "bg": ("обзор на мача", "репортаж от мача", "обзор"),
    "bn": ("ম্যাচের হাইলাইটস", "হাইলাইটস"),
    "bs": ("sažetak utakmice", "najbolji trenuci"),
    "ca": ("resum del partit", "resum"),
    "ckb": ("پوختەی یاری",),
    "cs": ("sestřih zápasu", "sestřih", "sestřih utkání"),
    "cy": ("uchafbwyntiau'r gêm", "uchafbwyntiau"),
    "da": ("kampens højdepunkter", "højdepunkter"),
    "de": ("Spielzusammenfassung", "Zusammenfassung"),
    "el": ("στιγμιότυπα αγώνα", "στιγμιότυπα", "γκολ και φάσεις"),
    "en": ("highlights", "relive the best moments of"),
    "es": ("resumen", "goles del partido", "resumen del encuentro"),
    "et": ("mängu kokkuvõte", "mängu tipphetked"),
    "eu": ("partidaren laburpena",),
    "fa": ("خلاصه بازی", "خلاصه مسابقه", "خلاصه"),
    "fi": ("ottelukooste", "kooste", "maalikooste"),
    "fil": ("mga tampok ng laban",),
    "fo": ("samandráttur", "samandráttur frá dystinum"),
    "fr": ("résumé du match", "résumé", "temps forts du match", "meilleurs moments"),
    "gl": ("resumo do partido",),
    "he": ("תקציר המשחק", "תקציר"),
    "hi": ("मैच हाइलाइट्स", "हाइलाइट्स"),
    "hr": ("sažetak utakmice", "sažetak"),
    "ht": ("rezime match la", "rezime match"),
    "hu": ("mérkőzés összefoglaló", "összefoglaló"),
    "hy": ("խաղի ամփոփում", "հանդիպման ամփոփում", "խաղի տեսություն"),
    "id": ("cuplikan pertandingan", "cuplikan gol"),
    "is": ("samantekt leiks", "svipmyndir"),
    "it": ("sintesi della partita", "sintesi", "gol e azioni salienti"),
    "ja": ("ハイライト", "試合ハイライト", "ダイジェスト"),
    "ka": ("მატჩის მიმოხილვა", "მატჩის მომენტები"),
    "kk": ("матчқа шолу", "ойынға шолу", "матч шолуы"),
    "km": ("វីដេអូសង្ខេបការប្រកួត", "សង្ខេបការប្រកួត"),
    "ko": ("하이라이트", "경기 하이라이트"),
    "ky": ("матчтын обзору", "оюндун обзору"),
    "lb": ("resumé vum match", "resumé vum spill"),
    "lo": ("ໄຮໄລ້ການແຂ່ງຂັນ", "ໄຮໄລ້"),
    "lt": ("rungtynių santrauka", "rungtynių apžvalga"),
    "lv": ("spēles apskats", "spēles spilgtākie momenti"),
    "mg": ("famintinana ny lalao",),
    "mk": ("преглед на натпреварот", "најдобри моменти", "голови и шанси"),
    "ml": ("മത്സര ഹൈലൈറ്റുകൾ", "ഹൈലൈറ്റുകൾ"),
    "mn": ("тоглолтын тойм", "тоглолтын онцлох мөчүүд"),
    "ms": ("sorotan perlawanan", "ringkasan perlawanan", "aksi tumpuan"),
    "mt": ("sommarju tal-partita", "l-aqwa siltiet"),
    "my": ("ပွဲစဉ် အနှစ်ချုပ်", "အကောင်းဆုံး အခိုက်အတန့်များ"),
    "nb": ("høydepunkter", "kampens høydepunkter"),
    "ne": ("खेलको हाइलाइट्स", "हाइलाइट्स"),
    "nl": ("samenvatting", "wedstrijdsamenvatting", "hoogtepunten"),
    "nn": ("høgdepunkt", "høgdepunkt frå kampen"),
    "pap": ("resúmen di partido",),
    "pl": ("skrót meczu", "skrót spotkania", "najlepsze akcje meczu"),
    "pt": ("resumo do jogo", "resumo", "melhores momentos",
           "gols do jogo", "gols da partida", "golos do jogo"),
    "ro": ("rezumatul meciului", "rezumat", "goluri și ocazii"),
    "ru": ("обзор матча", "обзор", "лучшие моменты", "голы и моменты"),
    "rw": ("incamake y'umukino",),
    "si": ("තරගයේ විශේෂ අවස්ථා",),
    "sk": ("zostrih zápasu", "zostrih", "zostrih stretnutia"),
    "sl": ("povzetek tekme", "vrhunci tekme"),
    "so": ("soo koobista ciyaarta", "goos gooska ciyaarta"),
    "sq": ("përmbledhja e ndeshjes", "përmbledhje", "golat e ndeshjes"),
    "sr": ("сажетак утакмице", "sažetak utakmice",
           "најбољи моменти", "najbolji momenti", "голови и шансе"),
    "sv": ("höjdpunkter", "matchsammandrag", "sammandrag"),
    "sw": ("muhtasari wa mechi", "magoli na matukio"),
    "ta": ("போட்டியின் சிறப்பம்சங்கள்", "போட்டிச் சுருக்கம்"),
    "tg": ("шарҳи бозӣ", "лаҳзаҳои беҳтарини бозӣ"),
    "th": ("ไฮไลท์ฟุตบอล", "ไฮไลต์การแข่งขัน", "ไฮไลท์", "ไฮไลต์"),
    "tk": ("oýnuň gysgaça syny", "duşuşygyň syny"),
    "tr": ("maç özeti", "maçın özeti", "özet görüntüler"),
    "uk": ("огляд матчу", "відеоогляд", "гол та огляд", "гол і огляд"),
    "ur": ("میچ کی جھلکیاں", "میچ ہائی لائٹس"),
    "uz": ("o'yin sharhi", "uchrashuv sharhi", "ўйин шарҳи"),
    "vi": ("tóm tắt trận đấu", "diễn biến chính"),
    "zh": ("比赛集锦", "賽事精華", "集锦", "集錦", "全場精華", "全场精彩"),
    "zu": ("amaphuzu avelele omdlalo",),
}

_REGIONAL_TERMS = {
    ("BR", "pt"): "melhores momentos",
    ("HK", "zh"): "賽事精華",
    ("MO", "zh"): "賽事精華",
    ("TW", "zh"): "賽事精華",
}

_INTERNATIONAL = {
    "africa": ("en", "ar", "fr", "pt", "sw"),
    "asia": ("en", "ar", "fa", "ja", "ko", "zh"),
    "australia and oceania": ("en", "fr"),
    "europe": ("en", "de", "es", "fr", "it", "nl", "pt", "tr"),
    "north and central america": ("en", "es", "fr"),
    "south america": ("es", "pt", "en", "nl"),
    "world": (),
}

_LANGUAGE_ALIASES = {"iw": "he", "in": "id", "tl": "fil", "no": "nb"}
_UNSPACED = frozenset(("ja", "km", "ko", "lo", "my", "th", "zh"))
# Flashscore appends these country codes to clubs in cross-border fixtures.
_CLUB_COUNTRIES = {
    "alg": "algeria", "arg": "argentina", "aus": "australia", "aut": "austria",
    "bel": "belgium", "bra": "brazil", "chi": "chile", "chn": "china", "cmr": "cameroon",
    "cro": "croatia", "cze": "czechia", "den": "denmark", "egy": "egypt", "eng": "england",
    "esp": "spain", "fin": "finland", "fra": "france", "ger": "germany", "gha": "ghana",
    "gre": "greece", "ind": "india", "irn": "iran", "ita": "italy", "jpn": "japan",
    "kor": "south korea", "mar": "morocco", "mli": "mali", "mne": "montenegro", "mya": "myanmar",
    "ned": "netherlands", "nor": "norway", "phl": "philippines", "pol": "poland", "por": "portugal",
    "prk": "north korea", "rou": "romania", "rsa": "south africa", "rus": "russia",
    "sco": "scotland", "slo": "slovenia", "srb": "serbia", "sui": "switzerland", "svk": "slovakia",
    "swe": "sweden", "tur": "turkey", "uga": "uganda", "ukr": "ukraine", "usa": "usa",
    "uzb": "uzbekistan", "vie": "vietnam",
}
_CLUB_COUNTRY = re.compile(r"^(.+?)\s+\(([a-z]{3})\)$", re.IGNORECASE)
# Remove only a national side's qualifier while identifying its country.
_NATIONAL_SIDE = re.compile(r"^(.+?)((?:\s+(?:u\d{2}|w|women|reserves?))+)$", re.IGNORECASE)
_FOOTBALL_COUNTRY_NAMES = {
    "england": {"bg": "Англия", "de": "England", "es": "Inglaterra", "fr": "Angleterre", "it": "Inghilterra",
                "nl": "Engeland", "pl": "Anglia", "pt": "Inglaterra", "tr": "İngiltere", "uk": "Англія"},
    "northern ireland": {"es": "Irlanda del Norte", "fr": "Irlande du Nord", "tr": "Kuzey İrlanda"},
    "scotland": {"es": "Escocia", "fr": "Écosse", "tr": "İskoçya"},
    "wales": {"es": "Gales", "fr": "Pays de Galles", "tr": "Galler"},
}


def country_key(value):
    normal = unicodedata.normalize("NFKD", value.casefold().replace("&", " and ").replace("ı", "i"))
    plain = "".join(char for char in normal if not unicodedata.combining(char))
    return " ".join(re.sub(r"[\W_]+", " ", plain).split())


def _country_entry(key):
    return _COUNTRIES.get(_COUNTRY_ALIASES.get(key, key))


def country_region(country):
    entry = _country_entry(country_key(country))
    return entry[0] if entry else ""


def international_territory(value):
    return country_key(value) in _INTERNATIONAL


@lru_cache(maxsize=1)
def _territory_names():
    path = Path(__file__).with_name("match_highlights_countries.json")
    try:
        names = json.loads(path.read_text(encoding="utf-8"))["names"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return names if isinstance(names, dict) else {}


def local_country_name(country, locale):
    region = country_region(country)
    language = locale.split("-", 1)[0]
    # GB is shared by four distinct football associations in the locale table.
    if region == "GB":
        return _FOOTBALL_COUNTRY_NAMES.get(country_key(country), {}).get(language, country)
    if not region:
        return country
    if language == "zh" and locale.rsplit("-", 1)[-1] in ("HK", "MO", "TW"):
        language = "zh-Hant"
    names = _territory_names()
    return names.get(language, {}).get(region) or names.get("en", {}).get(region, country)


def international_team(name):
    """Return the territory, team name without its country code, and national qualifier."""
    club = _CLUB_COUNTRY.fullmatch(name)
    if club and club[2].lower() in _CLUB_COUNTRIES:
        return _CLUB_COUNTRIES[club[2].lower()], club[1], None
    side = _NATIONAL_SIDE.fullmatch(name)
    country, suffix = side.groups() if side else (name, "")
    return country if country_region(country) else "", country, suffix


def search_locale(country, device_locale):
    """Return the YouTube language/region and local keyword for this heading."""
    key = country_key(country)
    device = device_locale.replace("_", "-").split("-", 1)[0].lower()
    device = _LANGUAGE_ALIASES.get(device, device)
    entry = _country_entry(key)
    if entry:
        region, choices = entry
        languages = choices.split()
        language = device if device in languages else languages[0]
        keyword = _REGIONAL_TERMS.get((region, language), _TERMS[language][0])
        locale = "zh-Hant-MO" if (language, region) == ("zh", "MO") else "{}-{}".format(language, region)
        return locale, keyword
    choices = _INTERNATIONAL.get(key, ())
    if choices:
        if device in choices:
            return device_locale, _TERMS[device][0]
        return ("es-AR", _TERMS["es"][0]) if choices[0] == "es" else ("en-US", _TERMS["en"][0])
    return "en-US", _TERMS["en"][0]


def _keyword_text(value):
    # Preserve combining marks in scripts such as Thai, Devanagari and Khmer.
    # Accented Latin words are also matched when YouTube omits their accents.
    value = unicodedata.normalize("NFC", value)
    chars = []
    for char in unicodedata.normalize("NFKD", value.casefold().replace("ı", "i")):
        category = unicodedata.category(char)
        if category.startswith("M"):
            if chars and chars[-1].isascii() and chars[-1].isalpha():
                continue
        elif not char.isalnum():
            char = " "
        chars.append(char)
    return " ".join(unicodedata.normalize("NFC", "".join(chars)).split())


def _keyword_pattern():
    spaced = set()
    unspaced = set()
    for language, terms in _TERMS.items():
        target = unspaced if language in _UNSPACED else spaced
        target.update(_keyword_text(term) for term in terms)
    for (_, language), term in _REGIONAL_TERMS.items():
        (unspaced if language in _UNSPACED else spaced).add(_keyword_text(term))

    def alternatives(terms):
        return "|".join(re.escape(term) for term in sorted(terms, key=lambda term: (-len(term), term)))

    return re.compile(r"(?<!\S)(?:{spaced})(?!\S)|(?:{unspaced})".format(
        spaced=alternatives(spaced), unspaced=alternatives(unspaced)))


_KEYWORD_PATTERN = _keyword_pattern()


def has_local_highlight_keyword(value):
    return _KEYWORD_PATTERN.search(_keyword_text(unescape(value))) is not None
