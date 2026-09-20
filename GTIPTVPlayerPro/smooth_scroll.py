# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

"""Timer-driven synopsis text with receiver-safe repaint behaviour.

Most receivers can move Enigma2's native ``ScrollLabel`` child one pixel at a
time.  The SF8008 framebuffer driver visibly shakes that moving widget even at
the reduced R118 cadence.  On that receiver we therefore keep the widget
fixed and redraw the text at successive offsets on Enigma2's software canvas.
"""

from enigma import eTimer

try:
    from enigma import (
        RT_HALIGN_BLOCK,
        RT_HALIGN_CENTER,
        RT_HALIGN_LEFT,
        RT_HALIGN_RIGHT,
        RT_VALIGN_BOTTOM,
        RT_VALIGN_CENTER,
        RT_VALIGN_TOP,
        RT_WRAP,
        eCanvas,
        eLabel,
        ePoint,
        eRect,
        eSize,
        gFont,
        gRGB,
    )
    from Components.GUIComponent import GUIComponent
    from skin import parseColor, parseFont
    CANVAS_SCROLL_AVAILABLE = True
except (AttributeError, ImportError):
    # Very small compatibility images and offline tests may not expose the
    # canvas surface.  They retain the established native component instead
    # of making the Movies or Series screen fail to open.
    RT_HALIGN_BLOCK = 0
    RT_HALIGN_CENTER = 0
    RT_HALIGN_LEFT = 0
    RT_HALIGN_RIGHT = 0
    RT_VALIGN_BOTTOM = 0
    RT_VALIGN_CENTER = 0
    RT_VALIGN_TOP = 0
    RT_WRAP = 0
    eCanvas = None
    eLabel = None
    ePoint = None
    eRect = None
    eSize = None
    gFont = None
    gRGB = None
    GUIComponent = object
    parseColor = None
    parseFont = None
    CANVAS_SCROLL_AVAILABLE = False

try:
    from Components.ScrollLabel import ScrollLabel
except ImportError:  # Minimal test images may omit the standard component.
    from Components.Label import Label as ScrollLabel


SMOOTH_SCROLL_INITIAL_DELAY_MS = 2000
SMOOTH_SCROLL_TICK_MS = 80
SMOOTH_SCROLL_END_PAUSE_MS = 1600
SMOOTH_SCROLL_STEP_PX = 1
DRIVER_SAFE_SCROLL_TICK_MS = 200
DRIVER_SAFE_SCROLL_END_PAUSE_MS = 3200
DRIVER_SAFE_MODEL_MARKERS = ("sf8008",)
RECEIVER_IDENTITY_PATHS = (
    "/proc/stb/info/model",
    "/proc/stb/info/boxtype",
    "/proc/stb/info/vumodel",
)


def _normalise_receiver_identity(value):
    return "".join(
        character
        for character in str(value or "").lower()
        if character.isalnum()
    )


def _receiver_identity(paths=RECEIVER_IDENTITY_PATHS):
    values = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                value = handle.read(128).strip()
        except (IOError, OSError, TypeError, ValueError):
            continue
        if value:
            values.append(value)
    return " ".join(values)


def _requires_driver_safe_scroll(identity=None):
    if identity is None:
        identity = _receiver_identity()
    normalised = _normalise_receiver_identity(identity)
    return any(marker in normalised for marker in DRIVER_SAFE_MODEL_MARKERS)


DRIVER_SAFE_SCROLL = _requires_driver_safe_scroll()


def _connect_timer(timer, callback):
    try:
        timer.timeout.connect(callback)
    except AttributeError:
        timer.callback.append(callback)


class GTNativeSmoothScrollLabel(ScrollLabel):
    """A clipped ScrollLabel which continuously advances one pixel at a time."""

    def __init__(self, text=""):
        ScrollLabel.__init__(self, text)
        self._gt_scroll_timer = eTimer()
        self._gt_scroll_phase = "idle"
        self._gt_rendered_text = None
        self._gt_rendered_ready = False
        self._gt_driver_safe_scroll = bool(DRIVER_SAFE_SCROLL)
        _connect_timer(self._gt_scroll_timer, self._gt_scroll_tick)

    def _gt_layout_ready(self):
        try:
            if hasattr(self, "long_text") and self.long_text is None:
                return False
            return int(getattr(self, "pageHeight", 0)) > 0
        except (TypeError, ValueError, OverflowError):
            return False

    def _gt_total_height(self):
        for name in ("totalTextHeight", "TotalTextHeight"):
            try:
                return max(0, int(getattr(self, name)))
            except (AttributeError, TypeError, ValueError, OverflowError):
                pass
        return 0

    def _gt_current_position(self):
        for name in ("currentPosition", "curPos"):
            try:
                return max(0, int(getattr(self, name)))
            except (AttributeError, TypeError, ValueError, OverflowError):
                pass
        return 0

    def _gt_last_position(self):
        try:
            page_height = max(0, int(getattr(self, "pageHeight", 0)))
        except (TypeError, ValueError, OverflowError):
            page_height = 0
        return max(0, self._gt_total_height() - page_height)

    def _gt_start_timer(self, delay_ms):
        try:
            self._gt_scroll_timer.start(max(1, int(delay_ms)), True)
        except Exception:
            self._gt_scroll_phase = "idle"

    def _gt_set_position(self, position):
        target = max(0, int(position))
        if target == self._gt_current_position():
            return True
        setter = getattr(self, "setPos", None)
        if callable(setter):
            try:
                setter(target)
                return True
            except Exception:
                pass
        return False

    def restart_scroll(self):
        """Return to the first pixel and re-arm only when text overflows."""
        self.stop_scroll()
        self._gt_set_position(0)
        if self._gt_last_position() <= 0:
            return
        self._gt_scroll_phase = "scroll"
        self._gt_start_timer(SMOOTH_SCROLL_INITIAL_DELAY_MS)

    def stop_scroll(self):
        try:
            self._gt_scroll_timer.stop()
        except Exception:
            pass
        self._gt_scroll_phase = "idle"

    def _gt_scroll_tick(self):
        last_position = self._gt_last_position()
        if last_position <= 0:
            self.stop_scroll()
            self._gt_set_position(0)
            return
        if self._gt_scroll_phase == "rewind":
            self._gt_set_position(0)
            self._gt_scroll_phase = "scroll"
            self._gt_start_timer(SMOOTH_SCROLL_INITIAL_DELAY_MS)
            return
        if self._gt_driver_safe_scroll:
            # SF8008-family drivers need fewer native position updates, but
            # retaining the one-pixel step avoids the visible line jumps used
            # by the first safety profile.
            step = SMOOTH_SCROLL_STEP_PX
            tick_ms = DRIVER_SAFE_SCROLL_TICK_MS
            end_pause_ms = DRIVER_SAFE_SCROLL_END_PAUSE_MS
        else:
            step = SMOOTH_SCROLL_STEP_PX
            tick_ms = SMOOTH_SCROLL_TICK_MS
            end_pause_ms = SMOOTH_SCROLL_END_PAUSE_MS
        next_position = min(last_position, self._gt_current_position() + step)
        self._gt_set_position(next_position)
        if next_position >= last_position:
            self._gt_scroll_phase = "rewind"
            self._gt_start_timer(end_pause_ms)
        else:
            self._gt_scroll_phase = "scroll"
            self._gt_start_timer(tick_ms)

    def setText(self, text, *args, **kwargs):
        rendered = "" if text is None else str(text)
        if (
            self._gt_rendered_ready
            and self._gt_rendered_text == rendered
        ):
            return None
        result = ScrollLabel.setText(self, text, *args, **kwargs)
        self._gt_rendered_text = rendered
        self._gt_rendered_ready = self._gt_layout_ready()
        if hasattr(self, "_gt_scroll_timer"):
            self.restart_scroll()
        return result

    def applySkin(self, desktop, parent):
        result = ScrollLabel.applySkin(self, desktop, parent)
        # OpenPLi/OE-A call our setText override from ScrollLabel.applySkin.
        # Retain a fallback for small compatibility implementations that do not.
        if not self._gt_rendered_ready:
            self._gt_rendered_text = None
            self.setText(getattr(self, "message", ""))
        return result

    def GUIdelete(self):
        self.stop_scroll()
        self._gt_rendered_ready = False
        return ScrollLabel.GUIdelete(self)


def _skin_pair(value, fallback=(0, 0)):
    try:
        left, right = str(value).split(",", 1)
        return max(0, int(left)), max(0, int(right))
    except (TypeError, ValueError, OverflowError):
        return fallback


def _skin_truth(value):
    return str(value or "").strip().lower() in (
        "1",
        "enabled",
        "on",
        "true",
        "yes",
    )


class GTSF8008CanvasScrollLabel(GUIComponent):
    """Fixed software canvas used only by the SF8008 synopsis surface.

    The canvas itself never moves.  Each timer tick clears its private 32-bit
    pixmap and renders the same wrapped text one pixel higher.  This avoids the
    native child-widget movement which triggers the SF8008 driver tremor while
    retaining the approved pixel-by-pixel visual motion.
    """

    GUI_WIDGET = eCanvas

    def __init__(self, text=""):
        GUIComponent.__init__(self)
        self.message = "" if text is None else str(text)
        self._gt_rendered_text = None
        self._gt_rendered_ready = False
        self._gt_scroll_phase = "idle"
        self._gt_scroll_timer = eTimer()
        self._gt_measure_label = None
        self._gt_width = 0
        self._gt_height = 0
        self._gt_font = gFont("Regular", 20)
        self._gt_foreground = gRGB(0x00FFFFFF)
        self._gt_background = gRGB(0xFF000000)
        self._gt_transparent = True
        self._gt_text_flags = RT_WRAP | RT_HALIGN_LEFT | RT_VALIGN_TOP
        self.currentPosition = 0
        self.curPos = 0
        self.totalTextHeight = 0
        self.TotalTextHeight = 0
        self.pageHeight = 0
        _connect_timer(self._gt_scroll_timer, self._gt_scroll_tick)

    def _gt_read_skin(self, parent):
        canvas_attributes = []
        halign = {
            "left": RT_HALIGN_LEFT,
            "center": RT_HALIGN_CENTER,
            "right": RT_HALIGN_RIGHT,
            "block": RT_HALIGN_BLOCK,
        }
        valign = {
            "top": RT_VALIGN_TOP,
            "center": RT_VALIGN_CENTER,
            "bottom": RT_VALIGN_BOTTOM,
        }
        horizontal_flag = RT_HALIGN_LEFT
        vertical_flag = RT_VALIGN_TOP
        wrap = True
        for attribute, value in list(self.skinAttributes or []):
            if attribute == "size":
                self._gt_width, self._gt_height = _skin_pair(
                    value,
                    (self._gt_width, self._gt_height),
                )
                canvas_attributes.append((attribute, value))
            elif attribute == "font":
                try:
                    self._gt_font = parseFont(value, parent.scale)
                except Exception:
                    pass
            elif attribute == "foregroundColor":
                try:
                    self._gt_foreground = parseColor(value)
                except Exception:
                    pass
            elif attribute == "backgroundColor":
                try:
                    self._gt_background = parseColor(value)
                except Exception:
                    pass
            elif attribute == "transparent":
                self._gt_transparent = _skin_truth(value)
                canvas_attributes.append((attribute, value))
            elif attribute == "halign":
                horizontal_flag = halign.get(str(value).lower(), horizontal_flag)
            elif attribute == "valign":
                vertical_flag = valign.get(str(value).lower(), vertical_flag)
            elif attribute == "noWrap":
                wrap = not _skin_truth(value)
            elif attribute in (
                "scrollbarMode",
                "scrollbarWidth",
                "scrollbarMargin",
                "scrollbarSliderBorderWidth",
                "scrollbarSliderForegroundColor",
                "scrollbarSliderBorderColor",
                "scrollbarSliderPicture",
                "scrollbarBackgroundPicture",
                "sliderPixmap",
                "scrollbarbackgroundPixmap",
            ):
                continue
            else:
                canvas_attributes.append((attribute, value))
        self._gt_text_flags = horizontal_flag | vertical_flag
        if wrap:
            self._gt_text_flags |= RT_WRAP
        if self._gt_transparent:
            # Enigma2 gRGB alpha is inverse (0xff is fully transparent).
            self._gt_background = gRGB(0xFF000000)
        self.skinAttributes = canvas_attributes

    def _gt_canvas_size(self):
        width = self._gt_width
        height = self._gt_height
        try:
            size = self.instance.size()
            width = int(size.width()) or width
            height = int(size.height()) or height
        except (AttributeError, TypeError, ValueError, OverflowError):
            pass
        return max(0, width), max(0, height)

    def _gt_prepare_surface(self):
        if self.instance is None:
            return False
        width, height = self._gt_canvas_size()
        if width <= 0 or height <= 0:
            return False
        self._gt_width = width
        self._gt_height = height
        self.pageHeight = height
        try:
            self.instance.setSize(eSize(width, height))
            # Blend the transparent canvas over backdrop artwork.  eCanvas
            # itself remains at the exact same screen position for every tick.
            self.instance.setAlphatest(2 if self._gt_transparent else 0)
        except Exception:
            return False
        return True

    def _gt_prepare_measure_label(self):
        label = self._gt_measure_label
        if label is None:
            return False
        try:
            label.setFont(self._gt_font)
            label.setNoWrap(0 if self._gt_text_flags & RT_WRAP else 1)
            label.setVAlign(eLabel.alignTop)
            if self._gt_text_flags & RT_HALIGN_RIGHT:
                label.setHAlign(eLabel.alignRight)
            elif self._gt_text_flags & RT_HALIGN_CENTER:
                label.setHAlign(eLabel.alignCenter)
            elif self._gt_text_flags & RT_HALIGN_BLOCK:
                label.setHAlign(eLabel.alignBlock)
            else:
                label.setHAlign(eLabel.alignLeft)
            label.move(ePoint(self._gt_width, self._gt_height))
            label.resize(eSize(self._gt_width, self._gt_height))
            return True
        except Exception:
            return False

    def _gt_measure(self):
        total_height = 0
        if self._gt_prepare_measure_label():
            try:
                self._gt_measure_label.setText(self.message.rstrip())
                total_height = int(
                    self._gt_measure_label.calculateSize().height()
                )
            except (AttributeError, TypeError, ValueError, OverflowError):
                total_height = 0
        total_height = max(0, total_height)
        self.totalTextHeight = total_height
        self.TotalTextHeight = total_height
        return total_height

    def _gt_last_position(self):
        return max(0, self.TotalTextHeight - self.pageHeight)

    def _gt_start_timer(self, delay_ms):
        try:
            self._gt_scroll_timer.start(max(1, int(delay_ms)), True)
        except Exception:
            self._gt_scroll_phase = "idle"

    def _gt_draw(self):
        if self.instance is None or not self._gt_rendered_ready:
            return False
        try:
            self.instance.clear(self._gt_background)
            if self.message:
                draw_height = max(self._gt_height, self.TotalTextHeight)
                self.instance.writeText(
                    eRect(
                        0,
                        -self.currentPosition,
                        self._gt_width,
                        draw_height,
                    ),
                    self._gt_foreground,
                    self._gt_background,
                    self._gt_font,
                    self.message,
                    self._gt_text_flags,
                )
            return True
        except Exception:
            return False

    def _gt_set_position(self, position, force=False):
        target = max(0, min(int(position), self._gt_last_position()))
        changed = target != self.currentPosition
        self.currentPosition = target
        self.curPos = target
        if force or changed:
            self._gt_draw()
        return True

    def restart_scroll(self):
        self.stop_scroll()
        self._gt_measure()
        self._gt_set_position(0, force=True)
        if self._gt_last_position() <= 0:
            return
        self._gt_scroll_phase = "scroll"
        self._gt_start_timer(SMOOTH_SCROLL_INITIAL_DELAY_MS)

    def stop_scroll(self):
        try:
            self._gt_scroll_timer.stop()
        except Exception:
            pass
        self._gt_scroll_phase = "idle"

    def _gt_scroll_tick(self):
        last_position = self._gt_last_position()
        if last_position <= 0:
            self.stop_scroll()
            self._gt_set_position(0, force=True)
            return
        if self._gt_scroll_phase == "rewind":
            self._gt_set_position(0, force=True)
            self._gt_scroll_phase = "scroll"
            self._gt_start_timer(SMOOTH_SCROLL_INITIAL_DELAY_MS)
            return
        next_position = min(
            last_position,
            self.currentPosition + SMOOTH_SCROLL_STEP_PX,
        )
        self._gt_set_position(next_position)
        if next_position >= last_position:
            self._gt_scroll_phase = "rewind"
            self._gt_start_timer(DRIVER_SAFE_SCROLL_END_PAUSE_MS)
        else:
            self._gt_scroll_phase = "scroll"
            self._gt_start_timer(DRIVER_SAFE_SCROLL_TICK_MS)

    def setText(self, text, *unused_args, **unused_kwargs):
        rendered = "" if text is None else str(text)
        if self._gt_rendered_ready and self._gt_rendered_text == rendered:
            return None
        self.message = rendered
        self._gt_rendered_text = rendered
        if self.instance is not None:
            self._gt_rendered_ready = self._gt_prepare_surface()
            if self._gt_rendered_ready:
                self.restart_scroll()
        return None

    def getText(self):
        return self.message

    def applySkin(self, desktop, parent):
        self._gt_read_skin(parent)
        result = GUIComponent.applySkin(self, desktop, parent)
        self._gt_rendered_ready = self._gt_prepare_surface()
        if self._gt_rendered_ready:
            self._gt_rendered_text = self.message
            self.restart_scroll()
        return result

    def postWidgetCreate(self, instance):
        # Screen skinning has already attached skinAttributes at this point.
        for attribute, value in list(self.skinAttributes or []):
            if attribute == "size":
                self._gt_width, self._gt_height = _skin_pair(value)
                break
        if self._gt_width > 0 and self._gt_height > 0:
            try:
                instance.setSize(eSize(self._gt_width, self._gt_height))
            except Exception:
                pass
        try:
            self._gt_measure_label = eLabel(instance)
        except Exception:
            self._gt_measure_label = None

    def GUIdelete(self):
        self.stop_scroll()
        self._gt_rendered_ready = False
        self._gt_measure_label = None
        return GUIComponent.GUIdelete(self)


def GTSmoothScrollLabel(text=""):
    """Return the receiver-specific synopsis component.

    SF8008 uses the fixed software canvas.  Every other receiver keeps the
    R109 native one-pixel ScrollLabel path and its original 80 ms cadence.
    """
    if DRIVER_SAFE_SCROLL and CANVAS_SCROLL_AVAILABLE:
        return GTSF8008CanvasScrollLabel(text)
    return GTNativeSmoothScrollLabel(text)
