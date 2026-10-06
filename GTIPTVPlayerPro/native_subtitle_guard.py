# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Keep hidden native InfoBars from selecting subtitles for GT-owned VOD.

InfoBarSubtitleSupport's already-bound service callbacks resolve its getter
and enable method at call time on both OpenPLi and OpenATV.  Guarding those
instance methods leaves service-event routing and image preferences intact.
GT's embedded controller calls the decoder directly, outside these hooks.
"""


_MISSING = object()
_OWNER_HOOKS = {}


def _reference_key(reference):
    if reference is None:
        return ""
    for name in ("toCompareString", "toString"):
        method = getattr(reference, name, None)
        if not callable(method):
            continue
        try:
            value = method()
        except Exception:
            continue
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if isinstance(value, str) and value:
            return value
    if isinstance(reference, bytes):
        return reference.decode("utf-8", "replace")
    return reference if isinstance(reference, str) else ""


def _current_reference(session):
    navigation = getattr(session, "nav", None)
    for name in ("getCurrentlyPlayingServiceReference",
                 "getCurrentlyPlayingServiceOrGroup"):
        getter = getattr(navigation, name, None)
        if callable(getter):
            try:
                reference = getter()
            except Exception:
                continue
            if reference is not None:
                return reference
    return None


def _native_owners(session):
    candidates = []
    try:
        from Screens.InfoBar import InfoBar
        candidates.append(getattr(InfoBar, "instance", None))
    except Exception:
        pass
    try:
        from Components.ServiceEventTracker import ServiceEventTracker
        candidates.extend(tuple(getattr(ServiceEventTracker, "InfoBarStack", ()) or ()))
    except Exception:
        pass
    seen = set()
    for owner in candidates:
        if owner is None or id(owner) in seen:
            continue
        seen.add(id(owner))
        if getattr(owner, "session", None) is session:
            yield owner


class _CachedSubtitleProxy(object):
    """Forward the subtitle interface while withholding its auto choice."""

    def __init__(self, interface, is_blocked):
        self._interface = interface
        self._is_blocked = is_blocked

    def getCachedSubtitle(self, *args, **kwargs):
        if self._is_blocked():
            return None
        return self._interface.getCachedSubtitle(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._interface, name)

    def __bool__(self):
        return bool(self._interface)

    __nonzero__ = __bool__


class _NativeOwnerHooks(object):
    def __init__(self, owner):
        self.owner = owner
        self.guards = []
        self._originals = {}
        self._wrappers = {}
        getter = getattr(owner, "getCurrentServiceSubtitle", None)
        enable = getattr(owner, "enableSubtitle", None)
        if not callable(getter) or not callable(enable):
            raise ValueError("native subtitle methods are unavailable")

        def guarded_getter(*args, **kwargs):
            interface = getter(*args, **kwargs)
            if interface is not None and self.is_blocked():
                return _CachedSubtitleProxy(interface, self.is_blocked)
            return interface

        def guarded_enable(*args, **kwargs):
            if self.is_blocked():
                return None
            return enable(*args, **kwargs)

        wrappers = {
            "getCurrentServiceSubtitle": guarded_getter,
            "enableSubtitle": guarded_enable,
        }
        attributes = getattr(owner, "__dict__", {})
        for name in wrappers:
            self._originals[name] = attributes.get(name, _MISSING)
        try:
            for name, wrapper in wrappers.items():
                setattr(owner, name, wrapper)
                self._wrappers[name] = wrapper
        except Exception:
            self.restore()
            raise

    def is_blocked(self):
        return any(guard.owns_current_service() for guard in tuple(self.guards))

    def add(self, guard):
        if guard not in self.guards:
            self.guards.append(guard)

    def remove(self, guard):
        if guard in self.guards:
            self.guards.remove(guard)
        if self.guards:
            return
        self.restore()
        if _OWNER_HOOKS.get(id(self.owner)) is self:
            del _OWNER_HOOKS[id(self.owner)]

    def restore(self):
        for name, wrapper in self._wrappers.items():
            # Another plugin may have wrapped/replaced us since installation.
            # Keep that method in place; retained wrappers become pass-through
            # as soon as this hook no longer has an active owner.
            if getattr(self.owner, name, None) is not wrapper:
                continue
            original = self._originals[name]
            try:
                if original is _MISSING:
                    delattr(self.owner, name)
                else:
                    setattr(self.owner, name, original)
            except Exception:
                pass


class NativeSubtitleAutoGuard(object):
    """Scope native automatic subtitle selection to one GT playback owner."""

    def __init__(self, session, is_active):
        self.session = session
        self._is_active = is_active
        self._claimed_keys = ()
        self._hooks = {}
        self._closed = False

    def _active(self):
        if self._closed or not callable(self._is_active):
            return False
        try:
            return bool(self._is_active())
        except Exception:
            return False

    def owns_current_service(self):
        if not self._active():
            return False
        key = _reference_key(_current_reference(self.session))
        return bool(key and key in self._claimed_keys)

    def _refresh_owners(self):
        owners = tuple(_native_owners(self.session))
        owner_ids = {id(owner) for owner in owners}
        for owner_id, hook in tuple(self._hooks.items()):
            if owner_id not in owner_ids:
                hook.remove(self)
                del self._hooks[owner_id]
        for owner in owners:
            owner_id = id(owner)
            if owner_id in self._hooks:
                continue
            hook = _OWNER_HOOKS.get(owner_id)
            if hook is None:
                try:
                    hook = _NativeOwnerHooks(owner)
                except Exception:
                    continue
                _OWNER_HOOKS[owner_id] = hook
            hook.add(self)
            self._hooks[owner_id] = hook

    def claim(self, reference):
        """Register a pending VOD service before its synchronous play events."""
        if not self._active():
            return False
        target = _reference_key(reference)
        if not target:
            return False
        current = _reference_key(_current_reference(self.session))
        # An engine switch can emit events for either its old or new decoder
        # before playService returns.  Retain only an already-owned current
        # service and this pending target, never an unrelated TV service.
        keys = [current] if current in self._claimed_keys and current != target else []
        keys.append(target)
        self._claimed_keys = tuple(keys)
        self._refresh_owners()
        return True

    def clear_native_selection(self):
        """Drop native bookkeeping only after the claimed decoder is current."""
        if not self.owns_current_service():
            return False
        self._refresh_owners()
        cleared = False
        for hook in tuple(self._hooks.values()):
            owner = hook.owner
            if not hasattr(owner, "selected_subtitle"):
                continue
            try:
                owner.selected_subtitle = None
                cleared = True
            except Exception:
                pass
        return cleared

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._claimed_keys = ()
        for hook in tuple(self._hooks.values()):
            hook.remove(self)
        self._hooks.clear()
