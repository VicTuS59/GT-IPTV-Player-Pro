"""Apply native AV choices to the active scaler, including forced-wide modes."""
import importlib

from .diagnostics import log_event
from .i18n import N_


ASPECT_FIELDS = (
    ("aspect", N_("Aspect ratio")),
    ("policy_43", N_("Display 4:3 content as")),
    ("policy_169", N_("Display 16:9 content as")),
)
DISPLAY_NODES = ("/proc/stb/video/aspect", "/proc/stb/video/policy", "/proc/stb/video/policy2")


def _read_node(path):
    try:
        with open(path, "r") as handle:
            return handle.read(256).strip()
    except (OSError, UnicodeError):
        return None


def _write_node(path, value):
    with open(path, "w") as handle:
        if handle.write(value) != len(value):
            raise OSError("Video scaler write was incomplete")
    observed = _read_node(path)
    stretch = {"bestfit", "scale", "auto"}
    if observed != value and not (path != DISPLAY_NODES[0] and observed in stretch and value in stretch):
        raise OSError("Video scaler did not accept the selection")


def _native_display():
    try:
        module = importlib.import_module("Components.AVSwitch")
    except (ImportError, AttributeError):
        return None
    for name in ("avSwitch", "iAVSwitch"):
        switch = getattr(module, name, None)
        if all(callable(getattr(switch, method, None))
               for method in ("setAspect", "setPolicy43", "setPolicy169")):
            return switch
    return None


def _display_targets(av, observed, field):
    """Classic OpenPLi uses logical policy names; drivers use different IDs."""
    element = getattr(av, "aspect", None)
    ratio = str(getattr(element, "value", "")).replace("_", ":")
    if ratio not in ("4:3", "16:9", "16:10", "auto"):
        raise ValueError("Unsupported native aspect")
    if ratio == "auto" and field != "aspect":
        # Editing a fit policy must not be replaced by auto's "bestfit".
        # Use the effective output ratio chosen by the image's notifier.
        current = observed.get(DISPLAY_NODES[0])
        ratio = current if current in ("4:3", "16:9", "16:10") else "16:9"
    classic = any(str(value) in ("4_3", "16_9", "16_10")
                  for value, _label in _selection_choices(element))
    policy43 = str(getattr(getattr(av, "policy_43", None), "value", ""))
    policy169 = str(getattr(getattr(av, "policy_169", None), "value", ""))
    if classic:
        policy43 = {"pillarbox": "panscan", "panscan": "letterbox", "scale": "bestfit"}.get(policy43, policy43)
        policy169 = {"scale": "bestfit"}.get(policy169, policy169)
    if ratio == "auto":
        choices = (_read_node("/proc/stb/video/policy_choices") or "").split()
        policy = "auto" if "auto" in choices else "bestfit"
    else:
        policy = policy169 if ratio == "4:3" else policy43
    targets = {DISPLAY_NODES[0]: "any" if ratio == "auto" else ratio,
               DISPLAY_NODES[1]: policy}
    if observed.get(DISPLAY_NODES[2]) is not None:
        targets[DISPLAY_NODES[2]] = policy169 if ratio in ("16:9", "16:10") else "policy"
    for path, value in list(targets.items()):
        if observed.get(path) is None or not value:
            raise OSError("Video scaler control is unavailable")
        supported = _read_node(path + "_choices")
        if supported is not None and value not in supported.split():
            # "policy" tells policy2 to follow the main policy; some drivers
            # omit that alias. In 4:3/auto mode policy2 is not used.
            if path == DISPLAY_NODES[2] and value == "policy":
                targets.pop(path)
                continue
            raise ValueError("Video scaler choice is unavailable")
    return targets


def _apply_display(av, field):
    switch = _native_display()
    if switch is not None:
        for name, method in (("aspect", "setAspect"), ("policy_43", "setPolicy43"), ("policy_169", "setPolicy169")):
            element = getattr(av, name, None)
            if element is not None and getattr(switch, method)(element) is False:
                raise OSError("Native scaler rejected the selection")
        log_event("display", "applied field={} backend=native".format(field))
        return True
    observed = {path: _read_node(path) for path in DISPLAY_NODES}
    targets = _display_targets(av, observed, field)
    if field == "policy_169" and targets[DISPLAY_NODES[0]] in ("16:9", "16:10") and DISPLAY_NODES[2] not in targets:
        raise OSError("Secondary scaler policy is unavailable")
    # Deliberately apply the selected ratio after OpenPLi's notifier. Its
    # updateAspect() otherwise forces HDMI 720p/1080p/2160p back to 16:9.
    for path, value in targets.items():
        _write_node(path, value)
    log_event("display", "applied field={} backend=scaler".format(field))
    return True


def _native_av():
    try:
        from Components.config import config, configfile
        return config.av, configfile
    except (ImportError, AttributeError):
        return None, None


def _selection_choices(element):
    """Read actual IDs, including image-specific numeric policy values."""
    pairs = None
    getter = getattr(element, "getSelectionList", None)
    if callable(getter):
        try:
            pairs = list(getter())
        except Exception:
            pass
    if pairs is None:
        choices = getattr(element, "choices", ())
        choices = getattr(choices, "choices", choices)
        if isinstance(choices, dict):
            pairs = list(choices.items())
        else:
            pairs = []
            try:
                description = getattr(element, "description", {})
                for choice in choices:
                    if isinstance(choice, (tuple, list)) and len(choice) == 2:
                        pairs.append(choice)
                    else:
                        try:
                            label = description[choice]
                        except (KeyError, IndexError, TypeError):
                            label = choice
                        pairs.append((choice, label))
            except (TypeError, AttributeError):
                return []
    result, seen = [], set()
    for pair in pairs[:32]:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            continue
        value, label = pair
        if not isinstance(value, (str, int)) or not str(value) or str(value) in seen:
            continue
        seen.add(str(value))
        label = " ".join(str(label or value).split())[:120]
        result.append((value, label))
    return result


def aspect_groups():
    """Only expose config selections supported by the installed image."""
    av, _configfile = _native_av()
    groups = []
    for field, title in ASPECT_FIELDS:
        element = getattr(av, field, None)
        if element is None or not hasattr(element, "value"):
            continue
        choices = _selection_choices(element)
        if choices:
            groups.append({
                "field": field, "title": title, "element": element,
                "choices": choices, "current": element.value,
            })
    return groups


def apply_aspect_choice(field, expected_element, value):
    """Validate again, notify the video driver, and save through native config."""
    group = next((group for group in aspect_groups()
                  if group["field"] == field and group["element"] is expected_element), None)
    if group is None:
        return False
    matches = [native for native, _label in group["choices"] if str(native) == str(value)]
    if not matches:
        return False
    selected = matches[0]
    previous = expected_element.value
    changed = str(previous) != str(selected)
    av, configfile = _native_av()
    save = getattr(expected_element, "save", None)
    save_file = getattr(configfile, "save", None)
    if changed and (not callable(save) or not callable(save_file)):
        return False
    missing = object()
    saved_value = getattr(expected_element, "saved_value", missing)
    last_value = getattr(expected_element, "last_value", missing)
    output_before = {path: _read_node(path) for path in DISPLAY_NODES}
    file_attempted = False
    try:
        expected_element.value = selected
        if str(expected_element.value) != str(selected):
            raise ValueError("Native AV selection rejected")
        if changed and save() is False:
            raise ValueError("Native AV selection could not be saved")
        # Config notifiers alone can ignore an aspect choice in HD/4K, and
        # choosing an already saved value does not trigger them at all.
        if not _apply_display(av, field):
            raise OSError("Video scaler could not be updated")
        if changed:
            file_attempted = True
            if save_file() is False:
                raise ValueError("Native AV configuration could not be saved")
        return True
    except Exception as error:
        try:
            expected_element.value = previous
        except Exception:
            pass
        for name, value in (("saved_value", saved_value), ("last_value", last_value)):
            if value is not missing:
                try:
                    setattr(expected_element, name, value)
                except Exception:
                    pass
        if any(value is not None for value in output_before.values()):
            for path, value in output_before.items():
                if value is not None:
                    try:
                        _write_node(path, value)
                    except Exception:
                        pass
        else:
            try:
                _apply_display(av, field)
            except Exception:
                pass
        if file_attempted:
            try:
                save_file()
            except Exception:
                pass
        log_event("display", "rejected field={} value={} reason={}".format(field, str(selected), error.__class__.__name__))
        return False
