# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

"""Local category-name filter editor; the parent owns persistence."""

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap

from .browser import (
    GTAsyncListScreen, CATEGORY_FILTER_FOOTER_ITEMS,
    _live_category_manager_skin, _scale,
)
from .category_filters import (
    category_filter_ids, normalize_filter_term, scan_category_filters,
)
from .i18n import N_, _
from .scrollbar import update_scrollbar
from .typography import ellipsize_dynamic_text


class GTCategoryFilterScreen(GTAsyncListScreen):
    """Scan server category names and stage rules without saving to disk."""

    page_size = 8

    def __init__(
        self, session, categories, include_terms=(), exclude_terms=(),
        hidden_ids=(),
    ):
        self._categories = list(categories)
        self._include = set(include_terms)
        self._exclude = set(exclude_terms)
        self._hidden_ids = set(hidden_ids)
        self._query = ""
        self._preview = False
        self._filter_position = 0
        self._candidates = []
        self._scan_selected = ""
        self._scan_include = ()
        self._scan_exclude = ()
        self._allowed_ids = set()
        self._excluded_ids = set()
        self._footer_layout_items = CATEGORY_FILTER_FOOTER_ITEMS
        self.skin = _live_category_manager_skin().replace(
            'name="GTLiveCategoryManagerScreen"',
            'name="GTCategoryFilterScreen"', 1,
        )
        unused_width, unused_height, px = _scale()
        self._list_scroll_geometry = (px(1036), px(252), px(8), px(626))
        GTAsyncListScreen.__init__(
            self, session, "GTCategoryFilterScreen", N_("Filter Search"),
            _("Filter rules: Show / Hide / Clear"),
        )
        self["screen_label"].setText(_("Category Manager"))
        self["manager_panel_accent_magenta"] = Label("")
        self["manager_caption"] = Label(_("Filter Search"))
        self["manager_selection_count"] = Label("")
        self["manager_move_help"] = Label("")
        for index in range(self.page_size):
            self["category_row_{}".format(index)] = Label("")
            self["category_state_{}".format(index)] = Label("")
            self["category_check_{}_empty".format(index)] = Pixmap()
            self["category_check_{}_tick".format(index)] = Pixmap()
            self["category_insert_{}".format(index)] = Label("")
            for edge in ("fill", "top", "bottom", "left", "right"):
                self["category_focus_{}_{}".format(index, edge)] = Label("")
        self._hide_manager_only_components()
        self._ready = True
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions", "MenuActions"],
            {
                "ok": self.open_selected,
                "cancel": self.cancel_or_close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.page_up,
                "right": self.page_down,
                "leftRepeated": self.page_up,
                "rightRepeated": self.page_down,
                "red": self.clear_selected,
                "green": self.accept_rules,
                "yellow": self.toggle_preview,
                "blue": self.open_keyboard,
                "menu": self.rescan,
            },
            -1,
        )
        if hasattr(self, "onShown"):
            self.onShown.append(self._refresh)
        self.rescan()

    def load_items(self):
        return scan_category_filters(
            self._categories, self._scan_include, self._scan_exclude
        )

    def rescan(self):
        # Reuse the portal category snapshot; never request stream catalogues.
        if self._loading or self._closed:
            return
        self._scan_selected = self._selected_term()
        self._scan_include = tuple(self._include)
        self._scan_exclude = tuple(self._exclude)
        self.start_load()
        self._refresh()

    def _poll_result(self):
        if self._closed:
            return
        if not self._ready:
            GTAsyncListScreen._poll_result(self)
            return
        self._loading = False
        self._load_token = None
        if self._error:
            self._refresh()
            return
        self._candidates = list(self._entries)
        self._entries = []
        self._update_preview_counts()
        self._rebuild_entries(self._scan_selected)

    def _selected_term(self):
        if self._loading or self._preview or not self._entries:
            return ""
        return self._entries[self.selected_index]["term"]

    def _update_preview_counts(self):
        allowed, unused_excluded = category_filter_ids(
            self._categories, self._include, self._exclude
        )
        self._allowed_ids = set(allowed).difference(self._hidden_ids)
        current_ids = set(
            str(entry.category_id).strip() for entry in self._categories
            if str(getattr(entry, "category_id", "") or "").strip()
        )
        self._excluded_ids = current_ids.difference(self._allowed_ids)

    def _rebuild_entries(self, selected_term=""):
        if self._preview:
            self._entries = [
                {"category_id": str(entry.category_id), "label": str(entry.name)}
                for entry in self._categories
                if str(getattr(entry, "category_id", "") or "").strip()
            ]
        else:
            query = normalize_filter_term(self._query)
            self._entries = [
                entry for entry in self._candidates
                if not query or query in normalize_filter_term(entry["label"])
            ]
        self.selected_index = min(
            max(0, self.selected_index), max(0, len(self._entries) - 1)
        )
        if selected_term and not self._preview:
            for index, entry in enumerate(self._entries):
                if entry["term"] == selected_term:
                    self.selected_index = index
                    break
        self._refresh()

    def _refresh(self):
        self._hide_manager_only_components()
        if self._loading or self._error:
            for row_index in range(self.page_size):
                self["category_row_{}".format(row_index)].setText("")
                self["category_state_{}".format(row_index)].setText("")
                for edge in ("fill", "top", "bottom", "left", "right"):
                    self["category_focus_{}_{}".format(row_index, edge)].hide()
            self["category_row_0"].setText(
                _("Loading content...") if self._loading else _(self._error)
            )
            self["message"].setText(_("Please wait") if self._loading else "")
            return
        self.selected_index = min(
            max(0, self.selected_index), max(0, len(self._entries) - 1)
        )
        page_start = (self.selected_index // self.page_size) * self.page_size
        page = self._entries[page_start:page_start + self.page_size]
        for row_index in range(self.page_size):
            selected = False
            row_text = ""
            state = ""
            if row_index < len(page):
                entry = page[row_index]
                row_text = entry["label"]
                if self._preview:
                    state = (
                        _("Hidden") if entry["category_id"] in self._excluded_ids
                        else _("Visible")
                    )
                else:
                    term = entry["term"]
                    state = (
                        _("Hide") if term in self._exclude else
                        _("Show") if term in self._include else "-"
                    )
                    state = "{} ({})".format(state, entry["count"])
                selected = page_start + row_index == self.selected_index
            ellipsize_dynamic_text(
                self["category_row_{}".format(row_index)], row_text,
                fallback_chars=34,
            )
            ellipsize_dynamic_text(
                self["category_state_{}".format(row_index)], state,
                fallback_chars=18,
            )
            for edge in ("fill", "top", "bottom", "left", "right"):
                widget = self["category_focus_{}_{}".format(row_index, edge)]
                if selected:
                    widget.show()
                else:
                    widget.hide()
        if not self._entries:
            self["category_row_0"].setText(_("No filters found."))
        caption = _("Preview") if self._preview else _("Filter Search")
        if self._query and not self._preview:
            caption = "{}: {}".format(caption, self._query)
        ellipsize_dynamic_text(self["manager_caption"], caption, fallback_chars=45)
        status = "{}: {}  |  {}: {}  |  {}/{}".format(
            _("Visible"), len(self._allowed_ids),
            _("Hidden"), len(self._excluded_ids),
            self.selected_index + 1 if self._entries else 0, len(self._entries),
        )
        ellipsize_dynamic_text(self["message"], status, fallback_chars=70)
        update_scrollbar(
            self, "list_scroll", len(self._entries), self.selected_index,
            self.page_size, self._list_scroll_geometry,
        )

    def _hide_manager_only_components(self):
        """Keep inert widgets inherited from the shared manager skin hidden."""
        self["manager_selection_count"].hide()
        self["manager_move_help"].hide()
        for row_index in range(self.page_size):
            self["category_check_{}_empty".format(row_index)].hide()
            self["category_check_{}_tick".format(row_index)].hide()
            self["category_insert_{}".format(row_index)].hide()

    def open_selected(self):
        if self._preview:
            self.toggle_preview()
            return
        term = self._selected_term()
        if not term:
            return
        if term in self._exclude:
            self._include.discard(term)
            self._exclude.discard(term)
        elif term in self._include:
            self._include.discard(term)
            self._exclude.add(term)
        else:
            self._include.add(term)
        self._update_preview_counts()
        self._refresh()

    def clear_selected(self):
        term = self._selected_term()
        if not term:
            return
        self._include.discard(term)
        self._exclude.discard(term)
        self._update_preview_counts()
        self._refresh()

    def toggle_preview(self):
        if self._loading:
            return
        if self._preview:
            self._preview = False
            self.selected_index = self._filter_position
        else:
            self._filter_position = self.selected_index
            self._preview = True
            self.selected_index = 0
        self._rebuild_entries()

    def open_keyboard(self):
        if self._loading:
            return
        if self._preview:
            self.toggle_preview()
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self["message"].setText(
                _("The virtual keyboard is unavailable on this image.")
            )
            return
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self["message"].setText(_("Could not open the virtual keyboard."))
            return
        opener(
            self._query_entered, VirtualKeyBoard,
            title=_("Filter Search"), text=self._query,
        )

    def _query_entered(self, value):
        if value is None or self._closed or self._loading:
            return
        self._query = str(value).strip()[:160]
        self.selected_index = 0
        self._rebuild_entries()

    def accept_rules(self):
        self.close((tuple(sorted(self._include)), tuple(sorted(self._exclude))))

    def cancel_or_close(self):
        if self._preview and not self._loading:
            self.toggle_preview()
            return
        self.close(None)
