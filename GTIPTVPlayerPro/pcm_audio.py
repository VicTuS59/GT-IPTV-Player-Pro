# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Expose a receiver's native PCM multichannel switch to the player menu."""
from .diagnostics import log_event
from .i18n import N_, _
from .video_aspect import _native_av


_DRIVER_PATH = "/proc/stb/audio/multichannel_pcm"
_UNSUPPORTED = "unsupported"
_BLOCKED = "downmix"
_UNAVAILABLE = "unavailable"
_ERROR = "error"
_LABELS = {
    _UNSUPPORTED: N_("Not supported"),
    _BLOCKED: N_("AC3 downmix is active"),
    _UNAVAILABLE: N_("Temporarily unavailable"),
    _ERROR: N_("Setting could not be changed"),
}


def _capability(name):
    try:
        import Components.SystemInfo as system_info
    except ImportError:
        return None
    getter = getattr(getattr(system_info, "BoxInfo", None), "getItem", None)
    if callable(getter):
        try:
            value = getter(name)
            if value is not None:
                return value
        except (KeyError, TypeError, AttributeError, RuntimeError):
            pass
    get_legacy = getattr(getattr(system_info, "SystemInfo", None), "get", None)
    return get_legacy(name) if callable(get_legacy) else None


def _control():
    av, configfile = _native_av()
    save_file = getattr(configfile, "save", None)
    if not callable(save_file):
        return None, None, "", _UNSUPPORTED

    for field, support_flag in (("pcm_multichannel", "supportPcmMultichannel"),
                                ("multichannel_pcm", "HasMultichannelPCM")):
        element = getattr(av, field, None)
        value = getattr(element, "value", None)
        if (type(value) is not bool or not callable(getattr(element, "setValue", None))
                or not callable(getattr(element, "save", None))):
            continue
        support = _capability(support_flag)
        if support is not None and not support:
            continue
        available = _capability("CanPcmMultichannel") if field == "pcm_multichannel" else None
        if available is not None and not available:
            downmix = getattr(getattr(av, "downmix_ac3", None), "value", None)
            state = _BLOCKED if downmix is True or downmix in ("downmix", "0") else _UNAVAILABLE
            return None, None, "", state
        path = _DRIVER_PATH
        if (isinstance(support, str) and support.startswith(("/proc/stb/audio/", "/sys/class/"))
                and ".." not in support.split("/")):
            path = support
        return element, save_file, path, value
    return None, None, "", _UNSUPPORTED


def pcm_status():
    return _control()[-1]


def pcm_menu_label(state):
    title = _("PCM Multichannel Audio")
    if state is True:
        label = _("On")
    elif state is False:
        label = _("Off")
    else:
        label = _(_LABELS[state])
    return "{} — {}".format(title, label)


def _read_driver(path):
    try:
        with open(path, "r") as source:
            value = source.read(32).strip().lower()
    except (OSError, UnicodeError):
        return None
    if value in ("enable", "enabled", "on", "1"):
        return True
    if value in ("disable", "disabled", "off", "0"):
        return False
    return None


def toggle_pcm():
    """Recheck the native capability before applying one global AV change."""
    element, save_file, path, previous = _control()
    if element is None:
        return previous
    desired = not previous
    missing = object()
    saved = [(key, getattr(element, key, missing)) for key in ("saved_value", "last_value")]
    file_attempted = False
    try:
        element.setValue(desired)
        if element.value is not desired:
            raise ValueError("Native PCM setting rejected")
        applied = _read_driver(path)
        if applied is not None and applied is not desired:
            raise OSError("PCM driver rejected the setting")
        if element.save() is False:
            raise OSError("Native PCM setting could not be saved")
        file_attempted = True
        if save_file() is False:
            raise OSError("Native AV configuration could not be saved")
        return desired
    except Exception as error:
        try:
            element.setValue(previous)
        except Exception:
            pass
        for key, value in saved:
            if value is not missing:
                try:
                    setattr(element, key, value)
                except Exception:
                    pass
        if file_attempted:
            try:
                save_file()
            except Exception:
                pass
        log_event("audio", "PCM setting rejected: {}".format(type(error).__name__))
        return _ERROR
