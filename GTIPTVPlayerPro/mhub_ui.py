# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manage user-entered mHub sources and open their native Live TV browser."""

import uuid

from Screens.MessageBox import MessageBox

from .i18n import _
from .mhub_sources import (
    MHubSource,
    MHubSourceConflict,
    delete_mhub_source,
    load_mhub_sources,
    save_mhub_source,
)


def open_mhub_sources(session):
    try:
        from Screens.ChoiceBox import ChoiceBox
    except ImportError:
        ChoiceBox = None
    if ChoiceBox is None or not callable(getattr(session, "openWithCallback", None)):
        session.open(MessageBox, _("Source unavailable"), MessageBox.TYPE_ERROR)
        return
    _MHubSourceManager(session, ChoiceBox).open()


class _MHubSourceManager:
    TITLE = "mHub v2"

    def __init__(self, session, choice_box):
        self.session = session
        self.choice_box = choice_box

    def open(self, *unused):
        try:
            sources = load_mhub_sources()
        except (OSError, ValueError):
            self.session.open(MessageBox, _("Source unavailable"), MessageBox.TYPE_ERROR)
            return
        choices = [(_("Add Source"), None)]
        choices.extend((source.display_name, source) for source in sources)
        self.session.openWithCallback(
            self._source_selected, self.choice_box, title=self.TITLE, list=choices,
        )

    def _message(self, text, callback, kind=MessageBox.TYPE_ERROR):
        self.session.openWithCallback(callback, MessageBox, text, kind)

    def _source_selected(self, selection=None):
        if not selection:
            return
        source = selection[1]
        if source is None:
            self._edit_name()
        else:
            self._source_actions(source)

    def _source_actions(self, source):
        choices = [(_("Open"), "open"), (_("Edit"), "edit"), (_("Delete"), "delete")]
        self.session.openWithCallback(
            lambda selection=None: self._action_selected(source, selection),
            self.choice_box,
            title=source.display_name,
            list=choices,
        )

    def _action_selected(self, source, selection):
        action = selection[1] if selection else ""
        if action == "open":
            self._open_live(source)
        elif action == "edit":
            self._edit_name(source)
        elif action == "delete":
            self.session.openWithCallback(
                lambda answer=None: self._delete_source(source, answer),
                MessageBox,
                _("Delete this item?"),
                MessageBox.TYPE_YESNO,
                default=False,
            )
        else:
            self.open()

    def _keyboard(self, callback, title, text):
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self._message(_("The virtual keyboard is unavailable on this image."), self.open)
            return
        self.session.openWithCallback(callback, VirtualKeyBoard, title=title, text=text)

    def _edit_name(self, source=None):
        self._keyboard(
            lambda value=None: self._name_entered(source, value),
            _("Enter a source name"),
            source.name if source is not None else "",
        )

    def _name_entered(self, source, value):
        if value is None:
            self.open()
            return
        name = str(value).strip()
        if not name:
            self._message(_("Enter a source name"), lambda *unused: self._edit_name(source))
            return
        self._edit_url(source, name, source.location if source is not None else "https://")

    def _edit_url(self, source, name, location):
        self._keyboard(
            lambda value=None: self._url_entered(source, name, value),
            _("Enter the server URL supplied by your provider."),
            location,
        )

    def _url_entered(self, source, name, value):
        if value is None:
            self.open()
            return
        location = str(value).strip()
        source_id = source.source_id if source is not None else uuid.uuid4().hex
        try:
            updated = MHubSource(source_id, name, location)
        except ValueError:
            self._message(
                _("Enter a valid server URL"),
                lambda *unused: self._edit_url(source, name, location),
            )
            return
        try:
            save_mhub_source(updated)
        except MHubSourceConflict:
            self._message(
                _("SAVED SOURCE"),
                lambda *unused: self._edit_url(source, name, location),
                MessageBox.TYPE_INFO,
            )
            return
        except (OSError, ValueError):
            self._message(_("Source unavailable"), self.open)
            return
        self.open()

    def _delete_source(self, source, confirmed):
        if not confirmed:
            self._source_actions(source)
            return
        try:
            delete_mhub_source(source.source_id)
        except (OSError, ValueError):
            self._message(_("Could not delete the source: {}").format(source.display_name), self.open)
            return
        self.open()

    def _open_live(self, source):
        from .content import ContentError
        from .mhub import MHubContentClient

        try:
            client = MHubContentClient(source)
        except ContentError:
            self._message(_("Source unavailable"), self.open)
            return

        from .browser_categories import GTContentBrowserScreen

        self.session.openWithCallback(
            self.open, GTContentBrowserScreen, source, "live", client=client,
        )
