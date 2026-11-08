# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Use an image's native Dolby Digital and DTS output policies."""
from collections import namedtuple

from .diagnostics import log_event
from .i18n import N_, _
from .pcm_audio import _capability
from .persistence import guarded_mutation
from .video_aspect import _native_av, _selection_choices


_CODECS = {
    "ac3": ("Dolby Digital (AC3)", "downmix_ac3", "CanDownmixAC3"),
    "dts": ("DTS", "downmix_dts", "CanDownmixDTS"),
}
_UNAVAILABLE = "unavailable"
_ERROR = "error"
_OTHER = "other"
_AUTO = "automatic"
_PCM = "pcm"
_MODE_STATES = {
    "passthrough": True, "downmix": False,
    "hdmibest": _AUTO, "usehdmicaps": _AUTO, "auto": _AUTO, "automatic": _AUTO,
    "multichannel": _PCM, "multichannelpcm": _PCM, "pcm": _PCM,
}
_STATUS_LABELS = {
    _UNAVAILABLE: N_("Not available"), _ERROR: N_("Setting could not be changed"),
    _AUTO: N_("Automatic"), _PCM: N_("PCM Multichannel Audio"), _OTHER: N_("Unknown"),
}
_Control = namedtuple("_Control", "av element save_file off on status")


def _mode(value, dream_numeric=False):
    key = "".join(character for character in str(value).strip().lower() if character.isalnum())
    if dream_numeric and key in ("0", "1", "2"):
        return {"0": False, "1": True, "2": _AUTO}[key]
    return _MODE_STATES.get(key, _OTHER)


def _editable(element):
    return callable(getattr(element, "setValue", None)) and callable(getattr(element, "save", None))


def _control(codec):
    if codec not in _CODECS:
        return None
    try:
        unused, field, flag = _CODECS[codec]
        av, configfile = _native_av()
        element = getattr(av, field, None)
        save_file = getattr(configfile, "save", None)
        if not _editable(element) or not callable(save_file):
            return None
        read_only = getattr(element, "isReadOnly", None)
        if (callable(read_only) and read_only()) or getattr(element, "save_disabled", False):
            return None
        support = _capability(flag)
        if support is not None and not support:
            return None
        current = element.value
        if type(current) is bool:
            # Native ConfigYesNo represents downmix. Disabling it passes the codec through.
            return _Control(av, element, save_file, True, False, not current)
        numeric = codec == "ac3" and _capability("machinebuild") in ("dreamone", "dreamtwo")
        values = {}
        for value, unused_label in _selection_choices(element):
            mode = _mode(value, numeric)
            if type(mode) is bool:
                values.setdefault(mode, value)
        if len(values) == 2:
            return _Control(av, element, save_file, values[False], values[True], _mode(current, numeric))
    except Exception:
        # An image can leave a placeholder or a broken optional binding in config.av.
        pass
    return None


def passthrough_title():
    return "Dolby Digital / DTS — " + _("Digital audio passthrough")


def _status_label(state):
    if state is True:
        return _("On")
    if state is False:
        return _("Off")
    return _(_STATUS_LABELS[state])


def passthrough_label(codec, state):
    return "{} — {}".format(_CODECS[codec][0], _status_label(state))


def passthrough_rows():
    rows = []
    for codec in _CODECS:
        control = _control(codec)
        state = control.status if control is not None else _UNAVAILABLE
        rows.append((passthrough_label(codec, state), codec))
    return rows


def passthrough_choices(codec):
    control = _control(codec)
    if control is None:
        return None, passthrough_label(codec, _UNAVAILABLE), []
    choices = [(_status_label(state) + ("  ✓" if control.status is state else ""), state)
               for state in (True, False)]
    return control.element, passthrough_label(codec, control.status), choices


def _driver_mode(codec):
    # The Amlogic AC3 notifier uses a different, machine-specific control.
    if codec == "ac3" and _capability("AmlogicFamily"):
        return _OTHER
    try:
        with open("/proc/stb/audio/" + codec, "r") as source:
            return _mode(source.read(32))
    except (OSError, UnicodeError):
        return _OTHER


def _snapshot(element):
    saved = {name: getattr(element, name) for name in ("saved_value", "last_value", "lastValue", "loadValue")
             if hasattr(element, name)}
    return element, element.value, saved


def _restore(snapshots, saved_elements):
    # Restore AC3 first. Its notifier can change the dependent PCM setting.
    for element, previous, saved in snapshots:
        try:
            if element.value != previous:
                element.setValue(previous)
                if id(element) in saved_elements:
                    element.save()
        except Exception:
            pass
        for name, value in saved.items():
            try:
                setattr(element, name, value)
            except Exception:
                pass


@guarded_mutation(failure=_UNAVAILABLE)
def set_passthrough(codec, enabled, expected_element):
    control = _control(codec)
    if control is None or control.element is not expected_element or type(enabled) is not bool:
        return _UNAVAILABLE
    if control.status is enabled:
        return enabled
    snapshots = []
    saved_elements = set()
    file_attempted = False
    try:
        snapshots.append(_snapshot(control.element))
        if codec == "ac3":
            # Some images disable PCM in the AC3 notifier. Persist or roll back
            # only changes made by that notifier.
            for name in ("pcm_multichannel", "multichannel_pcm"):
                element = getattr(control.av, name, None)
                if _editable(element) and type(element.value) is bool:
                    if all(element is not entry[0] for entry in snapshots):
                        snapshots.append(_snapshot(element))
        desired = control.on if enabled else control.off
        control.element.setValue(desired)
        if str(control.element.value) != str(desired):
            raise ValueError("Native passthrough selection rejected")
        for element, previous, unused in snapshots:
            if element.value != previous:
                saved_elements.add(id(element))
                if element.save() is False:
                    raise OSError("Native audio selection could not be saved")
        applied = _driver_mode(codec)
        if applied != _OTHER and applied is not enabled:
            raise OSError("Audio driver rejected the passthrough selection")
        file_attempted = True
        if control.save_file() is False:
            raise OSError("Native AV configuration could not be saved")
        return enabled
    except Exception as error:
        _restore(snapshots, saved_elements)
        if file_attempted:
            try:
                control.save_file()
            except Exception:
                pass
        try:
            log_event("audio", "Passthrough selection rejected", error)
        except Exception:
            pass
        return _ERROR
