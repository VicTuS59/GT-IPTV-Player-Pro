# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Standard gettext integration for GT IPTV Player Pro."""

import datetime
import gettext
import os
import threading

from .paths import plugin_path

try:
    from Components.Language import language as _language
except Exception:
    # Gettext also remains usable before language services load.
    _language = None

PLUGIN_DOMAIN = "GTIPTVPlayerPro"
LOCALE_PATH = plugin_path("locale")

SUPPORTED_LANGUAGE_CODES = (
    "ar", "bg", "ca", "cs", "da", "de", "el", "en", "es", "et",
    "fa", "fi", "fr", "fy", "gl", "he", "hr", "hu", "id", "is",
    "it", "ku", "lt", "lv", "mk", "nl", "nb", "nn", "pl", "pt",
    "pt_BR", "ro", "ru", "sk", "sl", "sr", "sv", "th", "tr", "uk",
    "vi", "zh_CN", "zh_HK", "en_AU", "en_GB", "sq", "ta",
)

_BASE_LANGUAGE_CODES = set(SUPPORTED_LANGUAGE_CODES)
_CATALOGS = {}
_CATALOG_LOCK = threading.RLock()
_CALLBACK_REGISTERED = False
_CALLBACK_LANGUAGE_SERVICE = None

# A small number of Enigma2 images expose legacy language identifiers instead
# of the current ISO codes.  Keep these aliases at the device boundary so the
# catalogue layout remains identical on every image.
_LANGUAGE_CODE_ALIASES = {
    "hk": "zh_HK",
    "no": "nb",
    "tw": "zh_HK",
}

_CATALOG_LANGUAGE_DIRS = {
    "fr": "fr_FR",
    "pt_BR": "pt",
    "sq": "sq_AL",
    "zh_CN": "zh",
}

# Every device language advertised by the plugin has a packaged UI catalogue.
# English variants intentionally use the source strings; regional variants
# listed in _CATALOG_LANGUAGE_DIRS share their complete base catalogue.
_CATALOG_LANGUAGE_CODES = frozenset(SUPPORTED_LANGUAGE_CODES)

_METADATA_LOCALES = {
    "ar": "ar-SA", "bg": "bg-BG", "ca": "ca-ES", "cs": "cs-CZ",
    "da": "da-DK", "de": "de-DE", "el": "el-GR", "en": "en-US",
    "en_AU": "en-AU", "en_GB": "en-GB", "es": "es-ES",
    "et": "et-EE", "fa": "fa-IR", "fi": "fi-FI", "fr": "fr-FR",
    "fy": "fy-NL", "gl": "gl-ES", "he": "he-IL", "hr": "hr-HR",
    "hu": "hu-HU", "id": "id-ID", "is": "is-IS", "it": "it-IT",
    "ku": "ku-TR", "lt": "lt-LT", "lv": "lv-LV", "mk": "mk-MK",
    "nl": "nl-NL", "nb": "nb-NO", "nn": "nn-NO", "pl": "pl-PL",
    "pt": "pt-PT", "pt_BR": "pt-BR", "ro": "ro-RO", "ru": "ru-RU",
    "sk": "sk-SK", "sl": "sl-SI", "sq": "sq-AL", "sr": "sr-RS",
    "sv": "sv-SE", "ta": "ta-IN", "th": "th-TH", "tr": "tr-TR",
    "uk": "uk-UA", "vi": "vi-VN", "zh_CN": "zh-CN",
    "zh_HK": "zh-HK",
}

_NATIVE_LANGUAGE_NAMES = {
    "ar": "العربية", "bg": "Български", "ca": "Català", "cs": "Čeština",
    "da": "Dansk", "de": "Deutsch", "el": "Ελληνικά", "en": "English",
    "en_AU": "English (Australia)", "en_GB": "English (UK)",
    "es": "Español", "et": "Eesti", "fa": "فارسی", "fi": "Suomi",
    "fr": "Français", "fy": "Frysk", "gl": "Galego", "he": "עברית",
    "hr": "Hrvatski", "hu": "Magyar", "id": "Bahasa Indonesia",
    "is": "Íslenska", "it": "Italiano", "ku": "Kurdî", "lt": "Lietuvių",
    "lv": "Latviešu", "mk": "Македонски", "nl": "Nederlands",
    "nb": "Norsk bokmål", "nn": "Norsk nynorsk", "pl": "Polski",
    "pt": "Português", "pt_BR": "Português (Brasil)", "ro": "Română",
    "ru": "Русский", "sk": "Slovenčina", "sl": "Slovenščina",
    "sq": "Shqip", "sr": "Српски", "sv": "Svenska", "ta": "தமிழ்",
    "th": "ไทย", "tr": "Türkçe", "uk": "Українська",
    "vi": "Tiếng Việt", "zh_CN": "简体中文", "zh_HK": "繁體中文",
}


def N_(value):
    """Mark text for extraction while deferring translation until display."""
    return value

_SHORT_WEEKDAYS = {
    "ar": ("الاث", "الث", "الأر", "الخ", "الج", "الس", "الأح"),
    "bg": ("Пон", "Вто", "Сря", "Чет", "Пет", "Съб", "Нед"),
    "ca": ("Dl", "Dt", "Dc", "Dj", "Dv", "Ds", "Dg"),
    "cs": ("Po", "Út", "St", "Čt", "Pá", "So", "Ne"),
    "da": ("Man", "Tir", "Ons", "Tor", "Fre", "Lør", "Søn"),
    "de": ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"),
    "el": ("Δευ", "Τρι", "Τετ", "Πεμ", "Παρ", "Σαβ", "Κυρ"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "es": ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"),
    "et": ("E", "T", "K", "N", "R", "L", "P"),
    "fa": (
        "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه",
        "جمعه", "شنبه", "یکشنبه",
    ),
    "fi": ("Ma", "Ti", "Ke", "To", "Pe", "La", "Su"),
    "fr": ("Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"),
    "fy": ("Mo", "Ti", "Wo", "To", "Fr", "Sne", "Sin"),
    "gl": ("Lun", "Mar", "Mér", "Xov", "Ven", "Sáb", "Dom"),
    "he": ("שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"),
    "hr": ("Pon", "Uto", "Sri", "Čet", "Pet", "Sub", "Ned"),
    "hu": ("H", "K", "Sze", "Cs", "P", "Szo", "V"),
    "id": ("Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"),
    "is": ("Mán", "Þri", "Mið", "Fim", "Fös", "Lau", "Sun"),
    "it": ("Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"),
    "ku": ("Duş", "Sêş", "Çar", "Pên", "În", "Şem", "Yek"),
    "lt": ("Pr", "An", "Tr", "Kt", "Pn", "Št", "Sk"),
    "lv": ("Pr", "Ot", "Tr", "Ce", "Pk", "Se", "Sv"),
    "mk": ("Пон", "Вто", "Сре", "Чет", "Пет", "Саб", "Нед"),
    "nb": ("Man", "Tir", "Ons", "Tor", "Fre", "Lør", "Søn"),
    "nl": ("Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"),
    "nn": ("Mån", "Tys", "Ons", "Tor", "Fre", "Lau", "Sun"),
    "pl": ("Pon", "Wt", "Śr", "Czw", "Pt", "Sob", "Niedz"),
    "pt": ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"),
    "pt_BR": ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"),
    "ro": ("Lun", "Mar", "Mie", "Joi", "Vin", "Sâm", "Dum"),
    "ru": ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"),
    "sk": ("Po", "Ut", "St", "Št", "Pi", "So", "Ne"),
    "sl": ("Pon", "Tor", "Sre", "Čet", "Pet", "Sob", "Ned"),
    "sq": ("Hën", "Mar", "Mër", "Enj", "Pre", "Sht", "Die"),
    "sr": ("Пон", "Уто", "Сре", "Чет", "Пет", "Суб", "Нед"),
    "sv": ("Mån", "Tis", "Ons", "Tor", "Fre", "Lör", "Sön"),
    "ta": ("திங்", "செவ்", "புத", "வியா", "வெள்", "சனி", "ஞாயி"),
    "th": ("จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา."),
    "tr": ("Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"),
    "uk": ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"),
    "vi": ("T2", "T3", "T4", "T5", "T6", "T7", "CN"),
    "zh_CN": ("周一", "周二", "周三", "周四", "周五", "周六", "周日"),
    "zh_HK": ("週一", "週二", "週三", "週四", "週五", "週六", "週日"),
}

_TRACK_LANGUAGE_NAMES = {
    "ar": N_("Arabic"),
    "ara": N_("Arabic"),
    "cs": N_("Czech"),
    "ces": N_("Czech"),
    "cze": N_("Czech"),
    "de": N_("German"),
    "deu": N_("German"),
    "ger": N_("German"),
    "el": N_("Greek"),
    "ell": N_("Greek"),
    "gre": N_("Greek"),
    "en": N_("English"),
    "eng": N_("English"),
    "es": N_("Spanish"),
    "spa": N_("Spanish"),
    "et": N_("Estonian"),
    "est": N_("Estonian"),
    "fi": N_("Finnish"),
    "fin": N_("Finnish"),
    "fr": N_("French"),
    "fra": N_("French"),
    "fre": N_("French"),
    "hu": N_("Hungarian"),
    "hun": N_("Hungarian"),
    "it": N_("Italian"),
    "ita": N_("Italian"),
    "nl": N_("Dutch"),
    "nld": N_("Dutch"),
    "dut": N_("Dutch"),
    "pl": N_("Polish"),
    "pol": N_("Polish"),
    "pt": N_("Portuguese"),
    "por": N_("Portuguese"),
    "ru": N_("Russian"),
    "rus": N_("Russian"),
    "sk": N_("Slovak"),
    "slk": N_("Slovak"),
    "slo": N_("Slovak"),
    "sq": N_("Albanian"),
    "sqi": N_("Albanian"),
    "alb": N_("Albanian"),
    "sr": N_("Serbian"),
    "srp": N_("Serbian"),
    "sv": N_("Swedish"),
    "swe": N_("Swedish"),
    "tr": N_("Turkish"),
    "tur": N_("Turkish"),
    "zh": N_("Chinese"),
    "zho": N_("Chinese"),
    "chi": N_("Chinese"),
}

for _track_language_name in set(_TRACK_LANGUAGE_NAMES.values()):
    _TRACK_LANGUAGE_NAMES[_track_language_name.lower()] = _track_language_name


def _normalise_language(value, fallback="en"):
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return fallback
    value = str(value or "").strip().replace("-", "_")
    if not value:
        return fallback
    value = value.split(".", 1)[0].split("@", 1)[0]
    lowered = value.lower()
    if lowered in ("c", "posix"):
        return "en"
    language = lowered.split("_", 1)[0]
    country = lowered.split("_", 1)[1] if "_" in lowered else ""
    alias = _LANGUAGE_CODE_ALIASES.get(language)
    if alias:
        return alias
    if language == "pt" and country == "br":
        return "pt_BR"
    if language == "zh" and (
        country in ("hk", "tw", "mo", "hant")
        or country.startswith("hant_")
    ):
        return "zh_HK"
    if language == "zh":
        return "zh_CN"
    if language == "en" and country == "au":
        return "en_AU"
    if language == "en" and country in ("gb", "uk"):
        return "en_GB"
    return language if language in _BASE_LANGUAGE_CODES else fallback


def _get_language_service():
    """Return Components.Language lazily when import order permits it."""
    global _language
    if _language is not None:
        return _language
    with _CATALOG_LOCK:
        if _language is not None:
            return _language
        try:
            from Components.Language import language as language_service
        except Exception:
            return None
        _language = language_service
        return _language


def _register_language_callback(language_service=None):
    """Register once, including when Components.Language loaded late."""
    global _CALLBACK_LANGUAGE_SERVICE, _CALLBACK_REGISTERED
    if language_service is None:
        language_service = _get_language_service()
    if language_service is None:
        return False
    add_callback = getattr(language_service, "addCallback", None)
    if not callable(add_callback):
        return False
    with _CATALOG_LOCK:
        if (
            _CALLBACK_REGISTERED
            and _CALLBACK_LANGUAGE_SERVICE is language_service
        ):
            return True
        # Mark first because a few image implementations invoke callbacks
        # immediately from addCallback().  The catalogue lock is re-entrant.
        _CALLBACK_REGISTERED = True
        _CALLBACK_LANGUAGE_SERVICE = language_service
        try:
            add_callback(locale_init)
        except Exception:
            if _CALLBACK_LANGUAGE_SERVICE is language_service:
                _CALLBACK_REGISTERED = False
                _CALLBACK_LANGUAGE_SERVICE = None
            return False
    return True


def device_language():
    language_service = _get_language_service()
    if language_service is not None:
        _register_language_callback(language_service)
        for name in ("getLanguage", "getActiveLanguage"):
            getter = getattr(language_service, name, None)
            if not callable(getter):
                continue
            try:
                value = getter()
            except Exception:
                continue
            code = _normalise_language(value, fallback="")
            if code:
                return code
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name, "")
        for candidate in value.split(":"):
            code = _normalise_language(candidate, fallback="")
            if code:
                return code
    return "en"


def _ui_language_code():
    code = device_language()
    return code if code in _CATALOG_LANGUAGE_CODES else "en"


def _catalog(code):
    code = _normalise_language(code)
    if code not in _CATALOG_LANGUAGE_CODES:
        code = "en"
    with _CATALOG_LOCK:
        catalog = _CATALOGS.get(code)
        if catalog is None:
            candidates = [_CATALOG_LANGUAGE_DIRS.get(code, code)]
            if "_" in code:
                candidates.append(code.split("_", 1)[0])
            catalog = gettext.translation(
                PLUGIN_DOMAIN,
                LOCALE_PATH,
                languages=candidates,
                fallback=True,
            )
            _CATALOGS[code] = catalog
        return catalog


def _(value):
    if value is None:
        return ""
    text = str(value)
    return _catalog(_ui_language_code()).gettext(text) if text else text


def metadata_language():
    return _METADATA_LOCALES.get(device_language(), "en-US")


def device_language_label():
    code = device_language()
    native = _NATIVE_LANGUAGE_NAMES.get(code, code)
    if code not in _CATALOG_LANGUAGE_CODES:
        native = "{} → English".format(native)
    return "{} • {}".format(_("Automatic"), native)


def localized_upper(value):
    value = str(value or "")
    if _ui_language_code() == "tr":
        value = value.replace("i", "İ").replace("ı", "I")
    return value.upper()


def localized_language_name(value):
    """Translate common decoder language codes without dynamic msgids."""
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    if not text or text.lower() in ("und", "unknown"):
        return ""
    key = text.lower().replace("-", "_")
    message = _TRACK_LANGUAGE_NAMES.get(key)
    if message is None and "_" in key:
        message = _TRACK_LANGUAGE_NAMES.get(key.split("_", 1)[0])
    if message is not None:
        return _(message)
    return text.upper() if len(text) <= 3 else text


def localized_date_text(value=None):
    value = value or datetime.datetime.now()
    language = _ui_language_code()
    if language == "tr":
        months = (
            "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
            "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
        )
        weekdays = (
            "Pazartesi", "Salı", "Çarşamba", "Perşembe",
            "Cuma", "Cumartesi", "Pazar",
        )
        return "{} {} {}".format(
            value.day,
            months[value.month - 1],
            weekdays[value.weekday()],
        )
    try:
        year = int(value.year)
        month = int(value.month)
        day = int(value.day)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return "---- -- --"
    if language == "en":
        return "{:02d}/{:02d}/{:04d}".format(month, day, year)
    if language in ("zh_CN", "zh_HK"):
        return "{:04d}-{:02d}-{:02d}".format(year, month, day)
    if language == "hu":
        return "{:04d}.{:02d}.{:02d}.".format(year, month, day)
    if language in (
        "cs", "de", "et", "fi", "nl", "pl", "ru", "sk", "sr", "sv",
    ):
        return "{:02d}.{:02d}.{:04d}".format(day, month, year)
    return "{:02d}/{:02d}/{:04d}".format(day, month, year)


def localized_short_weekday(value):
    try:
        parsed = datetime.datetime.strptime(str(value), "%Y-%m-%d")
    except (TypeError, ValueError):
        return "---"
    names = _SHORT_WEEKDAYS.get(_ui_language_code(), _SHORT_WEEKDAYS["en"])
    return names[parsed.weekday()]


def locale_init(*unused_args, **unused_kwargs):
    gettext.bindtextdomain(PLUGIN_DOMAIN, LOCALE_PATH)
    with _CATALOG_LOCK:
        _CATALOGS.clear()
    _register_language_callback()


locale_init()
