# -*- coding: utf-8 -*-
"""Device-language text for public DVB EPG sources."""
from .i18n import _

CONTEXT = 'Independent DVB EPG'


def tr(message):
    key = CONTEXT + '\x04' + message
    translated = _(key)
    return message if translated == key else translated


def source_name(key):
    if key in ('public-all-satellites-v1', 'public-tr-hotbird-v1'):
        return tr('All satellites · All sources')
    if key == 'public-tr-v1':
        return tr('Turkey') + ' · EPG-Ripper + Open-EPG'
    if key == 'public-hotbird-v1':
        return 'Hotbird · Rytec + Azman'
    return tr('All satellites · All sources')
