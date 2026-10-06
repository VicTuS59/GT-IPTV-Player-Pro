"""Small configuration adapter for the vendored resolver.

Keep per-request preferences local to each worker thread instead of relying
on the separate Enigma2 YouTube plugin's global config section.
"""
import threading

_local = threading.local()
# The resolver's format priority is module-global. Serialize both web and
# Discover Movies extraction while each caller sets its thread preferences.
EXTRACT_LOCK = threading.RLock()


class _Value(object):
    def __init__(self, name, default):
        self.name = name
        self.default = default

    @property
    def value(self):
        return getattr(_local, self.name, self.default)


class _Section(object):
    maxResolution = _Value("max_resolution", "264")
    searchLanguage = _Value("search_language", "tr")
    useDashMP4 = _Value("use_dash", True)
    streamMode = _Value("stream_mode", "auto")
    audioPreference = _Value("audio_preference", "default")


class _Plugins(object):
    YouTube = _Section()


class _Config(object):
    plugins = _Plugins()


config = _Config()


def set_preferences(resolution, language, dash, stream_mode=None, audio_preference="default"):
    _local.max_resolution = {
        "360": "18", "480": "35", "720": "22",
        "1080": "37", "1440": "264", "2160": "38",
    }.get(str(resolution), "264")
    _local.search_language = str(language or "tr")[:2]
    _local.use_dash = bool(dash)
    mode = str(stream_mode) if stream_mode is not None else ("auto" if dash else "compatible")
    _local.stream_mode = mode if mode in ("auto", "compatible", "dash") else "auto"
    _local.audio_preference = audio_preference if audio_preference in ("default", "original") else "default"
