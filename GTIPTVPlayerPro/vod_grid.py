# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded, receiver-independent layout and work queue for the VOD wall."""

from collections import deque
import threading
from xml.sax.saxutils import escape
from xml.etree import ElementTree as ET

GRID_COLUMNS = 7
GRID_ROWS = 2
GRID_PAGE_SIZE = GRID_COLUMNS * GRID_ROWS
CINEMATIC_POSTER_COLUMNS = 7


def cinematic_poster_geometry(width, height, px):
    """Portrait cards spanning the rail, including its final rounding pixels."""
    left, top, gap = px(365), px(616), px(18)
    available = width - left - gap * (CINEMATIC_POSTER_COLUMNS - 1)
    card_h = px(344)
    tiles = []
    for column in range(CINEMATIC_POSTER_COLUMNS):
        x = left + available * column // CINEMATIC_POSTER_COLUMNS + gap * column
        right = left + available * (column + 1) // CINEMATIC_POSTER_COLUMNS + gap * column
        card_w = right - x
        max_image_w = max(1, card_w - px(20))
        image_h = max(1, min(card_h - px(60), (max_image_w * 3 + 1) // 2))
        image_w = min(max_image_w, (image_h * 2 + 1) // 3)
        image_y = top + px(10)
        title_y = image_y + image_h + px(4)
        tiles.append({
            "card": (x, top, card_w, card_h),
            "image": (x + (card_w - image_w) // 2, image_y, image_w, image_h),
            "title": (x + px(6), title_y, card_w - px(12),
                      top + card_h - px(6) - title_y),
        })
    return {"tiles": tiles}


class PosterRetryQueue:
    """GUI-owned, page-scoped retries with a fixed budget per visible item."""

    def __init__(self):
        self.reset()

    def reset(self, context=None):
        self.context = context
        self.pending = {}
        self.attempts = {}

    def schedule(self, identity, job, now, delay):
        identity = str(identity)
        if identity in self.pending:
            return True
        attempt = self.attempts.get(identity, 0)
        if attempt >= 2:
            return False
        self.attempts[identity] = attempt + 1
        self.pending[identity] = (now + max(float(delay), (2.0, 20.0)[attempt]), job)
        return True

    def succeeded(self, identity):
        self.pending.pop(str(identity), None)

    def ready(self, now):
        jobs = []
        for identity, (deadline, job) in list(self.pending.items()):
            if deadline <= now:
                self.pending.pop(identity, None)
                jobs.append(job)
        return jobs

    def next_delay(self, now):
        return min((max(0.0, deadline - now) for deadline, job in self.pending.values()),
                   default=None)


def grid_geometry(width, height, px):
    footer_y = height - px(90)
    top, bottom = px(332), footer_y - px(22)
    left, right, gap = px(378), width - px(58), px(12)
    tile_w = (right - left - gap * (GRID_COLUMNS - 1)) // GRID_COLUMNS
    tile_h = (bottom - top - gap * (GRID_ROWS - 1)) // GRID_ROWS
    # Give the native fit decoder the full card width. Its source aspect
    # ratio is retained without the extra portrait-shaped inset used in r18.
    image_h = tile_h - px(50)
    image_w = tile_w - px(12)
    tiles = []
    for slot in range(GRID_PAGE_SIZE):
        row, column = divmod(slot, GRID_COLUMNS)
        x, y = left + column * (tile_w + gap), top + row * (tile_h + gap)
        tiles.append({
            "card": (x, y, tile_w, tile_h),
            "image": (x + px(6), y + px(6), image_w, image_h),
            "title": (x + px(4), y + tile_h - px(44), tile_w - px(8), px(42)),
        })
    return {
        "tiles": tiles,
        "footer_y": footer_y,
        "category_scroll": (px(342), px(58), px(8), 12 * px(72) - px(8)),
        "poster_scroll": (width - px(32), top, px(10), bottom - top),
        "caption": (left, px(6), right - left - px(130), px(46)),
        "position": (right - px(120), px(6), px(120), px(46)),
        "info_panel": (left, px(58), right - left, px(260)),
        "info_title": (left + px(22), px(72), right - left - px(44), px(52)),
        "info_rating": (left + px(22), px(130), px(245), px(40)),
        "info_meta": (left + px(280), px(130), right - left - px(302), px(40)),
        "info_plot": (left + px(22), px(178), right - left - px(44), px(64)),
        "info_people": (left + px(22), px(250), right - left - px(44), px(58)),
        "clean_background": (px(365), 0, width - px(365), height - px(90)),
    }


def grid_move(index, count, has_more, direction):
    """Return an item index, or None when Left should focus categories."""
    if count <= 0:
        return index
    column = index % GRID_COLUMNS
    if direction == "left":
        return None if column == 0 else index - 1
    if direction == "right":
        target = index if column == GRID_COLUMNS - 1 else index + 1
    elif direction == "up":
        target = max(0, index - GRID_COLUMNS) if index >= GRID_COLUMNS else index
    elif direction == "down":
        target = index + GRID_COLUMNS
        if not has_more and target >= count:
            # A short final row remains reachable from every column.
            next_row = (index // GRID_COLUMNS + 1) * GRID_COLUMNS
            target = count - 1 if next_row < count else index
    else:
        return index
    return target if has_more or target < count else index


def page_position(index, count, has_more=False):
    if count <= 0:
        return ""
    page = index // GRID_PAGE_SIZE + 1
    pages = (count + GRID_PAGE_SIZE - 1) // GRID_PAGE_SIZE
    return "{}/{}{}".format(page, pages, "+" if has_more else "")


class LatestPosterWorkers:
    """Two reusable daemon workers; queued obsolete pages are discarded."""

    def __init__(self, worker_count=2):
        self._condition = threading.Condition()
        self._pending = deque()
        self._closed = False
        self._threads = []
        for number in range(max(1, min(2, int(worker_count)))):
            worker = threading.Thread(target=self._run, name="gt-vod-poster-{}".format(number))
            worker.daemon = True
            self._threads.append(worker)
            worker.start()

    def replace(self, tasks):
        with self._condition:
            self._pending.clear()
            if not self._closed:
                self._pending.extend(tasks)
                self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            self._pending.clear()
            self._condition.notify_all()

    def _run(self):
        while True:
            with self._condition:
                while not self._pending and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                task = self._pending.popleft()
            try:
                task()
            except Exception:
                # Cancellation and individual artwork failures must not kill
                # either reusable worker or surface as receiver tracebacks.
                pass


def poster_grid_skin(kind, width, height, px, favorite_star, cinematic_skin):
    geometry = grid_geometry(width, height, px)
    canonical = ET.fromstring(cinematic_skin)
    # The shared theme, twelve category rows and remote footer are copied
    # verbatim from the active cinematic skin. Only its right-hand content
    # area is replaced, so future category/theme changes remain shared.
    keep = {"app_bg", "top_accent", "header", "screen_label", "content_panel",
            "list_accent", "breadcrumb", "items", "message", "footer",
            "list_scroll_track", "list_scroll_thumb", kind + "_server",
            kind + "_video_guard"}
    attributes = " ".join('{}="{}"'.format(key, escape(value, {'"': '&quot;'}))
                          for key, value in canonical.attrib.items())
    parts = ["<screen {}>".format(attributes)]
    for component in canonical:
        name = component.get("name", "")
        if name in keep or name.startswith(kind + "_category_"):
            parts.append(ET.tostring(component, encoding="unicode"))
    image_dir = favorite_star.rsplit("/", 1)[0]
    card_path = image_dir + "/vod-grid-card-r19.png"
    focus_path = image_dir + "/vod-grid-focus-r19.png"
    info_path = image_dir + "/vod-grid-info-r20.png"

    def widget(name, rectangle, font=1, color="#F8FAFC", background=None, z=2,
               extra="", pixmap=None):
        x, y, w, h = rectangle
        attributes = ('name="{}" position="{},{}" size="{},{}" zPosition="{}" '
                      'font="Regular;{}" foregroundColor="{}" transparent="{}"').format(
                          name, x, y, w, h, z, font, color, 0 if background else 1)
        if background:
            attributes += ' backgroundColor="{}"'.format(background)
        if pixmap:
            attributes += ' pixmap="{}"'.format(escape(pixmap, {'"': '&quot;'}))
        parts.append('<widget {} {}/>'.format(attributes, extra))

    widget("grid_clean_guard", geometry["clean_background"], background="#020617", z=-19)
    widget("grid_clean_background", geometry["clean_background"], z=-18,
           extra='alphatest="blend"')
    widget(kind + "_grid_info_panel", geometry["info_panel"], pixmap=info_path,
           z=1, extra='alphatest="blend" scale="1"')
    for suffix, rect, font, colour in (
        ("title", "info_title", 34, "#FFFFFF"),
        ("rating", "info_rating", 22, "#FACC15"),
        ("meta", "info_meta", 22, "#CBD5E1"),
        ("plot", "info_plot", 23, "#E2E8F0"),
        ("people", "info_people", 20, "#CBD5E1"),
    ):
        widget(kind + "_" + suffix, geometry[rect], px(font), colour,
               z=2, extra='valign="center" halign="left" noWrap="0"')
    for suffix, color in (("track", "#1C3046"), ("thumb", "#22D3EE")):
        widget("poster_scroll_" + suffix, geometry["poster_scroll"], background=color, z=5)
    sx, sy, sw, sh = geometry["poster_scroll"]
    widget("poster_scroll_more", (sx - px(10), sy + sh, sw + px(20), px(22)),
           px(24), "#22D3EE", extra='halign="center" valign="center" noWrap="1"')
    widget(kind + "_rail_caption", geometry["caption"], px(26), "#22D3EE", extra='valign="center" noWrap="1"')
    widget(kind + "_grid_position", geometry["position"], px(24), "#22D3EE", extra='valign="center" halign="right" noWrap="1"')
    for slot, tile in enumerate(geometry["tiles"]):
        x, y, w, h = tile["card"]
        prefix = kind + "_tile_"
        widget(prefix + "bg_{}".format(slot), tile["card"], pixmap=card_path, z=2, extra='alphatest="blend" scale="1"')
        widget(prefix + "focus_{}".format(slot), tile["card"], pixmap=focus_path, z=8, extra='alphatest="blend" scale="1"')
        widget(prefix + "inner_{}".format(slot), (x + px(3), y + px(3), w - px(6), h - px(6)), z=3)
        widget(prefix + "placeholder_{}".format(slot), tile["image"], px(30), "#22D3EE", "#101E30", z=5, extra='valign="center" halign="center"')
        widget(prefix + "poster_{}".format(slot), tile["image"], z=6, extra='alphatest="blend" scale="1"')
        widget(prefix + "favorite_{}".format(slot), (x + w - px(35), y + px(9), px(26), px(26)), z=9, pixmap=favorite_star, extra='alphatest="blend" scale="1"')
        widget(prefix + "title_{}".format(slot), tile["title"], px(18), z=7, extra='valign="center" halign="center" noWrap="0"')
    # This status surface also reserves the occupied right pane in the
    # shared footer's collision checks, keeping its MENU hint off posters.
    content_bottom = geometry["poster_scroll"][1] + geometry["poster_scroll"][3]
    widget("hint", (px(365), px(332), width - px(365), content_bottom - px(332)),
           px(28), "#CBD5E1", z=9, extra='halign="center" valign="center" noWrap="0"')
    # The common browser keeps these components for playback/resume paths.
    # Keep one-pixel compatibility surfaces; only the visible cards decode art.
    names = [kind + "_" + suffix for suffix in (
        "poster_frame", "poster_placeholder", "poster", "detail_panel", "detail_accent",
        "plot_caption")]
    names += [kind + "_genre"] if kind == "movie" else ["series_grid_panel", "series_grid_accent"]
    for name in names:
        widget(name, (0, 0, 1, 1), z=0)
    parts.append("</screen>")
    return "\n".join(parts)
