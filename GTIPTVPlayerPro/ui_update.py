# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Idempotent native-widget updates; no provider, timer or persistence state."""


def set_visible(component, visible):
    visible = bool(visible)
    # GUIComponent.visible is the authoritative state, including changes made
    # by image callbacks and controllers outside the presentation layer.
    current = getattr(component, 'visible', None)
    if isinstance(current, bool) and current == visible:
        return
    (component.show if visible else component.hide)()


def set_geometry(component, box):
    instance = getattr(component, 'instance', None)
    if instance is None:
        return
    x, y, width, height = map(int, box)
    from enigma import ePoint, eSize
    # Inspect each native value separately; moving a scrollbar does not resize
    # it. Missing getters on older images retain the corresponding update.
    try:
        position = instance.position()
        same_position = (position.x(), position.y()) == (x, y)
    except (AttributeError, TypeError, RuntimeError):
        same_position = False
    try:
        size = instance.size()
        same_size = (size.width(), size.height()) == (width, height)
    except (AttributeError, TypeError, RuntimeError):
        same_size = False
    if not same_position:
        instance.move(ePoint(x, y))
    if not same_size:
        instance.resize(eSize(width, height))


def set_size(component, width, height):
    instance = getattr(component, 'instance', None)
    if instance is None:
        return
    size = instance.size()
    if (size.width(), size.height()) != (width, height):
        from enigma import eSize
        instance.resize(eSize(width, height))


def set_value(component, value):
    key = (id(getattr(component, 'instance', None)), value)
    if getattr(component, '_gt_ui_value', None) != key:
        component.setValue(value)
        component._gt_ui_value = key


def set_pixmap_num(component, value):
    key = (id(getattr(component, 'instance', None)), value)
    if getattr(component, '_gt_ui_pixmap_num', None) != key:
        component.setPixmapNum(value)
        component._gt_ui_pixmap_num = key


def set_foreground(component, color):
    instance = getattr(component, 'instance', None)
    if instance is None:
        return
    key = (id(instance), color)
    if getattr(component, '_gt_ui_foreground', None) != key:
        from skin import parseColor
        instance.setForegroundColor(parseColor(color))
        component._gt_ui_foreground = key


def card_content_changed(screen, slot, item):
    from .i18n import device_language
    from .typography import current_text_size
    signature = (id(item), tuple(getattr(item, name, '') for name in
        ('stream_id', 'name', 'content_type', 'year', 'season', 'episode',
         'resume_position', 'resume_length', 'icon')), device_language(),
        current_text_size(), bool(getattr(screen, '_move_active', False)),
        id(getattr(screen['continue_title_'+str(slot)], 'instance', None)))
    rows = getattr(screen, '_r27_card_content', None)
    if rows is None:
        rows = screen._r27_card_content = {}
    if rows.get(slot) == signature:
        return False
    rows[slot] = signature
    return True
