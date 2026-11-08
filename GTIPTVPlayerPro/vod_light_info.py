# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native film and series details for the selected VOD item."""
from copy import copy
import re
from xml.sax.saxutils import quoteattr

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.ScrollLabel import ScrollLabel
from Screens.Screen import Screen
from enigma import eTimer

from .background import attach_pixmap
from .browser_common import (
    _WorkerToken, _bounded_client_call, _cinematic_cover_ratio, _connect_timer,
    _scale, _valid_cinematic_backdrop_file,
)
from .diagnostics import log_event
from .i18n import _, device_language, localized_upper
from .metadata import clean_media_title, complete_media_for_device_language
from .paths import plugin_path
from .provider_preview import catalogue_info
from .source_loader import SourceLoader
from .typography import fit_dynamic_text, set_text_if_changed
from .vod_artwork import artwork_urls, backdrop_urls, cached_artwork, cached_backdrop, download_artwork, download_backdrop
from .vod_light import geometry


CANVAS = "#050D16"
INK = "#F6F8FC"
MUTED = "#CDD6E2"
ACCENT = "#36D7F4"
GOLD = "#FFCE58"
HERO_BOX = (680, 0, 1240, 1080)
POSTER_BOX = (1242, 139, 493, 739)
DETAIL_FIELDS = ("title", "plot", "director", "cast", "rating", "year", "genre",
                 "duration", "backdrop", "cover", "cover_urls", "tmdb_id",
                 "imdb_id", "season_ids")
_UNSAFE_NATIVE_TEXT = re.compile(r"[\x00\ud800-\udfff]")


def _display_text(value):
    """Keep valid Unicode and line breaks when crossing into native labels."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return _UNSAFE_NATIVE_TEXT.sub("\ufffd", "" if value is None else str(value))


def _skin(width, height, layout):
    rect, font = layout["rect"], layout["font"]

    def box(values):
        x, y, w, h = rect(*values)
        return 'position="{},{}" size="{},{}"'.format(x, y, w, h)

    def label(name, values, size, color=INK, extra="", transparent=1):
        return ('<widget name="{}" {} zPosition="3" font="Regular;{}" '
                'foregroundColor="{}" transparent="{}" {} />'.format(
                    name, box(values), font(size), color, transparent, extra))

    def fill(values, color):
        return ('<eLabel {} zPosition="2" transparent="0" backgroundColor="{}" />'
                .format(box(values), color))

    scrim = quoteattr(plugin_path("skin", "images", "vod-info-scrim.png"))
    return "\n".join((
        '<screen name="GTLightSynopsis" position="0,0" size="{},{}" '
        'flags="wfNoBorder" backgroundColor="{}">'.format(width, height, CANVAS),
        '<widget name="hero" {} zPosition="0" />'.format(box(HERO_BOX)),
        '<widget name="poster" {} zPosition="1" />'.format(box(POSTER_BOX)),
        '<ePixmap {} pixmap={} zPosition="1" alphatest="blend" scale="1" />'
        .format(box((0, 0, 1920, 1080)), scrim),
        label("gt", (80, 40, 65, 56), 42, ACCENT),
        label("brand", (151, 44, 560, 50), 35),
        label("section", (80, 150, 780, 46), 35, ACCENT),
        label("title", (80, 202, 865, 123), 82, extra='valign="center"'),
        label("meta", (80, 333, 544, 52), 34, MUTED, 'valign="center"'),
        label("rating_source", (650, 336, 98, 46), 26, CANVAS,
              'valign="center" halign="center" backgroundColor="#FFCE58"', transparent=0),
        label("rating", (748, 336, 146, 46), 31, GOLD,
              'valign="center" halign="center" backgroundColor="#30250F"', transparent=0),
        label("summary_heading", (80, 421, 820, 49), 36),
        label("text", (80, 478, 835, 224), 34, MUTED, 'scrollbarMode="showOnDemand"'),
        label("director_label", (80, 742, 226, 49), 29, ACCENT),
        label("director", (325, 739, 565, 64), 32),
        label("cast_label", (80, 823, 226, 49), 29, ACCENT),
        label("cast", (325, 820, 565, 70), 32),
        fill((80, 969, 1760, 2), "#566475"),
        label("footer", (80, 990, 1700, 53), 29, MUTED),
        "</screen>",
    ))


def _score(value):
    match = re.fullmatch(r"(\d{1,3}(?:[.,]\d+)?)\s*(?:/\s*(5|10|100))?",
                         str(value or "").strip())
    if not match:
        return None
    number = float(match[1].replace(",", ".")) * 10 / int(match[2] or 10)
    return number if 0 < number <= 10 else None


def _rating(item, info):
    entry = getattr(item, "_catalogue_entry", None)
    if not isinstance(entry, dict):
        entry = {}
    for label, value in (
        ("IMDb", entry.get("rating_imdb") or entry.get("imdb_rating")),
        ("TMDb", getattr(info, "tmdb_rating", "")),
        (_("Rating"), getattr(info, "rating", "") or getattr(item, "rating", "")),
    ):
        number = _score(value)
        if number is not None:
            score = "{:.1f}".format(number)
            if device_language() == "tr":
                score = score.replace(".", ",")
            return label, "{}/10".format(score)
    return "", ""


def _fill_detail(primary, fallback):
    if primary is None:
        return copy(fallback)
    if fallback is None:
        return primary
    primary = copy(primary)
    for field in DETAIL_FIELDS:
        value = getattr(fallback, field, None)
        if value and not getattr(primary, field, None):
            setattr(primary, field, value)
    return primary


class LightSynopsisScreen(Screen):
    def __init__(self, session, provider, metadata, item, info=None, hero_url=""):
        width, height, unused = _scale()
        self._layout = geometry(width, height)
        self.skin = _skin(width, height, self._layout)
        Screen.__init__(self, session)
        self.provider, self.metadata, self.item = provider, metadata, copy(item)
        self.kind = "series" if item.content_type == "series" else "movie"
        self._closed = False
        self._started = False
        self._token = None
        self._art_token = None
        self._art_plan = self._art_request = None
        self._hero_ready = self._hero_decoding = False
        self._hero_url = hero_url
        self._plot = ""
        self._info = info or catalogue_info(provider, self.kind, self.item)
        self["hero"], self["poster"] = Pixmap(), Pixmap()
        self["hero"].hide()
        self["poster"].hide()
        self["gt"] = Label("GT")
        self["brand"] = Label("IPTV PLAYER PRO")
        self["section"] = Label(localized_upper(_("Information")))
        self["summary_heading"] = Label(localized_upper(
            _("SERIES SUMMARY") if self.kind == "series" else _("MOVIE SUMMARY")))
        self["director_label"] = Label(localized_upper(
            _("Director: {}").format("").rstrip(" :：")))
        self["cast_label"] = Label(localized_upper(
            _("Cast: {}").format("").rstrip(" :：")))
        for name in ("title", "meta", "rating_source", "rating", "director", "cast"):
            self[name] = Label("")
        self["text"] = ScrollLabel("")
        self["footer"] = Label("▲ ▼  " + _("Page") + "        OK / EXIT  " + _("Close"))
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], {
            "ok": self.close, "cancel": self.close, "up": self["text"].pageUp,
            "down": self["text"].pageDown, "left": self["text"].pageUp,
            "right": self["text"].pageDown, "upRepeated": self["text"].pageUp,
            "downRepeated": self["text"].pageDown,
        }, -1)
        self._details = SourceLoader(eTimer(), _connect_timer, self._provider_ready)
        self._art = SourceLoader(eTimer(), _connect_timer, self._art_ready)
        self.onLayoutFinish.append(self._start)
        self.onClose.append(self._stop)
        self.setTitle(_("Information"))

    def _fit(self, field, text, preferred, minimum, lines=1, fallback=70):
        font = self._layout["font"]
        fit_dynamic_text(self[field], _display_text(text), max_lines=lines, preferred_size=font(preferred),
                         min_size=font(minimum), fallback_chars=fallback)

    def _render(self, info, loading=False):
        item = self.item
        title = getattr(info, "title", "")
        if not title or title == _("Movie"):
            title = item.name
        self._fit("title", clean_media_title(title) or title, 82, 46, lines=2, fallback=50)
        genres = str(getattr(info, "genre", "") or "").split(",")
        meta = [getattr(info, "year", "") or getattr(item, "year", ""), genres[0].strip()]
        if self.kind == "series":
            seasons = getattr(info, "seasons", ())
            if seasons:
                label = _("Season") if len(seasons) == 1 or device_language() == "tr" else _("Seasons")
                meta.append("{} {}".format(len(seasons), label))
        else:
            meta.append(getattr(info, "duration", ""))
        self._fit("meta", "  ·  ".join(str(value) for value in meta if value), 34, 25, fallback=36)
        rating_source, rating = _rating(item, info)
        if rating:
            self._fit("rating_source", rating_source, 26, 18, fallback=10)
            self._fit("rating", rating, 31, 24, fallback=16)
            self["rating_source"].show()
            self["rating"].show()
        else:
            self["rating_source"].hide()
            self["rating"].hide()
        unavailable = _("Series summary unavailable.") if self.kind == "series" else _("Movie summary unavailable.")
        plot = getattr(info, "plot", "") or getattr(item, "plot", "") or (
            _("Loading...") if loading else unavailable)
        plot = _display_text(plot)
        if plot != self._plot and set_text_if_changed(self["text"], plot) is not None:
            self._plot = plot
        self._fit("director", getattr(info, "director", "") or "—", 32, 25, lines=2)
        cast = str(getattr(info, "cast", "") or "")
        cast = " · ".join(name.strip() for name in re.split(r"[,|;]", cast, maxsplit=3)[:3]
                          if name.strip())
        self._fit("cast", cast or "—", 32, 25, lines=2)

    def _start(self):
        if self._started:
            return
        self._started = True
        # ScrollLabel renders its text and slider in a child container.
        # Its skin z-position alone does not raise the container over the scrim.
        try:
            self["text"].instance.setZPosition(3)
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._schedule_art(complete=False)
        self._render(self._info, loading=True)
        self._token = _WorkerToken(23)
        self._details.request(self._load_provider_details)

    def _load_provider_details(self):
        info, token = self._info, self._token
        name = "load_vod_info" if self.kind == "movie" else "load_series_summary"
        loader = getattr(self.provider, name, None)
        if callable(loader) and any(not getattr(info, field, "")
                                    for field in ("plot", "director", "cast", "rating", "backdrop")):
            provider_token = _WorkerToken(6)
            provider_token.cancel_event = token.cancel_event
            provider_token.deadline = min(provider_token.deadline, token.deadline)
            try:
                provided = _bounded_client_call(self.provider, provider_token,
                                                lambda: loader(self.item.stream_id))
                info = _fill_detail(provided, info)
            except Exception as error:
                token.check()
                log_event("vod-info", "provider detail unavailable: {}".format(type(error).__name__))
        return info

    def _provider_ready(self, info, error):
        if self._closed:
            return
        metadata_available = getattr(self.metadata, "available", False)
        self._details_ready(info, error, complete=not metadata_available)
        if metadata_available:
            self._details.apply = self._metadata_ready
            self._details.request(self._load_metadata_details)

    def _load_metadata_details(self):
        completer = self.metadata.complete_movie if self.kind == "movie" else self.metadata.complete_series
        return _bounded_client_call(self.metadata, self._token, lambda:
            complete_media_for_device_language(self.metadata,
                lambda item, detail: completer(item, detail, require_backdrop=True, verify_identity=True),
                self.item, self._info))

    def _metadata_ready(self, info, error):
        if self._closed:
            return
        if error is not None:
            log_event("vod-info", "metadata detail unavailable", error)
        self._details_ready(info, error)
        verified_id = getattr(self._info, "_summary_tmdb_id", "")
        if self.kind == "series" and verified_id and not getattr(self._info, "director", ""):
            self._details.apply = self._directors_ready
            self._details.request(lambda: _bounded_client_call(self.metadata, self._token,
                lambda: self.metadata.series_directors(verified_id)))

    def _directors_ready(self, directors, error):
        if not self._closed and error is None and directors:
            self._info.director = directors
            self._render(self._info)

    def _details_ready(self, info, error, *, complete=True):
        if self._closed:
            return
        if error is None and info is not None:
            self._info = info
        self._schedule_art(complete=complete)
        if complete:
            self._render(self._info)

    def _schedule_art(self, *, complete):
        if self._hero_ready:
            return
        info = self._info
        backdrops = backdrop_urls(self.item, info, self._hero_url)
        covers = artwork_urls([getattr(info, "cover", ""), getattr(self.item, "icon", "")]
                              + list(getattr(info, "cover_urls", ()) or ()))[:4]
        verified_id = getattr(info, "_summary_tmdb_id", "") if complete else ""
        plan = backdrops, covers, verified_id, complete
        if not (backdrops or covers or verified_id) or plan == self._art_plan:
            return
        self._art_plan = plan
        if self._hero_decoding:
            return
        # The GUI lookup only returns remembered, worker-validated paths.
        # A newly discovered warm hero can overtake an older network job.
        hit = cached_backdrop(backdrops + covers)
        if self._art_token is not None and (hit is not None or (
                self._art_request is not None and set(backdrops).difference(self._art_request[0]))):
            self._art_token.cancel()
            self._art.cancel()
            self._art_token = None
        if hit is not None:
            self._art_request = None  # Revalidate on the worker if native decoding fails.
            path = hit[1]
            self._art_ready(("hero", path, _cinematic_cover_ratio(path)), None)
        else:
            self._launch_art()

    def _launch_art(self):
        if (self._closed or self._hero_ready or self._hero_decoding or self._art_token is not None
                or self._art_plan is None or self._art_request == self._art_plan):
            return
        plan = self._art_request = self._art_plan
        token = self._art_token = _WorkerToken(14)
        info = self._info
        self._art.request(lambda: self._load_art(plan, info, token))

    def _load_art(self, plan, info, token):
        backdrops, covers, verified_id, complete = plan
        found = cached_backdrop(backdrops + covers, token)
        if found is None:
            found = download_backdrop(self.provider, backdrops, token)
        if found is not None:
            path = found[1]
            return "hero", path, _cinematic_cover_ratio(path)
        if not complete:
            return None
        if verified_id:
            try:
                url = _bounded_client_call(self.metadata, token, lambda:
                    self.metadata.cinematic_backdrop(self.kind, self.item, info))
                extra = tuple(url for url in artwork_urls((url,)) if url not in backdrops)
                found = cached_backdrop(extra, token) or download_backdrop(self.provider, extra, token)
                if found is not None:
                    path = found[1]
                    return "hero", path, _cinematic_cover_ratio(path)
            except Exception:
                token.check()
        found = cached_artwork(covers, _cinematic_cover_ratio, token)
        if found is not None:
            path = found[1]
            name = "hero" if _valid_cinematic_backdrop_file(path) else "poster"
            return name, path, _cinematic_cover_ratio(path)
        for url in covers:
            token.check()
            path = download_artwork(self.provider, url, token)
            ratio = _cinematic_cover_ratio(path) if path else None
            if ratio:
                return ("hero" if _valid_cinematic_backdrop_file(path) else "poster"), path, ratio
        return None

    def _art_ready(self, result, error):
        self._art_token = None
        if self._closed:
            return
        if error is not None or result is None:
            self._launch_art()
            return
        name, path, ratio = result
        box = HERO_BOX if name == "hero" else POSTER_BOX
        width, height = self._layout["rect"](*box)[2:]
        self._hero_decoding = name == "hero"

        def loaded(success):
            if self._closed:
                return
            if name == "hero":
                self._hero_decoding = False
                self._hero_ready = success
                if success:
                    self["poster"].hide()
            if success and (name == "hero" or not self._hero_ready):
                self[name].show()
            self._launch_art()

        try:
            attach_pixmap(self, name, path, cover_ratio=ratio, on_loaded=loaded, show_on_load=False,
                          decode_size=(min(width, box[2]), min(height, box[3])))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            loaded(False)

    def _stop(self):
        self._closed = True
        for token in (self._token, self._art_token):
            if token is not None:
                token.cancel()
        self._details.close()
        self._art.close()
