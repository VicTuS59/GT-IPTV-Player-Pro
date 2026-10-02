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
    # Create both first-run account files before registering their TXT banks.
    from .playlist import ensure_playlist_file

    try:
        ensure_playlist_file()
    except (IOError, OSError):
        # The screen can still open and show a useful error if storage is read-only.
        pass
    try:
        from .stalker import ensure_portal_file

        ensure_portal_file()
    except (ImportError, IOError, OSError):
        pass
    try:
        from .playlist_files import default_registry

        # Bind empty or populated default files once for each bank. A user's
        # existing TXT selection stays in control after an upgrade.
        default_registry().slots("xtream")
    except (IOError, OSError, TypeError, ValueError):
        pass


def main(session, **kwargs):
    from .main import GTIPTVPlayerProScreen

    _prepare_storage()
    from .download_guard import install as install_download_guard
    install_download_guard(session)
    session.open(GTIPTVPlayerProScreen)


def menu(menuid, **kwargs):
    if menuid != "mainmenu":
        return []
    from .settings import load_main_menu_visibility

    # Read on each menu build so saved changes apply on the next opening.
    # The plugin-browser descriptor stays available independently.
    if not load_main_menu_visibility():
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
    if reason == 0 and session is not None:
        _prepare_storage()
        from .web_remote import WEB_REMOTE
        WEB_REMOTE.bind_session(session)
        try:
            from .download_guard import install as install_download_guard
            install_download_guard(session)
        except Exception:
            pass
        try:
            # OpenWebif builds its external resource tree after ordinary
            # session-start plugins.  Register the gated /gtiptv endpoint now;
            # images without OpenWebif use the temporary port-9999 fallback.
            from .web_server import register_openwebif

            register_openwebif()
        except Exception:
            pass
    elif reason != 0:
        from .web_remote import WEB_REMOTE
        WEB_REMOTE.bind_session(None)
        try:
            from .web_server import get_runtime

            get_runtime().stop()
        except Exception:
            pass
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
        session_descriptor = PluginDescriptor(
            where=session_start_location,
            fnc=session_start,
        )
        # OpenWebif deliberately starts late (weight 100).  Keep GT at 90 so
        # its optional external path is present when OpenWebif builds the tree.
        session_descriptor.weight = 90
        descriptors.append(session_descriptor)
    autostart_location = getattr(PluginDescriptor, "WHERE_AUTOSTART", None)
    if autostart_location is not None:
        descriptors.append(
            PluginDescriptor(
                where=autostart_location,
                fnc=autostart,
            )
        )
    return descriptors

