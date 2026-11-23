# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scroll a single focused text field inside its existing row bounds."""

from collections import namedtuple
from math import ceil
from time import monotonic

from .typography import _skin_attribute, _skin_font, _text_units, clean_dynamic_text

try:
    from enigma import eLabel, ePoint, eSize, eTimer, eWidget, gFont, gRGB
    from skin import parseColor
    from .front_panel_text import right_to_left
except (AttributeError, ImportError):
    eTimer = None


INITIAL_DELAY_MS = 1500
TICK_MS = 80
END_PAUSE_MS = 1200
MAX_TEXT_WIDTH = 16384


_Target = namedtuple('_Target', 'signature widget text box font foreground align paint show_full',
                     defaults=(None, None, None, 'left', None, None))


def _text(value):
    return clean_dynamic_text(value).encode('utf-8', 'replace').decode('utf-8')


def _displayed_prefix(widget, original):
    """Return the visible prefix length when the renderer added an ellipsis."""
    rendered = ' '.join(widget.getText().split())
    full = ' '.join(original.split())
    if rendered == full or not rendered.endswith(('…', '...')):
        return None
    prefix = rendered.rstrip('.… ')
    return len(prefix) if full.startswith(prefix) else None


def _controller(screen):
    if eTimer is None or getattr(screen, '_closed', False):
        return None
    existing = getattr(screen, '_gt_selected_row_scroll', None)
    if existing is not None:
        return existing
    try:
        return _SelectedRowScroll(screen)
    except Exception:
        # A receiver without the optional native rendering APIs keeps its label.
        return None


def clear_selection(screen):
    controller = getattr(screen, '_gt_selected_row_scroll', None)
    if controller is not None:
        controller.select(None)


def select_label(screen, name, full_text, *, key=None):
    """Pass the full value of the focused Label, before fitting or clipping."""
    widget = screen.get(name)
    value = _text(full_text)
    if widget is None or not value:
        clear_selection(screen)
        return
    controller = _controller(screen)
    if controller is not None:
        controller.select(_Target((name, id(widget), key, value), widget, value))


def _native_invalidator(widget):
    for owner, name in ((getattr(widget, 'l', None), 'invalidateEntry'),
                        (widget.instance, 'redrawItemByIndex'), (widget.instance, 'entryChanged')):
        invalidate = getattr(owner, name, None)
        if callable(invalidate):
            return invalidate
    return None


def redraw_native_row(widget, index):
    invalidate = _native_invalidator(widget)
    if invalidate is not None:
        invalidate(index)


def _selected_native_y(instance, index, row_height, height):
    entry_top = getattr(instance, 'getEntryTop', None)
    if callable(entry_top):
        try:
            y = entry_top()
            if 0 <= y < height:
                return y
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    top_index = getattr(instance, 'getTopIndex', None)
    if callable(top_index):
        try:
            top = top_index()
            y = (index - top) * row_height
            if top >= 0:
                return y if 0 <= y < height else None
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    # Older OpenPLi-derived vertical listboxes align the first row to a page.
    rows_per_page = height // row_height
    return index % rows_per_page * row_height if rows_per_page else None


def native_list_row(widget, index, box, row_height):
    """Locate the selected, visible text cell relative to its listbox."""
    try:
        instance = widget.instance
        if _native_invalidator(widget) is None or row_height <= 0:
            return None
        if index < 0 or widget.getSelectedIndex() != index:
            return None
        x, y, width, height = box
        size = instance.size()
        row_y = _selected_native_y(instance, index, row_height, size.height())
        if row_y is None:
            return None
        y += row_y
        if x >= 0 and y >= 0 and width > 1 and height > 1:
            if x + width <= size.width() and y + height <= size.height():
                return x, y, width, height
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    return None


def select_native(screen, widget, rectangle, full_text, font_tuple, foreground,
                  *, key, paint, align='left', show_full=None):
    """Pass the focused MultiContent cell and its normal-text paint callback."""
    value = _text(full_text)
    if not rectangle or not value or getattr(widget, 'instance', None) is None:
        clear_selection(screen)
        return
    controller = _controller(screen)
    if controller is not None:
        signature = (id(widget.instance), key, value, rectangle, font_tuple, foreground, align)
        controller.select(_Target(signature, widget, value, rectangle, font_tuple,
                                  foreground, align, paint, show_full))


def _native_color(color):
    if isinstance(color, str):
        return parseColor(color)
    if isinstance(color, int):
        return gRGB(color)
    return color


def _unwrapped_width(font, value, height, label=None):
    try:
        if label is None:
            size = eLabel.calculateTextSize(font, value, eSize(MAX_TEXT_WIDTH, height), True)
        else:
            label.setText(value)
            size = label.calculateSize()
        return max(0, size.width())
    except (AttributeError, RuntimeError, TypeError):
        return 0


class _SelectedRowScroll:
    def __init__(self, screen):
        self.closed = False
        self.visible = bool(getattr(screen, 'shown', False))
        self.target = None
        self.viewport = self.label = self.anchor = None
        self.suppressed = False
        self.fitting_title_shown = False
        self.probed_label = None
        self.deadline = 0.0
        self.timer = eTimer()
        try:
            self.connection = self.timer.timeout.connect(self._tick)
        except AttributeError:
            self.timer.callback.append(self._tick)
            self.connection = None
        screen.onClose.append(self.close)
        screen.onHide.append(self.hide)
        # Some older Screen variants offer onShown in place of onShow.
        shown = getattr(screen, 'onShow', None)
        (shown if shown is not None else screen.onShown).append(self.show)
        # A child dialog can take focus while leaving its parent displayed.
        exec_end = getattr(screen, 'onExecEnd', None)
        exec_begin = getattr(screen, 'onExecBegin', None)
        if exec_end is not None and exec_begin is not None:
            exec_end.append(self.hide)

            def resume_visible_screen():
                if getattr(screen, 'shown', False):
                    self.show()

            exec_begin.append(resume_visible_screen)
        screen._gt_selected_row_scroll = self

    def select(self, target):
        if self.closed:
            return
        if (target is not None and self.target is not None
                and target.signature == self.target.signature):
            if target.paint is not None:
                return
            if self.probed_label is not None and self._label_state(target) != self.probed_label:
                self._reset()
                self.probed_label = None
                if self.visible:
                    self._arm(INITIAL_DELAY_MS)
            elif self.suppressed:
                # A selected label may have been repainted by a data refresh.
                try:
                    self.anchor.setText('')
                except Exception:
                    self._reset()
            return
        self._reset()
        self.probed_label = None
        self.target = target
        if target is not None and self.visible:
            self._arm(INITIAL_DELAY_MS)

    def show(self):
        if self.closed or self.visible:
            return
        self.visible = True
        if self.target is not None:
            self._arm(INITIAL_DELAY_MS)

    def hide(self):
        self.visible = False
        self._reset()

    def close(self):
        self.closed = True
        self.hide()
        self.target = None
        self.connection = None
        self.timer = None

    def _arm(self, milliseconds):
        self.deadline = monotonic() + milliseconds / 1000.0
        try:
            self.timer.start(milliseconds, True)
        except Exception:
            self._reset()

    def _paint_original(self, hidden):
        target = self.target
        if target is None:
            return
        if target.paint is not None:
            target.paint(hidden)
        elif self.anchor is getattr(target.widget, 'instance', None):
            self.anchor.setText('' if hidden else target.widget.getText())

    def _reset(self):
        try:
            if self.timer is not None:
                self.timer.stop()
        except Exception:
            pass
        if self.suppressed:
            self.suppressed = False
            try:
                self._paint_original(False)
            except Exception:
                pass
        if self.fitting_title_shown:
            self.fitting_title_shown = False
            try:
                self.target.show_full(False)
            except Exception:
                pass
        if self.viewport is not None:
            try:
                self.viewport.hide()
            except Exception:
                pass
        # Release the child before releasing its clip widget.
        self.label = None
        self.viewport = None
        self.anchor = None

    def _geometry(self, target):
        if target.box is not None:
            return target.box, target.font, _native_color(target.foreground), target.align
        size = self.anchor.size()
        font = _skin_font(target.widget)
        current = getattr(target.widget, '_gt_typography_font_key', None)
        if current and current[0] == id(self.anchor):
            font = current[1:]
        try:
            color = self.anchor.getForegroundColor()
        except (AttributeError, RuntimeError, TypeError):
            color = parseColor(_skin_attribute(target.widget, 'foregroundColor') or '#FFFFFF')
        align = _skin_attribute(target.widget, 'halign') or 'left'
        return (0, 0, size.width(), size.height()), font, color, align

    @staticmethod
    def _label_state(target):
        widget = target.widget
        instance = getattr(widget, 'instance', None)
        try:
            size = instance.size()
            dimensions = (size.width(), size.height())
        except (AttributeError, RuntimeError, TypeError):
            dimensions = None
        getter = getattr(widget, 'getText', None)
        try:
            displayed = getter() if callable(getter) else None
        except (AttributeError, RuntimeError, TypeError):
            displayed = None
        return (id(instance), dimensions, bool(getattr(widget, 'visible', True)),
                getattr(widget, '_gt_typography_font_key', None), displayed)

    def _begin(self):
        target = self.target
        if target.paint is None:
            self.probed_label = self._label_state(target)
        self.anchor = getattr(target.widget, 'instance', None)
        if self.anchor is None or not getattr(target.widget, 'visible', True):
            return False
        box, font_pair, color, align = self._geometry(target)
        x, y, width, height = box
        family, size = font_pair
        if width <= 1 or height <= 1 or not family or size < 1:
            return False
        prefix = None
        if target.paint is None:
            rendered = target.widget.getText()
            if '\n' in rendered and ' '.join(rendered.split()) == ' '.join(target.text.split()):
                # Multi-line rows already showing the entire value need no movement.
                return False
            prefix = _displayed_prefix(target.widget, target.text)
        font = gFont(family, size)
        measured = _unwrapped_width(font, target.text, height)
        ellipsis_width = 0
        if measured and measured <= width and prefix is None:
            if target.show_full is None:
                return False
            # OpenPLi's listbox tests the title with an extra ellipsis before
            # drawing it, even when the title itself fits in the cell.
            ellipsis_width = _unwrapped_width(font, target.text + '…', height)
            if 0 < ellipsis_width <= width:
                return False

        self.viewport = viewport = eWidget(self.anchor)
        viewport.hide()
        viewport.setTransparent(1)
        viewport.move(ePoint(x, y))
        viewport.resize(eSize(width, height))
        self.label = label = eLabel(viewport)
        label.setTransparent(1)
        label.setNoWrap(1)
        label.setFont(font)
        label.setForegroundColor(color)
        label.setVAlign(eLabel.alignCenter)
        label.setText(target.text)
        if not measured or measured <= width:
            label.resize(eSize(MAX_TEXT_WIDTH, height))
            measured = _unwrapped_width(font, target.text, height, label) or measured
        if prefix is not None and (not measured or measured <= width):
            # Use the renderer's visible ellipsis as evidence even if the
            # image reports a clipped or incomplete measurement for the title.
            missing = ' '.join(target.text.split())[prefix:]
            measured = min(MAX_TEXT_WIDTH - 1, width + max(1, ceil(_text_units(missing) * size * 0.55)))
        if 0 < measured <= width and target.show_full is not None:
            if not ellipsis_width:
                ellipsis_width = _unwrapped_width(font, target.text + '…', height, label)
            if ellipsis_width > width:
                self._reset()
                self.fitting_title_shown = True
                target.show_full(True)
            return False
        if not width < measured < MAX_TEXT_WIDTH:
            return False

        self.rtl = right_to_left(target.text) or align == 'right'
        label.setHAlign(eLabel.alignRight if self.rtl else eLabel.alignLeft)
        self.overflow = measured - width
        self.speed = max(24, min(85, size * 1.3))
        self.last_offset = None
        label.resize(eSize(measured, height))
        self.started = monotonic() - TICK_MS / 1000.0
        self.suppressed = True
        self._paint_original(True)
        label.show()
        viewport.show()
        return True

    def _tick(self):
        if (self.closed or not self.visible or self.target is None or self.timer is None
                or self.fitting_title_shown):
            return
        try:
            remaining = self.deadline - monotonic()
            if remaining > 0:
                # A queued old tick must not shorten a new selection's dwell.
                self.timer.start(max(1, int(remaining * 1000) + 1), True)
                return
            if self.label is not None:
                if self.anchor is not self.target.widget.instance or not getattr(self.target.widget, 'visible', True):
                    self._reset()
                    return
            elif not self._begin():
                if not self.fitting_title_shown:
                    self._reset()
                return
            restarting = self.started is None
            distance = 0 if restarting else min(self.overflow, round((monotonic() - self.started) * self.speed))
            offset = distance - self.overflow if self.rtl else -distance
            if offset != self.last_offset:
                self.label.move(ePoint(offset, 0))
                self.last_offset = offset
            if restarting:
                self.started = monotonic() + INITIAL_DELAY_MS / 1000.0 - TICK_MS / 1000.0
                self._arm(INITIAL_DELAY_MS)
            elif distance < self.overflow:
                self._arm(TICK_MS)
            else:
                self.started = None
                self._arm(END_PAUSE_MS)
        except Exception:
            self._reset()
