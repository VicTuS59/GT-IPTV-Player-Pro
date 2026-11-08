# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

from Plugins.Plugin import PluginDescriptor

from . import PLUGIN_NAME
from .i18n import _

PLUGIN_ICON = "plugin.svg"
MAIN_MENU_ENTRY_ID = "gt_iptv_player_pro"
MAIN_MENU_WEIGHT = 50


def _log_hook_failure(area, message, error):
    try:
        from .diagnostics import log_event

        log_event(area, message, error)
    except Exception:
        pass


def _prepare_storage():
    # Finish or roll back an interrupted restore before any first-run writer
    # or scheduler touches the plugin's persistent state.
    try:
        from .backup import BackupManager
        BackupManager().recover()
    except Exception as error:
        _log_hook_failure("epg_scheduler", "Backup recovery failed", error)
        return False
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
    return True


def _install_optional_session_hooks(session):
    try:
        from .web_shortcut import install as install_web_shortcut

        install_web_shortcut(session)
    except Exception as error:
        _log_hook_failure("web_shortcut", "Web MENU shortcut setup failed", error)
    try:
        from .download_guard import install as install_download_guard

        install_download_guard(session)
    except Exception as error:
        _log_hook_failure("download_guard", "Download monitor setup failed", error)


def main(session, **kwargs):
    from .main import GTIPTVPlayerProScreen

    if not _prepare_storage():
        from Screens.MessageBox import MessageBox
        session.open(MessageBox, _("Restore failed. Use the safety backup."), MessageBox.TYPE_ERROR)
        return
    _install_optional_session_hooks(session)
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
        if not _prepare_storage():
            return
        _install_optional_session_hooks(session)
        try:
            from .web_remote import WEB_REMOTE

            WEB_REMOTE.bind_session(session)
        except Exception as error:
            _log_hook_failure("web_remote", "Web remote session setup failed", error)
        try:
            # OpenWebif builds its external resource tree after ordinary
            # session-start plugins.  Register the gated /gtiptv endpoint now;
            # images without OpenWebif use the temporary port-9999 fallback.
            from .web_server import register_openwebif

            register_openwebif()
        except Exception:
            pass
    elif reason != 0:
        from .download_guard import uninstall as uninstall_download_guard
        uninstall_download_guard()
        from .persistence import WRITER
        WRITER.flush(timeout=2)
        from .web_shortcut import uninstall as uninstall_web_shortcut
        uninstall_web_shortcut()
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
        _log_hook_failure("epg_scheduler", "DVB EPG scheduler hook failed", error)
    try:
        from .m3u_epg_scheduler import session_start as m3u_scheduler_start

        m3u_scheduler_start(reason, session=session, **kwargs)
    except Exception as error:
        _log_hook_failure("epg_scheduler", "M3U XMLTV scheduler hook failed", error)


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
