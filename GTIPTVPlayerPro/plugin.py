# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

from Plugins.Plugin import PluginDescriptor

from . import PLUGIN_NAME
from .i18n import _

PLUGIN_ICON = "plugin.svg"
MAIN_MENU_ENTRY_ID = "gt_iptv_player_pro"
MAIN_MENU_WEIGHT = 50


def _log_scheduler_failure(message, error):
    try:
        from .diagnostics import log_event

        log_event("epg_scheduler", message, error)
    except Exception:
        pass


def _prepare_storage():
    # plugin.py is imported while Enigma2 discovers installed plugins.
    from .playlist import ensure_playlist_file

    try:
        ensure_playlist_file()
    except (IOError, OSError):
        # The screen can still open and show a useful error if storage is read-only.
        pass
    try:
        from .playlist_files import default_registry

        # Loading either bank performs the one-time, path-only R100 migration
        # for both legacy TXT files.  The source files themselves are untouched.
        default_registry().slots("xtream")
    except (IOError, OSError, TypeError, ValueError):
        pass


def main(session, **kwargs):
    from .main import GTIPTVPlayerProScreen

    _prepare_storage()
    session.open(GTIPTVPlayerProScreen)


def menu(menuid, **kwargs):
    if menuid != "mainmenu":
        return []
    return [
        (
            PLUGIN_NAME,
            main,
            MAIN_MENU_ENTRY_ID,
            MAIN_MENU_WEIGHT,
        )
    ]


def session_start(reason, session=None, **kwargs):
    """Start or stop the optional EPG schedulers with the GUI session."""
    try:
        from .dvb_epg_scheduler import session_start as dvb_scheduler_start

        dvb_scheduler_start(reason, session=session, **kwargs)
    except Exception as error:
        # Scheduler failures must never prevent Enigma2 plugin discovery or
        # stop the independent M3U XMLTV lifecycle from being serviced.
        _log_scheduler_failure("DVB EPG scheduler hook failed", error)
    try:
        from .m3u_epg_scheduler import session_start as m3u_scheduler_start

        m3u_scheduler_start(reason, session=session, **kwargs)
    except Exception as error:
        _log_scheduler_failure("M3U XMLTV scheduler hook failed", error)


def autostart(reason, **kwargs):
    """Provide the matching shutdown hook on images using AUTOSTART."""
    session = kwargs.pop("session", None)
    session_start(reason, session=session, **kwargs)


def Plugins(**kwargs):
    descriptors = [
        PluginDescriptor(
            name=PLUGIN_NAME,
            description=_("Modern Enigma2 IPTV player"),
            where=PluginDescriptor.WHERE_PLUGINMENU,
            icon=PLUGIN_ICON,
            fnc=main,
        ),
        PluginDescriptor(
            where=PluginDescriptor.WHERE_MENU,
            fnc=menu,
        ),
    ]
    session_start_location = getattr(
        PluginDescriptor,
        "WHERE_SESSIONSTART",
        None,
    )
    if session_start_location is not None:
        descriptors.append(
            PluginDescriptor(
                where=session_start_location,
                fnc=session_start,
            )
        )
    autostart_location = getattr(PluginDescriptor, "WHERE_AUTOSTART", None)
    if autostart_location is not None:
        descriptors.append(
            PluginDescriptor(
                where=autostart_location,
                fnc=autostart,
            )
        )
    return descriptors
