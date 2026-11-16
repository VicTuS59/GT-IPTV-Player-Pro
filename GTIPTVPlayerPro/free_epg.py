# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Country catalogue for the optional FreeEPG M3U XMLTV preset."""

import json
import threading
import time

from .i18n import _
from .network_security import NetworkSecurityError, fetch_public_url


_ORIGIN = "https://www.free-epg.de"
_STATUS_URL = _ORIGIN + "/api/epg/status"
_COUNTRIES_URL = _ORIGIN + "/api/countries"
_STATUS_TIMEOUT = 5.0
_CATALOGUE_TTL = 30 * 60
_RETRY_DELAY = 60

# Published feeds listed at https://free-epg.de/countries on 2026-11-15.
# This seed also makes the picker usable on receivers without a timer API.
_BUNDLED_COUNTRIES = tuple("""
ae al am ar at au ba be bg bo br bs by ca ch ci cl cn co cr cw cy cz de
dk do ec ee eg es fi fr gb ge gh gr gt hk hn hr hu id ie il in is it jm
jp ke kr lb lk lt lu lv ma me mg mk mn mo mt mu mw mx my mz na ng ni nl
no nz pa pe ph pk pl pr pt py qa ro rs ru sa se sg si sk sv th tr tt tw
ua ug us uy uz ve vn za zm zw
""".split())

# Region labels are generated from Unicode CLDR. See LICENSING.md.
_ENGLISH_COUNTRY_NAMES = {
    "AC": "Ascension Island",
    "AD": "Andorra",
    "AE": "United Arab Emirates",
    "AF": "Afghanistan",
    "AG": "Antigua & Barbuda",
    "AI": "Anguilla",
    "AL": "Albania",
    "AM": "Armenia",
    "AN": "Curaçao",
    "AO": "Angola",
    "AQ": "Antarctica",
    "AR": "Argentina",
    "AS": "American Samoa",
    "AT": "Austria",
    "AU": "Australia",
    "AW": "Aruba",
    "AX": "Åland Islands",
    "AZ": "Azerbaijan",
    "BA": "Bosnia & Herzegovina",
    "BB": "Barbados",
    "BD": "Bangladesh",
    "BE": "Belgium",
    "BF": "Burkina Faso",
    "BG": "Bulgaria",
    "BH": "Bahrain",
    "BI": "Burundi",
    "BJ": "Benin",
    "BL": "St. Barthélemy",
    "BM": "Bermuda",
    "BN": "Brunei",
    "BO": "Bolivia",
    "BQ": "Caribbean Netherlands",
    "BR": "Brazil",
    "BS": "Bahamas",
    "BT": "Bhutan",
    "BU": "Myanmar (Burma)",
    "BV": "Bouvet Island",
    "BW": "Botswana",
    "BY": "Belarus",
    "BZ": "Belize",
    "CA": "Canada",
    "CC": "Cocos (Keeling) Islands",
    "CD": "Congo - Kinshasa",
    "CF": "Central African Republic",
    "CG": "Congo - Brazzaville",
    "CH": "Switzerland",
    "CI": "Côte d’Ivoire",
    "CK": "Cook Islands",
    "CL": "Chile",
    "CM": "Cameroon",
    "CN": "China",
    "CO": "Colombia",
    "CP": "Clipperton Island",
    "CQ": "Sark",
    "CR": "Costa Rica",
    "CS": "Serbia",
    "CU": "Cuba",
    "CV": "Cape Verde",
    "CW": "Curaçao",
    "CX": "Christmas Island",
    "CY": "Cyprus",
    "CZ": "Czechia",
    "DD": "Germany",
    "DE": "Germany",
    "DG": "Diego Garcia",
    "DJ": "Djibouti",
    "DK": "Denmark",
    "DM": "Dominica",
    "DO": "Dominican Republic",
    "DY": "Benin",
    "DZ": "Algeria",
    "EA": "Ceuta & Melilla",
    "EC": "Ecuador",
    "EE": "Estonia",
    "EG": "Egypt",
    "EH": "Western Sahara",
    "ER": "Eritrea",
    "ES": "Spain",
    "ET": "Ethiopia",
    "EU": "European Union",
    "EZ": "Eurozone",
    "FI": "Finland",
    "FJ": "Fiji",
    "FK": "Falkland Islands",
    "FM": "Micronesia",
    "FO": "Faroe Islands",
    "FR": "France",
    "FX": "France",
    "GA": "Gabon",
    "GB": "United Kingdom",
    "GD": "Grenada",
    "GE": "Georgia",
    "GF": "French Guiana",
    "GG": "Guernsey",
    "GH": "Ghana",
    "GI": "Gibraltar",
    "GL": "Greenland",
    "GM": "Gambia",
    "GN": "Guinea",
    "GP": "Guadeloupe",
    "GQ": "Equatorial Guinea",
    "GR": "Greece",
    "GS": "South Georgia & South Sandwich Islands",
    "GT": "Guatemala",
    "GU": "Guam",
    "GW": "Guinea-Bissau",
    "GY": "Guyana",
    "HK": "Hong Kong SAR China",
    "HM": "Heard & McDonald Islands",
    "HN": "Honduras",
    "HR": "Croatia",
    "HT": "Haiti",
    "HU": "Hungary",
    "HV": "Burkina Faso",
    "IC": "Canary Islands",
    "ID": "Indonesia",
    "IE": "Ireland",
    "IL": "Israel",
    "IM": "Isle of Man",
    "IN": "India",
    "IO": "British Indian Ocean Territory",
    "IQ": "Iraq",
    "IR": "Iran",
    "IS": "Iceland",
    "IT": "Italy",
    "JE": "Jersey",
    "JM": "Jamaica",
    "JO": "Jordan",
    "JP": "Japan",
    "KE": "Kenya",
    "KG": "Kyrgyzstan",
    "KH": "Cambodia",
    "KI": "Kiribati",
    "KM": "Comoros",
    "KN": "St. Kitts & Nevis",
    "KP": "North Korea",
    "KR": "South Korea",
    "KW": "Kuwait",
    "KY": "Cayman Islands",
    "KZ": "Kazakhstan",
    "LA": "Laos",
    "LB": "Lebanon",
    "LC": "St. Lucia",
    "LI": "Liechtenstein",
    "LK": "Sri Lanka",
    "LR": "Liberia",
    "LS": "Lesotho",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "LY": "Libya",
    "MA": "Morocco",
    "MC": "Monaco",
    "MD": "Moldova",
    "ME": "Montenegro",
    "MF": "St. Martin",
    "MG": "Madagascar",
    "MH": "Marshall Islands",
    "MK": "North Macedonia",
    "ML": "Mali",
    "MM": "Myanmar (Burma)",
    "MN": "Mongolia",
    "MO": "Macao SAR China",
    "MP": "Northern Mariana Islands",
    "MQ": "Martinique",
    "MR": "Mauritania",
    "MS": "Montserrat",
    "MT": "Malta",
    "MU": "Mauritius",
    "MV": "Maldives",
    "MW": "Malawi",
    "MX": "Mexico",
    "MY": "Malaysia",
    "MZ": "Mozambique",
    "NA": "Namibia",
    "NC": "New Caledonia",
    "NE": "Niger",
    "NF": "Norfolk Island",
    "NG": "Nigeria",
    "NH": "Vanuatu",
    "NI": "Nicaragua",
    "NL": "Netherlands",
    "NO": "Norway",
    "NP": "Nepal",
    "NR": "Nauru",
    "NU": "Niue",
    "NZ": "New Zealand",
    "OM": "Oman",
    "PA": "Panama",
    "PE": "Peru",
    "PF": "French Polynesia",
    "PG": "Papua New Guinea",
    "PH": "Philippines",
    "PK": "Pakistan",
    "PL": "Poland",
    "PM": "St. Pierre & Miquelon",
    "PN": "Pitcairn Islands",
    "PR": "Puerto Rico",
    "PS": "Palestinian Territories",
    "PT": "Portugal",
    "PW": "Palau",
    "PY": "Paraguay",
    "QA": "Qatar",
    "QO": "Outlying Oceania",
    "RE": "Réunion",
    "RH": "Zimbabwe",
    "RO": "Romania",
    "RS": "Serbia",
    "RU": "Russia",
    "RW": "Rwanda",
    "SA": "Saudi Arabia",
    "SB": "Solomon Islands",
    "SC": "Seychelles",
    "SD": "Sudan",
    "SE": "Sweden",
    "SG": "Singapore",
    "SH": "St. Helena",
    "SI": "Slovenia",
    "SJ": "Svalbard & Jan Mayen",
    "SK": "Slovakia",
    "SL": "Sierra Leone",
    "SM": "San Marino",
    "SN": "Senegal",
    "SO": "Somalia",
    "SR": "Suriname",
    "SS": "South Sudan",
    "ST": "São Tomé & Príncipe",
    "SU": "Russia",
    "SV": "El Salvador",
    "SX": "Sint Maarten",
    "SY": "Syria",
    "SZ": "Eswatini",
    "TA": "Tristan da Cunha",
    "TC": "Turks & Caicos Islands",
    "TD": "Chad",
    "TF": "French Southern Territories",
    "TG": "Togo",
    "TH": "Thailand",
    "TJ": "Tajikistan",
    "TK": "Tokelau",
    "TL": "Timor-Leste",
    "TM": "Turkmenistan",
    "TN": "Tunisia",
    "TO": "Tonga",
    "TP": "Timor-Leste",
    "TR": "Türkiye",
    "TT": "Trinidad & Tobago",
    "TV": "Tuvalu",
    "TW": "Taiwan",
    "TZ": "Tanzania",
    "UA": "Ukraine",
    "UG": "Uganda",
    "UK": "United Kingdom",
    "UM": "U.S. Outlying Islands",
    "UN": "United Nations",
    "US": "United States",
    "UY": "Uruguay",
    "UZ": "Uzbekistan",
    "VA": "Vatican City",
    "VC": "St. Vincent & Grenadines",
    "VD": "Vietnam",
    "VE": "Venezuela",
    "VG": "British Virgin Islands",
    "VI": "U.S. Virgin Islands",
    "VN": "Vietnam",
    "VU": "Vanuatu",
    "WF": "Wallis & Futuna",
    "WS": "Samoa",
    "XA": "Pseudo-Accents",
    "XB": "Pseudo-Bidi",
    "XK": "Kosovo",
    "YD": "Yemen",
    "YE": "Yemen",
    "YT": "Mayotte",
    "YU": "Serbia",
    "ZA": "South Africa",
    "ZM": "Zambia",
    "ZR": "Congo - Kinshasa",
    "ZW": "Zimbabwe",
    "ZZ": "Unknown Region"
}
_COUNTRY_MSGID_PREFIX = "FreeEPG country "

_LOCK = threading.Lock()
_countries = _BUNDLED_COUNTRIES
_expires_at = 0.0
_loading = False
_using_saved_list = True


def country_code(value):
    if not isinstance(value, str):
        return ""
    code = value.strip().lower()
    return code if len(code) == 2 and code.isascii() and code.isalpha() else ""


def xmltv_url(country):
    code = country_code(country)
    if not code:
        raise ValueError("invalid FreeEPG country")
    return "{}/api/epg/{}.xml.gz".format(_ORIGIN, code)


def country_name(country):
    code = country.upper()
    message = _COUNTRY_MSGID_PREFIX + code
    translated = _(message)
    if translated != message:
        return translated
    return _ENGLISH_COUNTRY_NAMES.get(code, code)


def _published_countries(body, from_status=True):
    data = json.loads(body)
    rows = data.get("countries") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) > 676:
        raise ValueError("invalid FreeEPG country list")
    codes = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid FreeEPG country status")
        if from_status:
            code = country_code(row.get("country"))
            status = row.get("status")
            if status not in ("fresh", "stale", "missing"):
                raise ValueError("invalid FreeEPG country status")
            # A stale feed still exists and can be read through its XMLTV URL.
            published = status != "missing"
        else:
            code = country_code(row.get("code"))
            published = row.get("hasEpg")
        if not code or not isinstance(published, bool):
            raise ValueError("invalid FreeEPG country status")
        if published:
            codes.add(code)
    return tuple(sorted(codes))


def _refresh_countries():
    global _countries, _expires_at, _loading, _using_saved_list
    updated = None
    deadline = time.monotonic() + _STATUS_TIMEOUT
    try:
        for url in (_STATUS_URL, _COUNTRIES_URL):
            timeout = min(3.5, deadline - time.monotonic())
            if timeout <= 0:
                break
            try:
                body, _, _ = fetch_public_url(
                    url,
                    maximum=128 * 1024,
                    timeout=timeout,
                    accept="application/json",
                    user_agent="GT IPTV Player Pro",
                )
                updated = _published_countries(body, from_status=url == _STATUS_URL)
            except (OSError, ValueError, NetworkSecurityError):
                continue
            break
    finally:
        with _LOCK:
            _using_saved_list = updated is None
            if updated is not None:
                _countries = updated
            _expires_at = time.monotonic() + (_RETRY_DELAY if updated is None else _CATALOGUE_TTL)
            _loading = False


def request_countries():
    """Queue one bounded refresh and return whether a request is in progress."""
    global _expires_at, _loading, _using_saved_list
    with _LOCK:
        if not _loading and time.monotonic() >= _expires_at:
            _loading = True
            try:
                threading.Thread(target=_refresh_countries, name="GTFreeEPG", daemon=True).start()
            except (OSError, RuntimeError):
                _loading = False
                _using_saved_list = True
                _expires_at = time.monotonic() + _RETRY_DELAY
        return _loading


def catalogue():
    with _LOCK:
        return _countries, _loading, _using_saved_list
