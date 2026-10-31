# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""live; loaded when its screen is needed."""

import datetime
import math
import re
import threading
import time
import uuid
from collections import OrderedDict, deque
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
try:
    from Components.VideoWindow import VideoWindow
except ImportError:
    VideoWindow = None
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from enigma import ePoint, eSize, eTimer, getDesktop, iServiceInformation
try:
    from enigma import iPlayableService as _PlayableServiceEvents
except ImportError:
    _PlayableServiceEvents = None
from .auxiliary_presentation import skin_after as aux_skin_after, refresh_after as aux_refresh_after, setup as setup_aux
from .broadcast_presentation import skin_after as broadcast_skin_after, refresh_after as broadcast_refresh_after, setup as setup_broadcast
from . import sorting_ui as content_sort_ui
from .artwork_file import ArtworkPath, artwork_path
from .content import ContentCategory, ContentItem, CONTENT_LABELS, ContentError, XtreamContentClient
from .diagnostics import log_event, redact
from .i18n import N_, _, ngettext, localized_date_text, localized_short_weekday, localized_language_name, localized_upper, metadata_language
from .live_recovery import media_snapshot as _decoder_media_snapshot
from .live_history import LiveChannelListContext, clear_live_channel_history, current_live_channel_context, live_channel_key, previous_live_channel_selection, remember_live_channel
from .live_channel_order import ChannelMove, LiveChannelOrderStore, arrange_channels, channel_order_scope
from .background import attach_background, attach_background_window, attach_native_alpha_pixmap, attach_pixmap
from .channel_highlight import DEFAULT_CHANNEL_HIGHLIGHT, channel_highlight_art, channel_highlight_colors, normalize_channel_highlight
from .category_filters import category_filter_ids
from .category_preferences import MAX_CATEGORIES_PER_SOURCE, CategoryPreferences, LiveCategoryPreferenceStore, arrange_categories, category_preferences_path
from .category_visibility import category_filter_active, category_visibility_ids, filter_hidden_category_items, hidden_category_match_ids
from .live_programme_art_ui import LiveProgrammePosterMixin
from .broadcast_layout import live_geometry as live_guide_side_geometry
from .playback import PlaybackResumeStore, resume_account_scope, resume_key, should_save_resume
from .paths import plugin_path
from .remote_footer import web_shortcut_hint_text, decorate_remote_footer, prepared_skin, footer_item, install_remote_footer, set_remote_footer
from .scrollbar import hide_scrollbar, update_horizontal_scrollbar, update_scrollbar
from .settings import DEFAULT_INFOBAR_TIMEOUT, available_service_types, load_player_settings, normalize_infobar_timeout, serviceapp_uses_async_seek, service_engine_label
from .typography import _grapheme_boundaries, _line_fits, _text_units, clean_dynamic_text, current_text_size, ellipsize_dynamic_text, fit_dynamic_text, font_px, scale_skin_fonts, set_static_text, set_text_if_changed, visible_count
from .download_guard import allow as _allow_account_playback
from .download_guard import cancel_pending as _cancel_download_pending
from .browser_common import CATEGORY_MANAGER_FOOTER_ITEMS, CATEGORY_MANAGER_MOVE_FOOTER_ITEMS, FAVORITE_STAR_PATH, GTAsyncListScreen, LIVE_CHANNEL_MOVE_FOOTER_ITEMS, LIVE_CHANNEL_ORDER_FOOTER_ITEMS, LIVE_DETAIL_CACHE_TTL_SECONDS, LIVE_DETAIL_EMPTY_RETRY_SECONDS, LIVE_DETAIL_EPG_RETRY_LIMIT, LIVE_DETAIL_EPG_TIMEOUT_SECONDS, LIVE_DETAIL_PROGRESS_INTERVAL_MS, LIVE_EPG_CACHE_MAX_ENTRIES, LIVE_EPG_CACHE_TTL_SECONDS, LIVE_EPG_FETCH_LIMIT, LIVE_FULLSCREEN_RESULT_KIND, LIVE_GUIDE_BACKGROUND_PATH, LIVE_PAGE_EPG_DWELL_MS, LIVE_PAGE_EPG_POLL_MS, LIVE_PAGE_EPG_REQUEST_PAUSE_MS, LIVE_PICON_DOWNLOAD_TIMEOUT, LIVE_PICON_PREFETCH_POLL_MS, LIVE_PICON_PREFETCH_WORKERS, LIVE_PREVIEW_FRAME_PATH, LIVE_PREVIEW_LINK_POLL_MS, LIVE_PREVIEW_VIDEO_ERROR_CHECKS, LIVE_PREVIEW_VIDEO_INITIAL_MS, LIVE_PREVIEW_VIDEO_MAX_CHECKS, LIVE_PREVIEW_VIDEO_RETRY_MS, LIVE_STREAM_FOOTER_ITEMS, LIVE_STREAM_PAGE_SIZE, M3U_SEARCH_FOOTER_ITEMS, M3U_SEARCH_RESULT_LIMIT, STALKER_LIVE_EPG_QUIET_MS, STALKER_LIVE_PAGE_EPG_DWELL_MS, STALKER_LIVE_PICON_QUIET_MS, WORKER_LIST_TIMEOUT_SECONDS, WORKER_PLAYBACK_LINK_TIMEOUT_SECONDS, _FavoriteSupport, _WorkerCancelled, _WorkerToken, _bounded_client_call, _cached_picon_path, _cancel_worker_token, _client_epg_state, _client_supports, _connect_timer, _debug, _debug_identifier, _desktop_size, _disable_live_window_animation, _epg_event_starts_in_future, _is_m3u_client, _is_stalker_client, _live_detail_dwell_ms, _load_live_picon_path, _load_shared_live_epg, _mark_picon_failure, _picon_failure_active, _picon_failure_remaining, _read_proc_number, _safe_picon_url, _scale, _stream_diag_pts_progress, _trace_fullscreen_osd, _valid_live_picon_file, _validated_content_type, _wrap_live_summary, content_client_for

@broadcast_skin_after('categories')
def _live_category_skin():
    width, height, px = _scale()
    row_font = font_px(px, {'standard': 28, 'large': 31, 'very_large': 33}[current_text_size()], value='standard', max_height=76, vertical_padding=8)
    theme_dir = globals().get('APP_BACKGROUND_PATH', '/usr/lib/enigma2/python/Plugins/Extensions/GTIPTVPlayerPro/skin/images/global-neon-v0912.png').rsplit('/', 1)[0]
    margin = px(50)
    footer_y = height - px(92)
    left_x = margin
    left_w = px(945)
    right_x = left_x + left_w + px(22)
    right_w = width - right_x - margin
    panel_y = px(60)
    panel_h = px(875)
    row_y = tuple((145 + index * 91 for index in range(8)))
    rows = []
    for index, y_value in enumerate(row_y):
        rows.append('\n    <widget name="category_row_art_{index}" position="{focus_x},{focus_y}"\n            size="{focus_w},{focus_box_h}" pixmap="{row_art}"\n            alphatest="blend" scale="1" zPosition="2" />\n    <widget name="category_focus_art_{index}" position="{focus_x},{focus_y}"\n            size="{focus_w},{focus_box_h}" pixmap="{focus_art}"\n            alphatest="blend" scale="1" zPosition="4" />\n    <widget name="category_icon_{index}" position="{icon_x},{icon_y}"\n            size="{icon_w},{icon_h}" pixmap="{category_icon}"\n            alphatest="blend" scale="1" zPosition="8" />\n    <widget name="category_row_{index}" position="{row_x},{row_y}"\n            size="{row_w},{row_h}" font="Regular;{row_font}"\n            foregroundColor="#F8FAFC" transparent="1" zPosition="6"\n            valign="center" halign="left" noWrap="1" />\n    <widget name="category_focus_{index}_fill" position="{focus_x},{focus_y}"\n            size="{focus_w},{focus_box_h}" font="Regular;1"\n            backgroundColor="#172A45" transparent="0" zPosition="3" />\n    <widget name="category_focus_{index}_top" position="{focus_x},{focus_y}"\n            size="{focus_w},{focus_h}" font="Regular;1"\n            backgroundColor="#00E5FF" transparent="0" zPosition="7" />\n    <widget name="category_focus_{index}_bottom" position="{focus_x},{focus_bottom_y}"\n            size="{focus_w},{focus_h}" font="Regular;1"\n            backgroundColor="#D946EF" transparent="0" zPosition="7" />\n    <widget name="category_focus_{index}_left" position="{focus_x},{focus_y}"\n            size="{focus_h},{focus_box_h}" font="Regular;1"\n            backgroundColor="#00E5FF" transparent="0" zPosition="7" />\n    <widget name="category_focus_{index}_right" position="{focus_right_x},{focus_y}"\n            size="{focus_h},{focus_box_h}" font="Regular;1"\n            backgroundColor="#D946EF" transparent="0" zPosition="7" />'.format(index=index, row_art=theme_dir + '/category-glass-row-r84.png', focus_art=theme_dir + '/focus-r64.png', category_icon=theme_dir + '/category-icon-r64.png', icon_x=left_x + px(34), icon_y=px(y_value + 7), icon_w=px(62), icon_h=px(62), row_x=left_x + px(113), row_y=px(y_value), row_w=left_w - px(162), row_h=px(76), row_font=row_font, focus_x=left_x + px(20), focus_y=px(y_value - 3), focus_w=left_w - px(40), focus_h=max(1, px(3)), focus_bottom_y=px(y_value + 77), focus_box_h=px(80), focus_right_x=left_x + left_w - px(23)))
    weather_x = right_x + px(29)
    weather_w = right_w - px(58)
    day_gap = px(8)
    day_w = int((weather_w - day_gap * 4) / 5)
    weather_days = []
    for index in range(5):
        day_x = weather_x + index * (day_w + day_gap)
        if index:
            weather_days.append('<widget name="weather_day_separator_{}" position="{},{}" size="{},{}" font="Regular;1" backgroundColor="#123B75" transparent="0" zPosition="3" />'.format(index, day_x - px(5), px(586), max(1, px(2)), px(242)))
        weather_days.append('\n    <widget name="weather_day_card_{index}" position="{day_x},{day_y}"\n            size="{day_w},{day_h}" font="Regular;1"\n            transparent="1" zPosition="2" />\n    <widget name="weather_day_{index}" position="{day_x},{day_label_y}"\n            size="{day_w},{day_label_h}" font="Regular;{day_label_font}"\n            foregroundColor="#E2E8F0" transparent="1" zPosition="4"\n            valign="center" halign="center" />\n    <widget name="weather_day_icon_{index}" position="{day_icon_x},{day_icon_y}"\n            size="{day_icon_w},{day_icon_h}" alphatest="blend" scale="1"\n            zPosition="4" />\n    <widget name="weather_day_high_{index}" position="{day_x},{day_high_y}"\n            size="{day_w},{day_high_h}" font="Regular;{day_high_font}"\n            foregroundColor="#FFFFFF" transparent="1" zPosition="4"\n            valign="center" halign="center" />\n    <widget name="weather_day_low_{index}" position="{day_x},{day_low_y}"\n            size="{day_w},{day_low_h}" font="Regular;{day_low_font}"\n            foregroundColor="#2FB7FF" transparent="1" zPosition="4"\n            valign="center" halign="center" />'.format(index=index, day_x=day_x, day_y=px(570), day_w=day_w, day_h=px(265), day_label_y=px(570), day_label_h=px(42), day_label_font=font_px(px, 21, max_height=42, vertical_padding=3), day_icon_x=day_x + int((day_w - px(92)) / 2), day_icon_y=px(632), day_icon_w=px(92), day_icon_h=px(92), day_high_y=px(740), day_high_h=px(48), day_high_font=font_px(px, 30, max_height=48, vertical_padding=3), day_low_y=px(786), day_low_h=px(42), day_low_font=font_px(px, 25, max_height=42, vertical_padding=3)))
    return '\n<screen name="GTContentBrowserScreen" position="0,0" size="{width},{height}"\n        flags="wfNoBorder" backgroundColor="#020617">\n    <widget name="app_bg" position="0,0" size="{width},{height}"\n            zPosition="0" />\n    <widget name="top_accent" position="0,0" size="{width},{accent_h}"\n            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />\n    <widget name="header" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="server" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="clock" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="left_panel" position="{left_x},{panel_y}"\n            size="{left_w},{panel_h}" font="Regular;1"\n            transparent="1" />\n    <widget name="right_panel" position="{right_x},{panel_y}"\n            size="{right_w},{panel_h}" font="Regular;1"\n            transparent="1" />\n    <widget name="left_panel_art" position="{left_x},{panel_y}"\n            size="{left_w},{panel_h}" pixmap="{panel_art}"\n            alphatest="blend" scale="1" zPosition="1" />\n    <widget name="right_panel_art" position="{right_x},{panel_y}"\n            size="{right_w},{panel_h}" pixmap="{panel_art}"\n            alphatest="blend" scale="1" zPosition="1" />\n    <widget name="left_panel_accent_cyan" position="{left_x},{panel_y}"\n            size="{left_accent_w},{panel_accent_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="2" />\n    <widget name="left_panel_accent_magenta" position="{left_accent_x},{panel_y}"\n            size="{left_accent_w},{panel_accent_h}" font="Regular;1"\n            backgroundColor="#D946EF" transparent="0" zPosition="2" />\n    <widget name="right_panel_accent_cyan" position="{right_x},{panel_y}"\n            size="{right_accent_w},{panel_accent_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="2" />\n    <widget name="right_panel_accent_magenta" position="{right_accent_x},{panel_y}"\n            size="{right_accent_w},{panel_accent_h}" font="Regular;1"\n            backgroundColor="#D946EF" transparent="0" zPosition="2" />\n    <widget name="breadcrumb" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="categories_caption" position="{crumb_x},{caption_y}"\n            size="{crumb_w},{caption_h}" font="Regular;{caption_font}"\n            foregroundColor="#20DDF4" transparent="1" zPosition="5"\n            valign="center" halign="left" />\n    {rows}\n    <widget name="list_scroll_track" position="{category_scroll_x},{category_scroll_y}"\n            size="{category_scroll_w},{category_scroll_h}" font="Regular;1"\n            backgroundColor="#26344E" transparent="0" zPosition="5" />\n    <widget name="list_scroll_thumb" position="{category_scroll_x},{category_scroll_y}"\n            size="{category_scroll_w},{category_scroll_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="6" />\n    <widget name="weather_caption" position="{weather_x},{weather_caption_y}"\n            size="{weather_w},{weather_caption_h}" font="Regular;{caption_font}"\n            foregroundColor="#20DDF4" transparent="1" zPosition="5"\n            valign="center" halign="left" />\n    <widget name="weather_header_divider" position="{weather_x},{weather_divider_y}"\n            size="{weather_w},{weather_divider_h}" font="Regular;1"\n            backgroundColor="#123C78" transparent="0" zPosition="3" />\n    <widget name="weather_city" position="{weather_x},{weather_city_y}"\n            size="{weather_w},{weather_city_h}" font="Regular;{weather_city_font}"\n            foregroundColor="#22D3EE" transparent="1" zPosition="5"\n            valign="center" halign="left" />\n    <widget name="weather_today_card" position="{weather_x},{weather_card_y}"\n            size="{weather_w},{weather_card_h}" font="Regular;1"\n            transparent="1" zPosition="2" />\n    <widget name="weather_current_icon" position="{current_icon_x},{current_icon_y}"\n            size="{current_icon_w},{current_icon_h}" alphatest="blend" scale="1"\n            zPosition="4" />\n    <widget name="weather_today_label" position="{today_text_x},{today_label_y}"\n            size="{today_text_w},{today_label_h}" font="Regular;{today_label_font}"\n            foregroundColor="#AAB7CC" transparent="1" zPosition="4"\n            valign="center" halign="left" />\n    <widget name="weather_temp" position="{today_text_x},{weather_temp_y}"\n            size="{temp_w},{weather_temp_h}" font="Regular;{weather_temp_font}"\n            foregroundColor="#FFFFFF" transparent="1" zPosition="4"\n            valign="center" halign="left" />\n    <widget name="weather_desc" position="{today_text_x},{weather_desc_y}"\n            size="{today_text_w},{weather_desc_h}" font="Regular;{weather_desc_font}"\n            foregroundColor="#22D3EE" transparent="1" zPosition="4"\n            valign="center" halign="left" />\n    <widget name="weather_forecast_divider" position="{weather_x},{forecast_divider_y}"\n            size="{weather_w},{weather_divider_h}" font="Regular;1"\n            backgroundColor="#123C78" transparent="0" zPosition="3" />\n    {weather_days}\n    <widget name="weather_metrics" position="{weather_x},{metrics_y}"\n            size="{weather_w},{metrics_h}" font="Regular;{metrics_font}"\n            foregroundColor="#9FB0C8" transparent="1" zPosition="5"\n            valign="center" halign="center" />\n    <widget name="weather_updated" position="{weather_x},{updated_y}"\n            size="{weather_w},{updated_h}" font="Regular;{updated_font}"\n            foregroundColor="#64748B" transparent="1" zPosition="5"\n            valign="center" halign="center" />\n    <widget name="message" position="{crumb_x},{message_y}"\n            size="{crumb_w},{message_h}" font="Regular;{message_font}"\n            foregroundColor="#9AA8BE" transparent="1" zPosition="5"\n            valign="center" halign="right" />\n    <widget name="category_footer_art" position="{footer_panel_x},{footer_panel_y}"\n            size="{footer_panel_w},{footer_panel_h}" pixmap="{panel_art}"\n            alphatest="blend" scale="1" zPosition="29" />\n    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"\n            font="Regular;{footer_font}" foregroundColor="#C8D2E3"\n            backgroundColor="#080E1A" transparent="0"\n            valign="center" halign="center" />\n    <widget name="screen_label" position="0,0" size="1,1" font="Regular;1"\n            transparent="1" />\n    <widget name="content_panel" position="0,0" size="1,1" font="Regular;1"\n            transparent="1" />\n    <widget name="list_accent" position="0,0" size="1,1" font="Regular;1"\n            transparent="1" />\n    <widget name="items" position="0,0" size="1,1" font="Regular;1"\n            transparent="1" />\n</screen>\n'.format(width=width, height=height, accent_h=px(5), margin=margin, panel_art=theme_dir + '/category-glass-panel-r84.png', left_x=left_x, left_w=left_w, right_x=right_x, right_w=right_w, left_accent_w=int(left_w / 2), left_accent_x=left_x + int(left_w / 2), right_accent_w=int(right_w / 2), right_accent_x=right_x + int(right_w / 2), panel_accent_h=px(3), panel_y=panel_y, panel_h=panel_h, crumb_x=left_x + px(34), crumb_w=left_w - px(68), caption_y=px(78), caption_h=px(56), caption_font=font_px(px, 36, role='title', max_height=56, vertical_padding=5), rows=''.join(rows), category_scroll_x=left_x + left_w - px(15), category_scroll_y=px(145), category_scroll_w=px(10), category_scroll_h=px(716), weather_x=weather_x, weather_w=weather_w, weather_caption_y=px(78), weather_caption_h=px(56), weather_divider_y=px(139), weather_divider_h=max(1, px(2)), forecast_divider_y=px(550), weather_city_y=px(158), weather_city_h=px(52), weather_city_font=font_px(px, 31, max_height=52, vertical_padding=5), weather_card_y=px(235), weather_card_h=px(295), current_icon_x=weather_x + px(34), current_icon_y=px(275), current_icon_w=px(220), current_icon_h=px(220), today_text_x=weather_x + px(335), today_text_w=weather_w - px(355), today_label_y=px(263), today_label_h=px(35), today_label_font=font_px(px, 23, max_height=35, vertical_padding=2), weather_temp_y=px(309), weather_temp_h=px(100), weather_temp_font=px(91), temp_w=weather_w - px(355), weather_desc_y=px(426), weather_desc_h=px(50), weather_desc_font=font_px(px, 30, max_height=50, vertical_padding=4), weather_days=''.join(weather_days), metrics_y=px(844), metrics_h=px(34), metrics_font=font_px(px, 17, max_height=34, vertical_padding=4), updated_y=px(877), updated_h=px(29), updated_font=font_px(px, 14, max_height=29, vertical_padding=4), message_y=px(877), message_h=px(35), message_font=font_px(px, 17, max_height=35, vertical_padding=4), footer_panel_x=px(45), footer_panel_y=height - px(112), footer_panel_w=px(1820), footer_panel_h=px(100), footer_y=footer_y, footer_h=height - footer_y, footer_font=px(19))

@aux_skin_after('categories' )
def _live_category_manager_skin():
    """Category editor using the same visual list as the Live TV screen."""
    width, height, px = _scale()
    margin = px(64)
    footer_y = height - px(92)
    panel_y = px(135)
    panel_h = px(805)
    panel_w = px(990)
    row_y = (252, 332, 412, 492, 572, 652, 732, 812)
    check_x = margin + px(42)
    row_x = margin + px(94)
    state_x = margin + panel_w - px(270)
    checkbox_paths = {state: plugin_path('skin', 'images', 'category-check-{}-r83.png'.format(state)).replace('&', '&amp;').replace('"', '&quot;') for state in ('empty', 'tick')}
    rows = []
    for index, y_value in enumerate(row_y):
        rows.append('\n    <widget name="category_check_{index}_empty" position="{check_x},{check_y}"\n            size="{check_size},{check_size}" pixmap="{check_empty}"\n            alphatest="blend" scale="1" zPosition="4" />\n    <widget name="category_check_{index}_tick" position="{check_x},{check_y}"\n            size="{check_size},{check_size}" pixmap="{check_tick}"\n            alphatest="blend" scale="1" zPosition="4" />\n    <widget name="category_row_{index}" position="{row_x},{row_y}"\n            size="{row_w},{row_h}" font="Regular;{row_font}"\n            foregroundColor="#F8FAFC" transparent="1" zPosition="4"\n            noWrap="1" valign="center" halign="left" />\n    <widget name="category_state_{index}" position="{state_x},{row_y}"\n            size="{state_w},{row_h}" font="Regular;{state_font}"\n            foregroundColor="#D946EF" transparent="1" zPosition="4"\n            noWrap="1" valign="center" halign="right" />\n    <widget name="category_focus_{index}_fill" position="{focus_x},{focus_y}"\n            size="{focus_w},{focus_box_h}" font="Regular;1"\n            backgroundColor="#172A45" transparent="0" zPosition="2" />\n    <widget name="category_focus_{index}_top" position="{focus_x},{focus_y}"\n            size="{focus_w},{focus_h}" font="Regular;1"\n            backgroundColor="#00E5FF" transparent="0" zPosition="3" />\n    <widget name="category_focus_{index}_bottom"\n            position="{focus_x},{focus_bottom_y}"\n            size="{focus_w},{focus_h}" font="Regular;1"\n            backgroundColor="#D946EF" transparent="0" zPosition="3" />\n    <widget name="category_focus_{index}_left" position="{focus_x},{focus_y}"\n            size="{focus_h},{focus_box_h}" font="Regular;1"\n            backgroundColor="#00E5FF" transparent="0" zPosition="3" />\n    <widget name="category_focus_{index}_right"\n            position="{focus_right_x},{focus_y}"\n            size="{focus_h},{focus_box_h}" font="Regular;1"\n            backgroundColor="#D946EF" transparent="0" zPosition="3" />\n    <widget name="category_insert_{index}" position="{focus_x},{insert_y}"\n            size="{focus_w},{insert_h}" font="Regular;1"\n            backgroundColor="#FACC15" transparent="0" zPosition="5" />'.format(index=index, check_x=check_x, check_y=px(y_value + 13), check_size=px(40), check_empty=checkbox_paths['empty'], check_tick=checkbox_paths['tick'], row_x=row_x, row_y=px(y_value), row_w=state_x - row_x - px(18), row_h=px(66), row_font=px(27), state_x=state_x, state_w=px(230), state_font=px(20), focus_x=margin + px(20), focus_y=px(y_value - 4), focus_w=panel_w - px(40), focus_h=px(4), focus_bottom_y=px(y_value + 66), focus_box_h=px(74), focus_right_x=margin + panel_w - px(24), insert_y=px(y_value + 70), insert_h=px(3)))
    return '\n<screen name="GTLiveCategoryManagerScreen" position="0,0"\n        size="{width},{height}" flags="wfNoBorder" backgroundColor="#020617">\n    <widget name="app_bg" position="0,0" size="{width},{height}"\n            zPosition="0" />\n    <widget name="top_accent" position="0,0" size="{width},{accent_h}"\n            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />\n    <widget name="header" position="{margin},{header_y}"\n            size="{header_w},{header_h}" font="Regular;{title_font}"\n            foregroundColor="#FFFFFF" transparent="1"\n            valign="center" halign="left" />\n    <widget name="screen_label" position="{screen_x},{header_y}"\n            size="{screen_w},{header_h}" font="Regular;{screen_font}"\n            foregroundColor="#7F8DA5" transparent="1"\n            valign="center" halign="right" />\n    <widget name="content_panel" position="{margin},{panel_y}"\n            size="{panel_w},{panel_h}" font="Regular;1"\n            backgroundColor="#0D1729" transparent="0" />\n    <widget name="list_accent" position="{margin},{panel_y}"\n            size="{panel_accent_w},{panel_accent_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="2" />\n    <widget name="manager_panel_accent_magenta"\n            position="{panel_accent_x},{panel_y}"\n            size="{panel_accent_w},{panel_accent_h}" font="Regular;1"\n            backgroundColor="#D946EF" transparent="0" zPosition="2" />\n    <widget name="breadcrumb" position="{crumb_x},{crumb_y}"\n            size="{crumb_w},{crumb_h}" font="Regular;{crumb_font}"\n            foregroundColor="#22D3EE" transparent="1"\n            noWrap="1" valign="center" halign="left" />\n    <widget name="manager_caption" position="{crumb_x},{caption_y}"\n            size="{crumb_w},{caption_h}" font="Regular;{caption_font}"\n            foregroundColor="#FFFFFF" transparent="1"\n            valign="center" halign="left" />\n    {rows}\n    <widget name="manager_selection_count" position="{help_x},{caption_y}"\n            size="{help_w},{caption_h}" font="Regular;{caption_font}"\n            foregroundColor="#22D3EE" transparent="1"\n            noWrap="1" valign="center" halign="left" />\n    <widget name="manager_move_help" position="{help_x},{help_y}"\n            size="{help_w},{help_h}" font="Regular;{help_font}"\n            foregroundColor="#FACC15" transparent="1"\n            valign="top" halign="left" />\n    <widget name="list_scroll_track" position="{scroll_x},{scroll_y}"\n            size="{scroll_w},{scroll_h}" font="Regular;1"\n            backgroundColor="#26344E" transparent="0" zPosition="5" />\n    <widget name="list_scroll_thumb" position="{scroll_x},{scroll_y}"\n            size="{scroll_w},{scroll_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="6" />\n    <widget name="message" position="{crumb_x},{message_y}"\n            size="{crumb_w},{message_h}" font="Regular;{message_font}"\n            foregroundColor="#9AA8BE" transparent="1"\n            noWrap="1" valign="center" halign="right" />\n    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"\n            font="Regular;{footer_font}" foregroundColor="#C8D2E3"\n            backgroundColor="#080E1A" transparent="0"\n            valign="center" halign="center" />\n    <widget name="items" position="0,0" size="1,1" font="Regular;1"\n            transparent="1" />\n</screen>\n'.format(width=width, height=height, accent_h=px(5), margin=margin, header_y=px(28), header_w=px(700), header_h=px(70), title_font=px(37), screen_x=width - margin - px(700), screen_w=px(700), screen_font=px(19), panel_y=panel_y, panel_w=panel_w, panel_h=panel_h, panel_accent_w=int(panel_w / 2), panel_accent_x=margin + int(panel_w / 2), panel_accent_h=px(3), crumb_x=margin + px(38), crumb_y=px(155), crumb_w=panel_w - px(76), crumb_h=px(45), crumb_font=px(19), caption_y=px(200), caption_h=px(48), caption_font=px(23), help_x=margin + panel_w + px(56), help_y=px(265), help_w=width - 2 * margin - panel_w - px(56), help_h=px(530), help_font=px(29), rows=''.join(rows), scroll_x=margin + panel_w - px(18), scroll_y=px(252), scroll_w=px(8), scroll_h=px(626), message_y=px(885), message_h=px(42), message_font=px(18), footer_y=footer_y, footer_h=height - footer_y, footer_font=px(19))

@broadcast_skin_after('live')
def _live_stream_skin(channel_highlight=DEFAULT_CHANNEL_HIGHLIGHT):
    """Opaque full-screen Enigma2 live guide with one decoder-0 PIG."""
    width, height, px = _scale()
    name_font = font_px(px, {'standard': 24, 'large': 26, 'very_large': 28}[current_text_size()], value='standard', max_height=72, vertical_padding=12)
    channel_highlight = normalize_channel_highlight(channel_highlight)
    focus_fill, focus_edge = channel_highlight_colors(channel_highlight)
    favorite_star = FAVORITE_STAR_PATH.replace('&', '&amp;').replace('"', '&quot;')
    top_h = px(88)
    footer_y = height - px(112)
    panel_y = top_h
    panel_h = max(1, footer_y - panel_y - px(8))
    side_geometry = live_guide_side_geometry(width, height, px)
    left_x, unused_y, left_w, unused_h = side_geometry['left_panel']
    list_x, unused_y, list_w, unused_h = side_geometry['list_panel']
    right_x, unused_y, right_w, unused_h = side_geometry['right_panel']
    row_x = list_x + px(14)
    row_w = list_w - px(28)
    row_h = px(72)
    row_step = px(78)
    row_first_y = px(166)
    progress_x = row_x + row_w - px(112)
    program_x = progress_x - px(192)
    name_x = row_x + px(239)
    name_w = program_x - px(11) - name_x
    rows = []
    for index in range(LIVE_STREAM_PAGE_SIZE):
        y = row_first_y + index * row_step
        rows.append('\n    <widget name="stream_row_bg_{index}" position="{row_x},{row_y}"\n            size="{row_w},{row_h}" font="Regular;1"\n            backgroundColor="#FF000000" transparent="1" zPosition="2" />\n    <widget name="stream_row_art_{index}" position="0,0" size="1,1"\n            transparent="1" zPosition="1" />\n    <widget name="stream_focus_art_{index}" position="0,0" size="1,1"\n            transparent="1" zPosition="1" />\n    <widget name="stream_focus_{index}_fill" position="{row_x},{row_y}"\n            size="{row_w},{row_h}" font="Regular;1"\n            backgroundColor="{focus_fill}" transparent="0" zPosition="3" />\n    <widget name="stream_focus_{index}_top" position="{row_x},{row_y}"\n            size="{row_w},{edge_h}" font="Regular;1"\n            backgroundColor="{focus_edge}" transparent="0" zPosition="7" />\n    <widget name="stream_focus_{index}_bottom" position="{row_x},{bottom_y}"\n            size="{row_w},{edge_h}" font="Regular;1"\n            backgroundColor="{focus_edge}" transparent="0" zPosition="7" />\n    <widget name="stream_focus_{index}_left" position="{row_x},{row_y}"\n            size="{marker_w},{row_h}" font="Regular;1"\n            backgroundColor="{focus_edge}" transparent="0" zPosition="8" />\n    <widget name="stream_focus_{index}_right" position="{right_edge_x},{row_y}"\n            size="{edge_h},{row_h}" font="Regular;1"\n            backgroundColor="{focus_edge}" transparent="0" zPosition="7" />\n    <widget name="stream_separator_{index}" position="{row_x},{separator_y}"\n            size="{row_w},{separator_h}" font="Regular;1"\n            backgroundColor="#8A48B9ED" transparent="0" zPosition="4" />\n    <widget name="stream_number_{index}" position="{number_x},{row_y}"\n            size="{number_w},{row_h}" font="Regular;{number_font}"\n            foregroundColor="#D7DEE8" transparent="1" zPosition="9"\n            valign="center" halign="left" noWrap="1" />\n    <widget name="stream_picon_{index}" position="{picon_x},{picon_y}"\n            size="{picon_w},{picon_h}" backgroundColor="transparent"\n            transparent="1" alphatest="blend" scale="1" zPosition="9" />\n    <widget name="stream_name_{index}" position="{name_x},{name_y}"\n            size="{name_w},{name_h}" font="Regular;{name_font}"\n            foregroundColor="#F8FAFC" transparent="1" zPosition="9"\n            valign="center" halign="left" noWrap="0" />\n    <widget name="stream_program_{index}" position="{program_x},{row_y}"\n            size="{program_w},{row_h}" font="Regular;{program_font}"\n            foregroundColor="#CCD3DD" transparent="1" zPosition="9"\n            valign="center" halign="left" noWrap="1" />\n    <widget name="stream_progress_bg_{index}" position="{progress_x},{progress_y}"\n            size="{progress_w},{progress_h}" font="Regular;1"\n            backgroundColor="#222B35" transparent="0" zPosition="8" />\n    <widget name="stream_progress_fill_{index}" position="{progress_x},{progress_y}"\n            size="{progress_w},{progress_h}" font="Regular;1"\n            backgroundColor="#25A9F2" transparent="0" zPosition="9" />\n    <widget name="stream_favorite_{index}" position="{favorite_x},{favorite_y}"\n            size="{favorite_size},{favorite_size}" pixmap="{favorite_star}"\n            alphatest="blend" scale="1" zPosition="10" />'.format(index=index, row_x=row_x, row_y=y, row_w=row_w, row_h=row_h, focus_fill=focus_fill, focus_edge=focus_edge, edge_h=max(1, px(2)), bottom_y=y + row_h - max(1, px(2)), marker_w=px(7), right_edge_x=row_x + row_w - max(1, px(2)), separator_y=y + row_h - max(1, px(1)), separator_h=max(1, px(1)), number_x=row_x + px(12), number_w=px(102), number_font=font_px(px, 27, max_height=72, vertical_padding=12), picon_x=row_x + px(118), picon_y=y + px(6), picon_w=px(110), picon_h=px(60), name_x=name_x, name_y=y + px(4), name_w=name_w, name_h=row_h - px(8), name_font=name_font, program_x=program_x, program_w=px(178), program_font=font_px(px, 22, max_height=72, vertical_padding=14), progress_x=progress_x, progress_y=y + px(31), progress_w=px(96), progress_h=px(10), favorite_x=row_x + px(207), favorite_y=y + px(5), favorite_size=px(22), favorite_star=favorite_star))
    preview_x, preview_y, preview_w, preview_h = side_geometry['preview']
    frame_x, frame_y, frame_w, frame_h = side_geometry['preview_frame']
    poster_x, poster_y, poster_w, poster_h = side_geometry['poster']
    unused_x, summary_y, summary_w, summary_h = side_geometry['summary']
    pagination_x, pagination_y, pagination_w, pagination_h = side_geometry['summary_pagination']
    schedule_rows = []
    for offset, row in enumerate(('next', 'third', 'fourth', 'fifth')):
        y = px(174 + offset * 78)
        schedule_rows.append('\n    <widget name="epg_{row}_time" position="{time_x},{row_y}"\n            size="{time_w},{row_h}" font="Regular;{time_font}"\n            foregroundColor="#DCE3EB" transparent="1" zPosition="7"\n            valign="center" halign="left" noWrap="1" />\n    <widget name="epg_{row}_title" position="{title_x},{row_y}"\n            size="{title_w},{row_h}" font="Regular;{title_font}"\n            foregroundColor="#F5F7FA" transparent="1" zPosition="7"\n            valign="center" halign="left" noWrap="0" />\n    <widget name="epg_{row}_status" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />'.format(row=row, time_x=left_x + px(26), row_y=y, time_w=px(92), row_h=px(64), time_font=font_px(px, 22, max_height=64, vertical_padding=8), title_x=left_x + px(126), title_w=left_w - px(152), title_font=font_px(px, 22, max_height=64, vertical_padding=8)))
    return '\n<screen name="GTStreamListScreen" position="0,0" size="{width},{height}"\n        flags="wfNoBorder" backgroundColor="#05080D" transparent="0">\n    <widget name="root_fill" position="0,0" size="{width},{height}"\n            font="Regular;1" backgroundColor="#05080D" transparent="0"\n            zPosition="0" />\n    <widget name="app_bg" position="0,0" size="{width},{height}"\n            transparent="0" scale="1" zPosition="1" />\n    <widget name="top_accent" position="0,0" size="{width},{top_line_h}"\n            font="Regular;1" backgroundColor="#173547" transparent="0"\n            zPosition="2" />\n    <widget name="clock_time" position="{clock_x},{clock_y}"\n            size="{clock_w},{clock_h}" font="Regular;{clock_font}"\n            foregroundColor="#5CC7FF" transparent="1" zPosition="5"\n            valign="center" halign="center" noWrap="1" />\n    <widget name="clock_date" position="{clock_x},{date_y}"\n            size="{clock_w},{date_h}" font="Regular;{date_font}"\n            foregroundColor="#68D8FF" transparent="1" zPosition="5"\n            valign="top" halign="center" />\n    <widget name="header" position="{header_x},{header_y}"\n            size="{header_w},{header_h}" font="Regular;{header_font}"\n            foregroundColor="#74C9FF" transparent="1" zPosition="5"\n            valign="center" halign="center" noWrap="1" />\n    <widget name="screen_label" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="breadcrumb" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="left_panel" position="{left_x},{panel_y}"\n            size="{left_w},{panel_h}" font="Regular;1"\n            backgroundColor="#071019" transparent="1" zPosition="2" />\n    <widget name="center_panel" position="{list_x},{panel_y}"\n            size="{list_w},{panel_h}" font="Regular;1"\n            backgroundColor="#FF000000" transparent="1" zPosition="2" />\n    <widget name="right_panel" position="{right_x},{panel_y}"\n            size="{right_w},{panel_h}" font="Regular;1"\n            backgroundColor="#071019" transparent="1" zPosition="2" />\n    <widget name="left_panel_art" position="0,0" size="1,1"\n            transparent="1" />\n    <widget name="right_panel_art" position="0,0" size="1,1"\n            transparent="1" />\n    <widget name="left_panel_accent_cyan" position="{left_x},{panel_y}"\n            size="{left_w},{panel_border_h}" font="Regular;1"\n            backgroundColor="#244050" transparent="0" zPosition="3" />\n    <widget name="left_panel_accent_magenta" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="right_panel_accent_cyan" position="{right_x},{panel_y}"\n            size="{right_w},{panel_border_h}" font="Regular;1"\n            backgroundColor="#244050" transparent="0" zPosition="3" />\n    <widget name="right_panel_accent_magenta" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="list_accent" position="{list_x},{panel_y}"\n            size="{list_w},{panel_border_h}" font="Regular;1"\n            backgroundColor="#244050" transparent="0" zPosition="3" />\n    <widget name="detail_picon" position="0,0" size="1,1"\n            backgroundColor="transparent" transparent="1" alphatest="blend"\n            scale="1" zPosition="6" />\n    <widget name="detail_programme_poster" position="{poster_x},{poster_y}"\n            size="{poster_w},{poster_h}"\n            backgroundColor="transparent" transparent="1" alphatest="blend"\n            scale="1" zPosition="7" />\n    <widget name="ribbon_channel" position="{detail_text_x},{channel_y}"\n            size="{detail_text_w},{channel_h}" font="Regular;{channel_font}"\n            foregroundColor="#F8FAFC" transparent="1" zPosition="6"\n            valign="center" halign="center" noWrap="1" />\n    <widget name="ribbon_divider" position="{detail_text_x},{divider_y}"\n            size="{detail_text_w},{divider_h}" font="Regular;1"\n            backgroundColor="#253440" transparent="0" zPosition="4" />\n    <widget name="ribbon_now_caption" position="{detail_text_x},{now_caption_y}"\n            size="{detail_text_w},{now_caption_h}" font="Regular;{now_caption_font}"\n            foregroundColor="#3EB8FF" transparent="1" zPosition="6"\n            valign="center" halign="left" />\n    <widget name="ribbon_now_title" position="{detail_text_x},{now_title_y}"\n            size="{detail_text_w},{now_title_h}" font="Regular;{now_title_font}"\n            foregroundColor="#FFFFFF" transparent="1" zPosition="6"\n            valign="center" halign="left" noWrap="1" />\n    <widget name="ribbon_now_time" position="{detail_text_x},{now_time_y}"\n            size="{now_time_w},{now_time_h}" font="Regular;{now_time_font}"\n            foregroundColor="#D7DEE8" transparent="1" zPosition="6"\n            valign="center" halign="left" noWrap="1" />\n    <widget name="epg_progress" position="{progress_label_x},{now_time_y}"\n            size="{progress_label_w},{now_time_h}" font="Regular;{progress_font}"\n            foregroundColor="#D7DEE8" transparent="1" zPosition="6"\n            valign="center" halign="right" noWrap="1" />\n    <widget name="epg_progress_bg" position="{detail_text_x},{detail_progress_y}"\n            size="{detail_progress_w},{detail_progress_h}" font="Regular;1"\n            backgroundColor="#26313A" transparent="0" zPosition="5" />\n    <widget name="epg_progress_fill" position="{detail_text_x},{detail_progress_y}"\n            size="{detail_progress_w},{detail_progress_h}" font="Regular;1"\n            backgroundColor="#24A9F2" transparent="0" zPosition="6" />\n    <widget name="summary_separator" position="{detail_text_x},{summary_line_y}"\n            size="{detail_text_w},{divider_h}" font="Regular;1"\n            backgroundColor="#1D2A34" transparent="0" zPosition="4" />\n    <widget name="summary" position="{detail_text_x},{summary_y}"\n            size="{summary_w},{summary_h}" font="Regular;{summary_font}"\n            foregroundColor="#D2D9E1" transparent="1" zPosition="6"\n            valign="top" halign="left" noWrap="1" />\n    <widget name="summary_scroll" position="{summary_scroll_x},{summary_scroll_y}"\n            size="{summary_scroll_w},{summary_scroll_h}" font="Regular;{summary_scroll_font}"\n            foregroundColor="#718091" transparent="1" zPosition="7"\n            valign="bottom" halign="right" />\n    <widget name="summary_scrollbar_track" position="{summary_bar_x},{summary_y}"\n            size="{summary_bar_w},{summary_h}" font="Regular;1"\n            backgroundColor="#172733" transparent="0" zPosition="5" />\n    <widget name="summary_scrollbar_thumb" position="{summary_bar_x},{summary_y}"\n            size="{summary_bar_w},{summary_h}" font="Regular;1"\n            backgroundColor="#2DB8F7" transparent="0" zPosition="6" />\n    <widget name="summary_caption" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="list_caption" position="{caption_x},{caption_y}"\n            size="{caption_w},{caption_h}" font="Regular;{caption_font}"\n            foregroundColor="#63C7FF" transparent="1" zPosition="6"\n            valign="center" halign="left" noWrap="1" />\n    <widget name="message" position="{message_x},{caption_y}"\n            size="{message_w},{caption_h}" font="Regular;{message_font}"\n            foregroundColor="#E1E6ED" transparent="1" zPosition="6"\n            valign="center" halign="right" noWrap="1" />\n    <widget name="list_caption_accent" position="{caption_x},{caption_line_y}"\n            size="{caption_line_w},{divider_h}" font="Regular;1"\n            backgroundColor="#26343F" transparent="0" zPosition="5" />\n    {rows}\n    <widget name="list_scroll_track" position="{list_scroll_x},{row_first_y}"\n            size="{list_scroll_w},{list_scroll_h}" font="Regular;1"\n            backgroundColor="#1C2A35" transparent="0" zPosition="8" />\n    <widget name="list_scroll_thumb" position="{list_scroll_x},{row_first_y}"\n            size="{list_scroll_w},{list_scroll_h}" font="Regular;1"\n            backgroundColor="#32B8F2" transparent="0" zPosition="9" />\n    <widget name="preview_frame" position="{preview_frame_x},{preview_frame_y}"\n            size="{preview_frame_w},{preview_frame_h}" pixmap="{preview_frame_art}"\n            transparent="1" alphatest="blend" scale="1" zPosition="4" />\n    <widget name="preview_video" position="{preview_x},{preview_y}"\n            size="{preview_w},{preview_h}" backgroundColor="transparent"\n            zPosition="5" />\n    <widget name="preview_mask" position="{preview_x},{preview_y}"\n            size="{preview_w},{preview_h}" font="Regular;1"\n            backgroundColor="#03070B" transparent="0" zPosition="7" />\n    <widget name="preview_hint" position="{preview_x},{preview_hint_y}"\n            size="{preview_w},{preview_hint_h}" font="Regular;{preview_hint_font}"\n            foregroundColor="#8092A3" transparent="1" zPosition="9"\n            valign="center" halign="center" />\n    <widget name="epg_now_time" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="epg_now_title" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="epg_now_status" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="epg_panel" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="epg_title" position="{detail_text_x},{schedule_title_y}"\n            size="{detail_text_w},{schedule_title_h}" font="Regular;{schedule_title_font}"\n            foregroundColor="#49BFFF" transparent="1" zPosition="6"\n            valign="center" halign="left" />\n    {schedule_rows}\n    <widget name="right_separator_bottom" position="{right_inner_x},{right_bottom_y}"\n            size="{right_inner_w},{divider_h}" font="Regular;1"\n            backgroundColor="#26343F" transparent="0" zPosition="4" />\n    <widget name="epg_ribbon" position="0,0" size="1,1" font="Regular;1"\n            transparent="1" />\n    <widget name="ribbon_next_caption" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="ribbon_clock" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="ribbon_next_title" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="ribbon_next_time" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="summary_panel_art" position="0,0" size="1,1"\n            transparent="1" />\n    <widget name="output_badge" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="engine_badge" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="footer_backdrop" position="0,{footer_backdrop_y}"\n            size="{width},{footer_backdrop_h}" font="Regular;1"\n            backgroundColor="#FF000000" transparent="1" zPosition="2" />\n    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"\n            font="Regular;1" backgroundColor="#FF000000" transparent="1" />\n    <widget name="content_panel" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n    <widget name="items" position="0,0" size="1,1"\n            font="Regular;1" transparent="1" />\n</screen>\n'.format(width=width, height=height, top_line_h=max(1, px(2)), clock_x=px(28), clock_y=px(5), clock_w=px(300), clock_h=px(38), clock_font=font_px(px, 29, role='title', max_height=38, vertical_padding=3), date_y=px(40), date_h=px(46), date_font=font_px(px, 17, max_height=46, vertical_padding=3), header_x=px(390), header_y=px(8), header_w=px(1140), header_h=px(68), header_font=font_px(px, 38, role='title', max_height=68, vertical_padding=8), left_x=left_x, left_w=left_w, list_x=list_x, list_w=list_w, right_x=right_x, right_w=right_w, panel_y=panel_y, panel_h=panel_h, panel_border_h=max(1, px(2)), poster_x=poster_x, poster_y=poster_y, poster_w=poster_w, poster_h=poster_h, detail_text_x=left_x + px(26), detail_text_w=left_w - px(52), channel_y=px(498), channel_h=px(52), channel_font=font_px(px, 30, role='title', max_height=52, vertical_padding=7), divider_y=px(484), divider_h=max(1, px(2)), now_caption_y=px(562), now_caption_h=px(36), now_caption_font=font_px(px, 25, role='title', max_height=36, vertical_padding=5), now_title_y=px(606), now_title_h=px(48), now_title_font=font_px(px, 29, role='title', max_height=48, vertical_padding=7), now_time_y=px(664), now_time_w=px(232), now_time_h=px(36), now_time_font=font_px(px, 21, max_height=36, vertical_padding=5), progress_label_x=left_x + left_w - px(136), progress_label_w=px(110), progress_font=font_px(px, 20, max_height=36, vertical_padding=5), detail_progress_y=px(712), detail_progress_w=left_w - px(52), detail_progress_h=px(10), summary_line_y=px(744), summary_y=summary_y, summary_w=summary_w, summary_h=summary_h, summary_font=font_px(px, 23, vertical_padding=5), summary_scroll_x=pagination_x, summary_scroll_y=pagination_y, summary_scroll_w=pagination_w, summary_scroll_h=pagination_h, summary_scroll_font=font_px(px, 14, max_height=18, vertical_padding=2), summary_bar_x=left_x + left_w - px(20), summary_bar_w=px(5), caption_x=list_x + px(22), caption_y=px(98), caption_w=px(560), caption_h=px(55), caption_font=font_px(px, 34, role='title', max_height=55, vertical_padding=7), message_x=list_x + list_w - px(252), message_w=px(225), message_font=font_px(px, 26, max_height=55, vertical_padding=8), caption_line_y=px(157), caption_line_w=list_w - px(44), rows=''.join(rows), row_first_y=row_first_y, list_scroll_x=list_x + list_w - px(9), list_scroll_w=px(4), list_scroll_h=LIVE_STREAM_PAGE_SIZE * row_step - px(6), preview_frame_x=frame_x, preview_frame_y=frame_y, preview_frame_w=frame_w, preview_frame_h=frame_h, preview_frame_art=LIVE_PREVIEW_FRAME_PATH.replace('&', '&amp;').replace('"', '&quot;'), preview_x=preview_x, preview_y=preview_y, preview_w=preview_w, preview_h=preview_h, preview_hint_y=preview_y + (preview_h - px(58)) // 2, preview_hint_h=px(58), preview_hint_font=font_px(px, 19, max_height=58, vertical_padding=8), right_inner_x=right_x + px(20), right_inner_w=right_w - px(40), schedule_title_y=px(102), schedule_title_h=px(55), schedule_title_font=font_px(px, 29, role='title', max_height=55, vertical_padding=8), schedule_rows=''.join(schedule_rows), right_bottom_y=footer_y - px(8), footer_backdrop_y=footer_y - px(5), footer_backdrop_h=height - footer_y + px(5), footer_y=footer_y, footer_h=height - footer_y)

class GTLiveCategoryManagerScreen(GTAsyncListScreen):
    """Transactional order, bulk move and visibility editor."""
    page_size = 8

    def __init__(self, session, account_name, account_scope, categories, category_store=None, selected_category_id='', content_type='live'):
        content_type = str(content_type or '').strip().lower()
        if content_type not in ('live', 'movie', 'series'):
            content_type = 'live'
        self.content_type = content_type
        self._all_category_label = CONTENT_LABELS[content_type][1]
        manager_labels = {'live': N_('LIVE TV CATEGORY SETTINGS'), 'movie': N_('MOVIE CATEGORY SETTINGS'), 'series': N_('SERIES CATEGORY SETTINGS')}
        manager_label = manager_labels[content_type]
        self.category_store = category_store if category_store is not None else LiveCategoryPreferenceStore(category_preferences_path(content_type))
        self.account_scope = str(account_scope or '')
        self._footer_layout_items = CATEGORY_MANAGER_FOOTER_ITEMS
        self._footer_layout_alternate_items = CATEGORY_MANAGER_MOVE_FOOTER_ITEMS
        self.skin = prepared_skin(_live_category_manager_skin)
        unused_width, unused_height, px = _scale()
        del unused_width, unused_height
        self._list_scroll_geometry = (px(64 + 990 - 18), px(252), px(8), px(626))
        self._source_categories = list(categories)
        self._selected_category_id = selected_category_id
        self._stored_preferences = CategoryPreferences()
        self._provider_entries = []
        self._hidden = set()
        self._initial_ids = self._provider_ids = ()
        self._initial_hidden = frozenset()
        self._save_result = None
        self._move_mode = False
        self._marked = set()
        self._move_ids = set()
        self._move_snapshot = None
        self._move_snapshot_index = 0
        self._move_snapshot_reset_requested = False
        self._reset_requested = False
        self._notice = ''
        self._opaque_background = True
        GTAsyncListScreen.__init__(self, session, 'GTLiveCategoryManagerScreen', N_('Category Manager'), '{}  |  {}'.format(account_name, _('Settings')))
        self['screen_label'].setText(_(manager_label))
        self['manager_panel_accent_magenta'] = Label('')
        self['manager_caption'] = Label(_(manager_label))
        self['manager_selection_count'] = Label('')
        self['manager_move_help'] = Label('')
        for index in range(self.page_size):
            self['category_row_{}'.format(index)] = Label('')
            self['category_state_{}'.format(index)] = Label('')
            self['category_check_{}_empty'.format(index)] = Pixmap()
            self['category_check_{}_tick'.format(index)] = Pixmap()
            self['category_insert_{}'.format(index)] = Label('')
            for edge in ('fill', 'top', 'bottom', 'left', 'right'):
                self['category_focus_{}_{}'.format(index, edge)] = Label('')
        self['actions'] = ActionMap(['OkCancelActions', 'DirectionActions', 'ColorActions', 'MenuActions'], {'ok': self.open_selected, 'cancel': self.cancel_or_close, 'up': self.move_up, 'down': self.move_down, 'upRepeated': self.move_up, 'downRepeated': self.move_down, 'left': self.page_up, 'right': self.page_down, 'leftRepeated': self.page_up, 'rightRepeated': self.page_down, 'yellow': self.start_move, 'red': self.toggle_mark, 'menu': self.hide_all_categories, 'green': self.save_and_close, 'blue': self.restore_defaults}, -1)
        if hasattr(self, 'onShown'):
            self.onShown.append(self._refresh)

        setup_aux(self, 'categories', self._footer_layout_items)

    @staticmethod
    def _entry_id(entry):
        return str(getattr(entry, 'category_id', '') or '').strip()

    def _current_ids(self):
        return [self._entry_id(entry) for entry in self._entries if self._entry_id(entry)]

    def _selected_id(self):
        if not self._entries:
            return ''
        return self._entry_id(self._entries[self.selected_index])

    def _select_category_id(self, category_id):
        category_id = str(category_id or '').strip()
        for index, entry in enumerate(self._entries):
            if self._entry_id(entry) == category_id:
                self.selected_index = index
                return
        self.selected_index = 0

    def load_items(self):
        preferences = self.category_store.load(self.account_scope)
        return (preferences, arrange_categories(self._source_categories, preferences, include_hidden=True, sort_mode='manual'))

    def _prepare_loaded_items(self, values):
        return values

    def _commit_loaded_items(self, prepared, generation):
        if not prepared:
            return
        self._stored_preferences, self._entries = prepared
        self._provider_entries = arrange_categories(self._source_categories, CategoryPreferences(), include_hidden=True)
        self._hidden = set(self._stored_preferences.hidden)
        current_ids = set(self._current_ids())
        self._hidden.intersection_update(current_ids)
        if self._stored_preferences.include_terms or self._stored_preferences.exclude_terms:
            allowed, unused_excluded = category_filter_ids(self._provider_entries, self._stored_preferences.include_terms, self._stored_preferences.exclude_terms)
            self._hidden.update(current_ids.difference(allowed))
        self._initial_ids = tuple(self._current_ids())
        self._initial_hidden = frozenset(self._hidden)
        self._provider_ids = tuple((self._entry_id(entry) for entry in self._provider_entries if self._entry_id(entry)))
        self._select_category_id(self._selected_category_id)

    def _poll_result(self):
        if self._closed:
            return
        result = self._save_result
        if result is not None:
            if not result['done'].is_set():
                self._timer.start(100, True)
                return
            self._save_result = None
            self._loading = False
            if result.get('saved'):
                self.close(True, result['order'], result['hidden'], (), (), result['sort_mode'])
            else:
                self._notice = _('Settings could not be saved. Check the receiver storage.')
                self._refresh()
            return
        GTAsyncListScreen._poll_result(self)

    def empty_message(self):
        return N_('No category found.')

    @aux_refresh_after
    def _refresh(self):
        if self._loading:
            self._clear_category_rows()
            self['items'].setText(_('Loading content...'))
            self['message'].setText(_('Please wait'))
            return
        if not self._entries:
            self._clear_category_rows()
            self['items'].setText(_(self.empty_message()))
            self['message'].setText('')
            hide_scrollbar(self, 'list_scroll')
            return
        self.selected_index = min(len(self._entries) - 1, max(0, int(self.selected_index)))
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_items = self._entries[page_start:page_start + self.page_size]
        plain_lines = []
        for row_index in range(self.page_size):
            if row_index < len(page_items):
                entry = page_items[row_index]
                index = page_start + row_index
                category_id = self._entry_id(entry)
                name = str(getattr(entry, 'name', '') or _('Category'))
                row_text = '{:02d}.  {}'.format(index + 1, name)
                hidden = bool(category_id and category_id in self._hidden)
                state_text = localized_upper(_('Hidden')) if hidden else ''
                rendered_row = ellipsize_dynamic_text(self['category_row_{}'.format(row_index)], row_text, fallback_chars=34)
                self['category_state_{}'.format(row_index)].setText(state_text)
                plain_lines.append(rendered_row)
                selected = index == self.selected_index
            else:
                self['category_row_{}'.format(row_index)].setText('')
                self['category_state_{}'.format(row_index)].setText('')
                category_id = ''
                selected = False
            marked = bool(category_id and category_id in self._marked)
            for check_state, visible in (('empty', bool(category_id) and (not marked)), ('tick', marked)):
                checkbox = self['category_check_{}_{}'.format(row_index, check_state)]
                if visible:
                    checkbox.show()
                else:
                    checkbox.hide()
            insertion_line = self['category_insert_{}'.format(row_index)]
            if self._move_mode and selected:
                insertion_line.show()
            else:
                insertion_line.hide()
            for edge in ('fill', 'top', 'bottom', 'left', 'right'):
                focus = self['category_focus_{}_{}'.format(row_index, edge)]
                if selected:
                    focus.show()
                else:
                    focus.hide()
        self['items'].setText('\n'.join(plain_lines))
        hidden_count = len(self._hidden)
        visible_count = len(self._entries) - hidden_count
        status = '{}: {}  |  {}: {}  |  {}/{}'.format(_('Visible'), visible_count, _('Hidden'), hidden_count, self.selected_index + 1, len(self._entries))
        if self._marked:
            status = '{}: {}  |  {}'.format(_('Marked'), len(self._marked), status)
        self['manager_selection_count'].setText('{}: {}'.format(_('Marked'), len(self._marked)) if self._marked else '')
        self['manager_move_help'].setText(self._move_guidance() if self._move_mode else '')
        if self._notice and (not self._move_mode):
            status = '{}  |  {}'.format(status, self._notice)
        ellipsize_dynamic_text(self['message'], status, fallback_chars=70)
        update_scrollbar(self, 'list_scroll', len(self._entries), self.selected_index, self.page_size, self._list_scroll_geometry)

    def _clear_category_rows(self):
        self['manager_selection_count'].setText('')
        self['manager_move_help'].setText('')
        for row_index in range(self.page_size):
            self['category_row_{}'.format(row_index)].setText('')
            self['category_state_{}'.format(row_index)].setText('')
            self['category_check_{}_empty'.format(row_index)].hide()
            self['category_check_{}_tick'.format(row_index)].hide()
            self['category_insert_{}'.format(row_index)].hide()
            for edge in ('fill', 'top', 'bottom', 'left', 'right'):
                self['category_focus_{}_{}'.format(row_index, edge)].hide()

    def _select_move_destination(self, distance):
        if not self._move_mode or not self._entries:
            return
        self.selected_index = min(len(self._entries) - 1, max(0, self.selected_index + int(distance)))
        self._refresh()

    def move_up(self):
        if self._loading:
            return
        if self._move_mode:
            self._select_move_destination(-1)
            return
        GTAsyncListScreen.move_up(self)

    def move_down(self):
        if self._loading:
            return
        if self._move_mode:
            self._select_move_destination(1)
            return
        GTAsyncListScreen.move_down(self)

    def page_up(self):
        if self._loading:
            return
        if self._move_mode:
            self._select_move_destination(-self.page_size)
            return
        GTAsyncListScreen.page_up(self)

    def page_down(self):
        if self._loading:
            return
        if self._move_mode:
            self._select_move_destination(self.page_size)
            return
        GTAsyncListScreen.page_down(self)

    def open_selected(self):
        if self._loading:
            return
        if self._move_mode:
            self.finish_move()
            return
        self.toggle_visibility()

    def toggle_visibility(self):
        category_id = self._selected_id()
        if not category_id:
            self._notice = _('{} is always visible.').format(_(self._all_category_label))
            self._refresh()
            return
        if category_id in self._hidden:
            self._hidden.discard(category_id)
            self._notice = _('Category shown.')
        else:
            self._hidden.add(category_id)
            self._notice = _('Category hidden.')
        self._refresh()

    def hide_all_categories(self):
        if self._loading:
            return
        if self._move_mode:
            return
        self._hidden.update(self._current_ids())
        self._notice = _('Category hidden.')
        self._refresh()

    def start_move(self):
        if self._loading:
            return
        if self._move_mode:
            return
        self._marked.intersection_update(self._current_ids())
        if not self._marked:
            self._notice = _('Mark categories with RED first.')
            self._refresh()
            return
        self._move_mode = True
        self._move_snapshot = list(self._entries)
        self._move_snapshot_index = self.selected_index
        self._move_snapshot_reset_requested = self._reset_requested
        self._move_ids = set(self._marked)
        self._notice = self._move_guidance()
        set_remote_footer(self, CATEGORY_MANAGER_MOVE_FOOTER_ITEMS)
        self._refresh()

    @staticmethod
    def _move_guidance():
        return _('Choose the destination with the arrows. OK places marked categories below the line; EXIT cancels.')

    def finish_move(self):
        if not self._move_mode:
            return
        block = [entry for entry in self._entries if self._entry_id(entry) in self._move_ids]
        insert_at = sum((1 for entry in self._entries[:self.selected_index + 1] if self._entry_id(entry) not in self._move_ids))
        remaining = [entry for entry in self._entries if self._entry_id(entry) not in self._move_ids]
        self._entries = remaining[:insert_at] + block + remaining[insert_at:]
        self.selected_index = insert_at
        self._move_mode = False
        self._marked.clear()
        self._move_ids.clear()
        self._move_snapshot = None
        self._move_snapshot_index = 0
        self._move_snapshot_reset_requested = False
        self._notice = _('Position updated. Press GREEN to save.')
        set_remote_footer(self, CATEGORY_MANAGER_FOOTER_ITEMS)
        self._refresh()

    def cancel_or_close(self):
        if self._loading:
            if self._save_result is None:
                self.close(False)
            return
        if self._move_mode:
            self._entries = list(self._move_snapshot or self._entries)
            self.selected_index = min(len(self._entries) - 1, max(0, self._move_snapshot_index))
            self._move_mode = False
            self._move_ids.clear()
            self._move_snapshot = None
            self._move_snapshot_index = 0
            self._reset_requested = self._move_snapshot_reset_requested
            self._move_snapshot_reset_requested = False
            self._notice = _('Move cancelled.')
            set_remote_footer(self, CATEGORY_MANAGER_FOOTER_ITEMS)
            self._refresh()
            return
        self.close(False)

    def restore_defaults(self):
        if self._loading:
            return
        if self._move_mode:
            return
        selected_id = self._selected_id()
        self._entries = list(self._provider_entries)
        self._hidden.clear()
        self._marked.clear()
        self._reset_requested = True
        self._select_category_id(selected_id)
        self._notice = _('Default order restored. Press GREEN to save.')
        self._refresh()

    def _has_changes(self):
        if self._reset_requested and (self._stored_preferences.order or self._stored_preferences.hidden or self._stored_preferences.include_terms or self._stored_preferences.exclude_terms):
            return True
        return bool(tuple(self._current_ids()) != self._initial_ids or frozenset(self._hidden) != self._initial_hidden or self._stored_preferences.include_terms or self._stored_preferences.exclude_terms)

    def save_and_close(self):
        if self._loading or not self._ready:
            return
        if self._move_mode:
            self._notice = self._move_guidance()
            self._refresh()
            return
        if not self._has_changes():
            self.close(False)
            return
        ordered_ids = self._current_ids()
        current_id_set = set(ordered_ids)
        ordered_id_set = set(current_id_set)
        if not self._reset_requested:
            missing_budget = max(0, MAX_CATEGORIES_PER_SOURCE - len(current_id_set))
            retained_missing = 0
            current_positions = {category_id: index for index, category_id in enumerate(ordered_ids)}
            missing_blocks = []
            pending_missing = []
            previous_anchor = ''
            for stored_id in self._stored_preferences.order:
                if stored_id in ordered_id_set:
                    if pending_missing:
                        missing_blocks.append((previous_anchor, stored_id, list(pending_missing)))
                        pending_missing = []
                    previous_anchor = stored_id
                    continue
                if retained_missing < missing_budget:
                    pending_missing.append(stored_id)
                    retained_missing += 1
            if pending_missing:
                missing_blocks.append((previous_anchor, '', list(pending_missing)))
            prefix_missing = []
            missing_before = {}
            missing_after = {}
            for previous_id, next_id, missing_ids in missing_blocks:
                if previous_id and next_id:
                    if current_positions[previous_id] < current_positions[next_id]:
                        missing_before[next_id] = missing_ids
                    else:
                        missing_after[previous_id] = missing_ids
                elif next_id:
                    missing_before[next_id] = missing_ids
                elif previous_id:
                    missing_after[previous_id] = missing_ids
                else:
                    prefix_missing.extend(missing_ids)
            merged_ids = list(prefix_missing)
            for category_id in ordered_ids:
                merged_ids.extend(missing_before.get(category_id, ()))
                merged_ids.append(category_id)
                merged_ids.extend(missing_after.get(category_id, ()))
            ordered_ids = merged_ids
            ordered_id_set = set(ordered_ids)
        hidden_ids = sorted((category_id for category_id in self._hidden if category_id in current_id_set))
        if not self._reset_requested:
            missing_id_set = ordered_id_set.difference(current_id_set)
            hidden_ids = sorted(set(hidden_ids).union((category_id for category_id in self._stored_preferences.hidden if category_id in missing_id_set)))
        reset = self._reset_requested and tuple(ordered_ids) == self._provider_ids and (not hidden_ids)
        mode = 'provider' if reset else 'manual' if tuple(self._current_ids()) != self._initial_ids else self._stored_preferences.sort_mode
        result = {'done': threading.Event(), 'order': list(ordered_ids), 'hidden': list(hidden_ids), 'sort_mode': mode}
        self._save_result = result
        self._loading = True
        store, scope = (self.category_store, self.account_scope)

        def save():
            try:
                result['saved'] = store.reset(scope) if reset else store.save(scope, result['order'], result['hidden'], include_terms=(), exclude_terms=(), sort_mode=mode)
            except Exception:
                result['saved'] = False
            finally:
                result['done'].set()
        threading.Thread(target=save, daemon=True).start()
        self._timer.start(100, True)
        self._refresh()

    def toggle_mark(self):
        if self._loading:
            return
        if self._move_mode:
            return
        category_id = self._selected_id()
        if not category_id:
            self._notice = _('{} cannot be moved.').format(_(self._all_category_label))
        elif category_id in self._marked:
            self._marked.discard(category_id)
            self._notice = ''
        else:
            self._marked.add(category_id)
            self._notice = ''
        self._refresh()

class GTM3UChannelSearchScreen(_FavoriteSupport, GTAsyncListScreen):
    """Search the already prepared M3U catalogue without artwork or EPG I/O."""

    def __init__(self, session, account, client=None, favorite_store=None, hidden_category_ids=None):
        self.account = account
        self.client = client or content_client_for(account)
        self._query = ''
        self._keyboard_requested = False
        self._keyboard_timer = eTimer()
        _connect_timer(self._keyboard_timer, self.open_keyboard)
        self._init_favorite_support(favorite_store)
        self._hidden_category_ids = hidden_category_match_ids((), hidden_category_ids)
        self._footer_layout_items = M3U_SEARCH_FOOTER_ITEMS
        self._opaque_background = True
        GTAsyncListScreen.__init__(self, session, 'GTM3UChannelSearchScreen', N_('Search All Channels'), '{}  >  {}'.format(account.display_name, _('All Channels')))
        self['screen_label'].setText(_('M3U CHANNEL SEARCH'))
        self['actions'] = ActionMap(['OkCancelActions', 'DirectionActions', 'ColorActions'], {'ok': self.open_selected, 'cancel': self.close, 'up': self.move_up, 'down': self.move_down, 'upRepeated': self.move_up, 'downRepeated': self.move_down, 'left': self.page_up, 'right': self.page_down, 'leftRepeated': self.page_up, 'rightRepeated': self.page_down, 'red': self.toggle_favorite, 'blue': self.open_keyboard}, -1)
        if hasattr(self, 'onClose'):
            self.onClose.append(self._stop_search)

        setup_aux(self, 'channels', M3U_SEARCH_FOOTER_ITEMS)

    def _start_initial_load(self):
        if self._closed or self._keyboard_requested:
            return
        self._keyboard_requested = True
        self._keyboard_timer.start(40, True)

    def open_keyboard(self):
        if self._closed:
            return
        if self._loading:
            self['message'].setText(_('Please wait'))
            return
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self['message'].setText(_('The virtual keyboard is unavailable on this image.'))
            return
        opener = getattr(self.session, 'openWithCallback', None)
        if not callable(opener):
            self['message'].setText(_('Could not open the virtual keyboard.'))
            return
        opener(self._query_entered, VirtualKeyBoard, title=_('Search All Channels'), text=self._query)

    def _query_entered(self, value):
        if self._closed or value is None:
            return
        self._query = ' '.join(str(value or '').split())[:120]
        if not self._query:
            self._entries = []
            self._ready = True
            self._error = ''
            self._refresh()
            return
        self.start_load()

    def load_items(self):
        searcher = getattr(self.client, 'search_items', None)
        if not callable(searcher):
            return []
        if self._hidden_category_ids:
            try:
                return searcher(self._query, limit=M3U_SEARCH_RESULT_LIMIT, hidden_category_ids=self._hidden_category_ids)
            except TypeError:
                pass
        return filter_hidden_category_items(searcher(self._query, limit=M3U_SEARCH_RESULT_LIMIT), self._hidden_category_ids)

    def empty_message(self):
        if self._query:
            return N_('No channels matched this search.')
        return N_('Enter a channel name.')

    def item_text(self, item):
        prefix = '★ ' if self._is_favorite(item) else ''
        group = str(getattr(item, 'group_title', '') or '')
        suffix = '  •  {}'.format(group) if group else ''
        return '{}{}{}'.format(prefix, item.name, suffix)

    @aux_refresh_after
    def _refresh(self):
        GTAsyncListScreen._refresh(self)
        if self._entries and (not self._error):
            if len(self._entries) >= M3U_SEARCH_RESULT_LIMIT:
                message = _('First {} matching channels').format(M3U_SEARCH_RESULT_LIMIT)
            else:
                message = _('{} channels found').format(len(self._entries))
            self['message'].setText(message)

    def toggle_favorite(self):
        if not self._entries or self._loading:
            return
        item = self._entries[self.selected_index]
        added = self._toggle_favorite_item(item)
        self._refresh()
        ellipsize_dynamic_text(self['message'], self._favorite_message(item, added), fallback_chars=80)

    def open_selected(self):
        if not self._entries or self._loading:
            return
        from .browser_player import open_extplayer
        open_extplayer(self.session, self.client, self._entries[self.selected_index], self._entries, self.selected_index, owner=self)

    def _stop_search(self):
        try:
            self._keyboard_timer.stop()
        except Exception:
            pass

class _GTStreamListBase(GTAsyncListScreen):
    """Shared catalogue primitives; live browsing never owns a decoder."""

    def _run_load(self, generation=None, token=None):
        return content_sort_ui.live_worker(self, token, lambda: GTAsyncListScreen._run_load(self, generation, token))

    def refresh_streams(self):
        for name in ('_load_token', '_live_more_token'):
            _cancel_worker_token(getattr(self, name, None))
        self._loading = False
        invalidator = getattr(self.client, 'invalidate_cache', None)
        if invalidator is not None:
            try:
                invalidator(self.content_type, self.category.category_id)
            except Exception:
                pass
        self.start_load()

    def start_load(self):
        if self.content_type == 'live' and (not self._loading):
            _cancel_worker_token(getattr(self, '_live_more_token', None))
            self._live_catalog_generation += 1
            self._live_initial_applied = False
            self._live_has_more = False
            self._live_more_loading = False
            self._live_more_token = None
            self._live_more_results = {}
            self._live_pending_target_index = None
            self._live_empty_more_batches = 0
        GTAsyncListScreen.start_load(self)

    def load_items(self):
        entries = self.client.load_items(self.content_type, self.category.category_id)
        if self.content_type == 'live':
            checker = getattr(self.client, 'has_more_items', None)
            try:
                self._live_has_more = bool(checker is not None and checker('live', self.category.category_id))
            except Exception:
                self._live_has_more = False
        category_id = str(getattr(self.category, 'category_id', '') or '').strip()
        if not category_id or category_filter_active(getattr(self, '_hidden_category_ids', ())):
            return filter_hidden_category_items(entries, getattr(self, '_hidden_category_ids', ()), category_id)
        return entries

    def _poll_result(self):
        if self.content_type != 'live':
            GTAsyncListScreen._poll_result(self)
            return
        if self._closed:
            return
        if not self._ready:
            if self._load_token is not None and self._load_token.expired():
                self._load_token.cancel()
                self._loading = False
                self._error = N_('Request timed out')
                self._ready = True
            else:
                self._timer.start(150, True)
                return
        if not self._live_initial_applied:
            self._live_initial_applied = True
            self._loading = False
            self._load_token = None
            self._refresh()
            return
        if not self._live_more_loading:
            return
        generation = self._live_catalog_generation
        token = getattr(self, '_live_more_token', None)
        with self._live_more_worker_events_lock:
            result = self._live_more_results.pop((generation, token), None)
        if result is None:
            if token is None or token.expired():
                if token is not None:
                    _cancel_worker_token(token)
                    with self._live_more_worker_events_lock:
                        self._live_more_worker_tokens.discard(token)
                if getattr(self, '_live_more_token', None) is token:
                    self._live_more_token = None
                self._live_more_loading = False
                self._live_pending_target_index = None
                return
            self._timer.start(100, True)
            return
        entries, error, has_more = result
        self._live_more_loading = False
        self._live_more_token = None
        self._live_has_more = bool(has_more)
        if error:
            self._live_pending_target_index = None
            if not self._entries:
                self._error = error
            else:
                log_event('content', 'Could not load more live channels')
        else:
            existing = {str(getattr(item, 'stream_id', '') or '') for item in self._entries}
            appended = []
            for item in entries:
                stream_id = str(getattr(item, 'stream_id', '') or '')
                if not stream_id or stream_id in existing:
                    continue
                existing.add(stream_id)
                appended.append(item)
            self._entries.extend(appended)
            apply_order = getattr(self, '_apply_live_channel_order', None)
            if callable(apply_order):
                apply_order()
            if appended:
                self._live_empty_more_batches = 0
            else:
                self._live_empty_more_batches += 1
        target = self._live_pending_target_index
        if target is not None and self._entries:
            if target < len(self._entries):
                self.selected_index = target
                self._live_pending_target_index = None
            elif not self._live_has_more:
                self.selected_index = target % len(self._entries)
                self._live_pending_target_index = None
        self._refresh_live_streams()
        if self._live_pending_target_index is not None and self._live_has_more:
            self._start_live_more(force=True)

    def _run_live_more(self, generation, token=None):
        entries = []
        error = ''
        has_more = False
        try:
            loader = getattr(self.client, 'load_more_items', None)
            if loader is not None:
                _bounded_client_call(self.client, token, lambda: loader('live', self.category.category_id))
                entries = list(_bounded_client_call(self.client, token, lambda: self.client.load_items('live', self.category.category_id)) or [])
                if not str(getattr(self.category, 'category_id', '') or '').strip() or category_filter_active(getattr(self, '_hidden_category_ids', ())):
                    entries = filter_hidden_category_items(entries, getattr(self, '_hidden_category_ids', ()), getattr(self.category, 'category_id', ''))
            checker = getattr(self.client, 'has_more_items', None)
            has_more = bool(checker is not None and checker('live', self.category.category_id))
        except _WorkerCancelled:
            return
        except ContentError as caught:
            error = str(caught)
            checker = getattr(self.client, 'has_more_items', None)
            has_more = bool(checker is not None and checker('live', self.category.category_id))
        except Exception:
            error = N_('Could not load content')
        if token is not None:
            try:
                token.check()
            except _WorkerCancelled:
                return
        lock = getattr(self, '_live_more_worker_events_lock', None)
        if lock is None:
            self._live_more_results[generation, token] = (entries, error, has_more)
            return
        with lock:
            if token is not None and (not token.active() or getattr(self, '_live_more_token', None) is not token):
                return
            self._live_more_results[generation, token] = (entries, error, has_more)

    def _maybe_load_more_live(self):
        if getattr(self, '_channel_move', None) is not None:
            return
        if self._fullscreen_active:
            return
        if not self._entries:
            self._start_live_more()
            return
        remaining = len(self._entries) - self.selected_index - 1
        threshold = 2 if str(getattr(self.client, 'source_type', '') or '') == 'stalker' else max(6, int(self.page_size / 2))
        if remaining <= threshold:
            self._start_live_more()

    @staticmethod
    def _progress_details(event):
        start = int(getattr(event, 'start_timestamp', 0) or 0)
        end = int(getattr(event, 'end_timestamp', 0) or 0)
        if not start or end <= start:
            return (0.0, '')
        now = int(time.time())
        ratio = max(0.0, min(1.0, float(now - start) / (end - start)))
        remaining_minutes = max(0, int((end - now + 59) / 60))
        return (ratio, _('%{}  •  {} MIN REMAINING').format(int(round(ratio * 100)), remaining_minutes))

class _StalkerStreamProbe(object):
    """Observe one live start locally; never fetch, replay or change a service.

    Service events and decoder clocks can show a tune failure or advancing
    media, but they cannot prove that the TV displays non-black pixels. A
    missing clock also cannot prove a geographical block or a dead stream.
    """
    SAMPLE_DELAYS_MS = (550, 2000, 6000)
    EVENT_NAMES = ('evStart', 'evUpdatedInfo', 'evVideoSizeChanged', 'evTuneFailed', 'evEOF', 'evStopped')

    def __init__(self, navigation, reference, play_id, started_at=None, phase='player'):
        self.navigation = navigation
        self.reference = reference
        candidate_id = str(play_id or '').lower()
        self.play_id = candidate_id if re.fullmatch('[0-9a-f]{10}', candidate_id) else uuid.uuid4().hex[:10]
        self.phase = 'preview' if phase == 'preview' else 'player'
        self.started_at = started_at or time.monotonic()
        self.service_started_at = time.monotonic()
        from .browser_player import reference_service_type
        self.engine = reference_service_type(reference, 4097)
        self._sample_index = 0
        self._previous = None
        self._seen_events = set()
        self._closed = False
        self._events_attached = False
        self.timer = None

    def _log(self, detail):
        elapsed = max(0, int((time.monotonic() - self.started_at) * 1000))
        log_event('stream_diag', 'play_id={} phase={} engine={} elapsed_ms={} {} geo=unknown'.format(self.play_id, self.phase, self.engine, elapsed, detail))

    def start(self):
        try:
            events = getattr(self.navigation, 'event', None)
            if events is not None:
                events.append(self.service_event)
                self._events_attached = True
        except Exception:
            pass
        try:
            self.timer = eTimer()
            _connect_timer(self.timer, self._sample)
            self.timer.start(self.SAMPLE_DELAYS_MS[0], True)
        except Exception:
            self.stop()

    def stop(self):
        self._closed = True
        if self.timer is not None:
            try:
                self.timer.stop()
            except Exception:
                pass
        if self._events_attached:
            try:
                self.navigation.event.remove(self.service_event)
            except (AttributeError, ValueError):
                pass
            except Exception:
                pass
            self._events_attached = False

    def _reference_active(self):
        getter = getattr(self.navigation, 'getCurrentlyPlayingServiceReference', None)
        if not callable(getter):
            return False
        try:
            current = getter()
            if current is self.reference:
                return True
            if current is None or self.reference is None:
                return False
            current_text = current.toString()
            expected_text = self.reference.toString()
            return bool(current_text and current_text == expected_text)
        except Exception:
            return False

    def service_event(self, event):
        if self._closed or _PlayableServiceEvents is None or event is None:
            return
        if not self._reference_active():
            return
        for name in self.EVENT_NAMES:
            if event != getattr(_PlayableServiceEvents, name, None):
                continue
            if name in ('evTuneFailed', 'evEOF', 'evStopped') and time.monotonic() - self.service_started_at < 0.25:
                return
            if name not in self._seen_events:
                self._seen_events.add(name)
                self._log('stage=service-event event={}'.format(name))
            return
        base = getattr(_PlayableServiceEvents, 'evUser', None)
        if isinstance(base, int):
            for offset in (10, 11, 12):
                name = 'evUser+{}'.format(offset)
                if event == base + offset and name not in self._seen_events:
                    self._seen_events.add(name)
                    self._log('stage=service-event event={}'.format(name))
                    return

    def _sample(self):
        if self._closed:
            return
        index = self._sample_index
        if index >= len(self.SAMPLE_DELAYS_MS):
            self.stop()
            return
        self._sample_index += 1
        if not self._reference_active():
            self._log('stage=decoder outcome=reference-changed')
            self.stop()
            return
        getter = getattr(self.navigation, 'getCurrentService', None)
        try:
            service = getter() if callable(getter) else None
            snapshot = _decoder_media_snapshot(service, iServiceInformation)
        except Exception:
            snapshot = {}
        previous = self._previous or {}
        video = snapshot.get('video_pts')
        audio = snapshot.get('audio_pts')
        video_progress = bool(snapshot.get('video_pts_source') == previous.get('video_pts_source') and _stream_diag_pts_progress(previous.get('video_pts'), video))
        audio_progress = _stream_diag_pts_progress(previous.get('audio_pts'), audio)
        self._previous = snapshot
        width = snapshot.get('width')
        height = snapshot.get('height')
        tracks = snapshot.get('audio_tracks')
        geometry = '{}x{}'.format(width, height) if isinstance(width, int) and isinstance(height, int) and (0 < width <= 8192) and (0 < height <= 8192) else 'unknown'
        track_count = tracks if isinstance(tracks, int) and 0 <= tracks <= 64 else 'unknown'
        if video_progress:
            outcome = 'video-pts-advancing'
        elif audio_progress:
            outcome = 'audio-pts-advancing-video-unconfirmed'
        elif index == len(self.SAMPLE_DELAYS_MS) - 1:
            outcome = 'pts-unavailable' if video is None and audio is None else 'no-pts-progress-observed'
        else:
            outcome = 'waiting-for-media'
        self._log('stage=decoder checkpoint_ms={} outcome={} video_size={} audio_tracks={} pts_source={}'.format(self.SAMPLE_DELAYS_MS[index], outcome, geometry, track_count, snapshot.get('video_pts_source') or 'none'))
        if video_progress or index == len(self.SAMPLE_DELAYS_MS) - 1:
            self.stop()
            return
        next_delay = self.SAMPLE_DELAYS_MS[index + 1] - self.SAMPLE_DELAYS_MS[index]
        try:
            self.timer.start(next_delay, True)
        except Exception:
            self.stop()

class _LiveStartupRecoverySupport(object):
    """Compatibility hooks for the retired automatic live recovery.

    R93 deliberately leaves Stalker/MAC playback passive after every start,
    preview hand-off, channel change and engine change.  A live stream is
    renewed only through :meth:`GTExternalPlayerScreen.restart_live_stream`,
    which is bound to the physical 0 key.  Keeping these no-op hooks avoids a
    risky lifecycle rewrite in the list/player hand-off code while making it
    impossible to create a timer, worker, provider request or decoder replay.
    """
    _startup_preview = False

    def _arm_live_startup(self):
        self._live_startup_recovery = None

    def _cancel_live_startup(self, restore_released=False):
        del restore_released
        self._live_startup_recovery = None
        return True

    def _live_startup_status(self, status):
        del status

    def _live_startup_recovered(self, reference):
        del reference

class GTStreamListScreen(LiveProgrammePosterMixin, _LiveStartupRecoverySupport, _FavoriteSupport, _GTStreamListBase):
    """One live-list implementation for Xtream, Stalker and M3U sources."""
    _startup_preview = True

    def __init__(self, session, account, content_type, category, client=None, weather_forecast=None, favorite_store=None, hidden_category_ids=None, live_source_categories=None):
        self.account = account
        self.category = category
        self._source_categories = list(live_source_categories or [category])
        self.client = client or content_client_for(account)
        self.content_type = _validated_content_type(self.client, content_type)
        self._channel_move = None
        self._channel_order_store = LiveChannelOrderStore()
        self._sorting_live_lock = threading.RLock()
        self._bind_live_channel_order()
        self._init_favorite_support(favorite_store)
        self._hidden_category_ids = hidden_category_match_ids((), hidden_category_ids)
        self._weather_forecast_snapshot = weather_forecast
        self._fullscreen_active = False
        self._live_modal_active = False
        self._opaque_background = True
        self._screen_background_path = ''
        self._footer_show_web_hint = False
        self._active_live_service_type = None
        settings = None
        try:
            settings = load_player_settings()
            self._active_live_service_type = int(settings.service_type_for('live'))
            self._channel_highlight = normalize_channel_highlight(getattr(settings, 'channel_highlight', DEFAULT_CHANNEL_HIGHLIGHT))
        except Exception:
            self._active_live_service_type = 4097
            self._channel_highlight = DEFAULT_CHANNEL_HIGHLIGHT
        self._live_catalog_generation = 0
        self._live_initial_applied = False
        self._live_has_more = False
        self._live_more_loading = False
        self._live_more_token = None
        self._live_more_results = {}
        self._live_pending_target_index = None
        self._live_empty_more_batches = 0
        self._live_render_page_key = None
        self._live_render_selected_row = -1
        self._live_active_picon_signature = None
        self._live_active_picon_generation = 0
        self._live_active_picon_widget_owners = {}
        self._live_active_picon_fetch_generation = 0
        self._live_active_picon_fetch_item = None
        self._live_active_picon_fetch_key = ''
        self._live_active_picon_fetch_deadline = 0.0
        self._live_active_picon_fetch_state = None
        self._live_active_picon_reload_pending = False
        self._live_row_picon_generation = 0
        self._live_row_picon_signature = None
        self._live_selected_picon_generation = 0
        self._live_selected_picon_signature = None
        self._live_picon_prefetch_generation = 0
        self._live_picon_prefetch_lock = threading.RLock()
        self._live_picon_prefetch_queue = deque()
        self._live_picon_prefetch_jobs = {}
        self._live_picon_prefetch_known = set()
        self._live_picon_prefetch_catalog = None
        self._live_picon_prefetch_catalog_count = 0
        self._live_picon_prefetch_catalog_epoch = None
        self._live_picon_prefetch_priority = None
        self._live_picon_prefetch_completed = set()
        self._live_picon_prefetch_tokens = set()
        self._live_picon_prefetch_worker_counts = {}
        self._init_programme_poster(eTimer, _connect_timer, settings)
        self._preview_item = None
        self._preview_epg_events = []
        self._preview_epg_loaded_at = 0.0
        self._detail_picon_path = ''
        self._detail_picon_loaded_at = 0.0
        self._detail_selection_key = None
        self._detail_page_key = None
        self._detail_generation = 0
        self._detail_deadline = 0.0
        self._detail_selection_started_at = 0.0
        self._detail_epg_retry_count = 0
        self._detail_epg_retry_key = ''
        self._detail_epg_retry_deadline = 0.0
        self._detail_results = {}
        self._detail_results_lock = threading.Lock()
        self._detail_store = {}
        self._detail_store_lock = threading.Lock()
        self._detail_store_epoch = 0
        self._live_page_epg_generation = 0
        self._live_page_epg_signature = None
        self._live_page_epg_pending = ()
        self._live_page_epg_state = None
        self._live_page_epg_worker_lock = threading.Lock()
        self._detail_foreground_state = None
        self._detail_prefetch_state = None
        self._detail_prefetch_deadline = 0.0
        self._live_more_worker_tokens = set()
        self._live_more_worker_events_lock = threading.Lock()
        self._pre_list_reference = None
        self._pre_list_reference_captured = False
        self._pre_list_restored = False
        self._playing_preview_item = None
        self._playing_preview_key = ''
        self._playing_preview_index = -1
        self._playing_preview_reference = None
        self._preview_service_active = False
        self._preview_service_started_at = 0.0
        self._preview_target_item = None
        self._preview_target_key = ''
        self._preview_target_index = -1
        self._preview_generation = 0
        self._preview_link_lock = threading.RLock()
        self._preview_link_pending = False
        self._preview_link_result = None
        self._preview_link_deadline = 0.0
        self._preview_link_token = None
        self._preview_fullscreen_requested = False
        self._stream_diag_play_id = ''
        self._stream_diag_started_at = 0.0
        self._stream_diag_probe = None
        self._preview_video_attempts = 0
        self._preview_video_geometry = None
        self._preview_fallback_visible = True
        self._preview_auto_suspended_key = ''
        self._fullscreen_link_snapshot = None
        self._detail_dwell_timer = eTimer()
        self._detail_epg_retry_timer = eTimer()
        self._detail_poll_timer = eTimer()
        self._detail_prefetch_timer = eTimer()
        self._detail_progress_timer = eTimer()
        self._summary_scroll_timer = eTimer()
        self._summary_text = ''
        self._summary_selection_key = None
        self._summary_lines = ()
        self._summary_line_offset = 0
        self._live_clock_minute = ''
        self._epg_clock_timer = eTimer()
        self._live_page_epg_dwell_timer = eTimer()
        self._live_page_epg_poll_timer = eTimer()
        self._preview_dwell_timer = eTimer()
        self._preview_link_timer = eTimer()
        self._preview_video_timer = eTimer()
        self._live_active_picon_timer = eTimer()
        self._live_active_picon_poll_timer = eTimer()
        self._live_picon_prefetch_poll_timer = eTimer()
        _connect_timer(self._detail_dwell_timer, self._detail_dwell_fired)
        _connect_timer(self._detail_epg_retry_timer, self._selected_epg_retry_fired)
        _connect_timer(self._detail_poll_timer, self._detail_poll)
        _connect_timer(self._detail_prefetch_timer, self._detail_prefetch_fired)
        _connect_timer(self._detail_progress_timer, self._detail_progress_tick)
        _connect_timer(self._summary_scroll_timer, self._summary_scroll_tick)
        _connect_timer(self._epg_clock_timer, self._epg_clock_tick)
        _connect_timer(self._live_page_epg_dwell_timer, self._live_page_epg_start)
        _connect_timer(self._live_page_epg_poll_timer, self._live_page_epg_poll)
        _connect_timer(self._preview_dwell_timer, self._preview_dwell_fired)
        _connect_timer(self._preview_link_timer, self._preview_link_tick)
        _connect_timer(self._preview_video_timer, self._preview_video_tick)
        _connect_timer(self._live_active_picon_timer, self._live_active_picon_fired)
        _connect_timer(self._live_active_picon_poll_timer, self._live_active_picon_poll)
        _connect_timer(self._live_picon_prefetch_poll_timer, self._live_picon_prefetch_poll)
        self._footer_layout_items = LIVE_CHANNEL_ORDER_FOOTER_ITEMS if self._channel_move_enabled else LIVE_STREAM_FOOTER_ITEMS
        self._footer_layout_alternate_items = LIVE_CHANNEL_MOVE_FOOTER_ITEMS if self._channel_move_enabled else ()
        self._footer_transparent_panel = True
        self._footer_show_dividers = True
        self._footer_side_padding = 16
        self.skin = prepared_skin(_live_stream_skin, self._channel_highlight)
        self.page_size = LIVE_STREAM_PAGE_SIZE
        width, height, px = _scale()
        self._ribbon_now_preferred_font = font_px(px, 29, role='title')
        self._ribbon_now_min_font = font_px(px, 25, role='title', value='standard')
        self._ribbon_next_preferred_font = font_px(px, 24, role='title')
        self._ribbon_next_min_font = font_px(px, 21, role='title', value='standard')
        side_geometry = live_guide_side_geometry(width, height, px)
        self._list_scroll_geometry = side_geometry['list_scrollbar']
        self._live_channel_name_font = font_px(px, {'standard': 24, 'large': 26, 'very_large': 28}[current_text_size()], value='standard', max_height=64, vertical_padding=4)
        self._live_channel_name_min_font = font_px(px, 18, value='standard', max_height=64, vertical_padding=4)
        self._summary_scrollbar_geometry = side_geometry['summary_scrollbar']
        unused_x, unused_y, summary_width, summary_height = side_geometry['summary']
        self._summary_font_size = font_px(px, 23, vertical_padding=5)
        self._summary_text_width = max(1, summary_width - px(2))
        self._summary_line_capacity = max(8, int(self._summary_text_width / max(1, self._summary_font_size * 0.58)))
        self._summary_visible_lines = max(1, int(summary_height / max(1, self._summary_font_size * 1.22)))
        title = CONTENT_LABELS[self.content_type][0]
        GTAsyncListScreen.__init__(self, session, 'GTStreamListScreen', title, '{}  >  {}'.format(account.display_name, category.name))
        self._fullscreen_osd_trace = None
        self._preview_fullscreen_pending = None
        self._preview_fullscreen_surface_hidden = False
        self._preview_fullscreen_ready_at = 0.0
        self._preview_fullscreen_timer = eTimer()
        _connect_timer(self._preview_fullscreen_timer, self._finish_preview_fullscreen)
        self._capture_pre_list_service()
        self._setup_live_widgets(px)
        self['header'].setText('GT IPTV PLAYER PRO  •  {}'.format(localized_upper(_('LIVE TV'))))
        self['actions'] = ActionMap(['OkCancelActions', 'DirectionActions', 'ColorActions', 'MoviePlayerActions'], {name: self._live_list_action(name, action) for name, action in {'ok': self.open_selected, 'cancel': self.close_list, 'stop': self.close_list, 'up': self.move_up, 'down': self.move_down, 'upRepeated': self.move_up, 'downRepeated': self.move_down, 'left': self.page_up, 'right': self.page_down, 'leftRepeated': self.page_up, 'rightRepeated': self.page_down, 'green': self.refresh_streams, 'red': self.toggle_selected_favorite, 'blue': self.open_channel_search, 'yellow': self.open_content_edit}.items()}, -1)
        if hasattr(self, 'onShown'):
            self.onShown.append(self._restore_preview_fullscreen_surface)
            self.onShown.append(self.start_epg_clock)
            self.onShown.append(self._favorite_stream_list_shown)
        if hasattr(self, 'onLayoutFinish'):
            self.onLayoutFinish.append(self._preview_layout_ready)
            self.onLayoutFinish.append(self._refresh_summary_scroll_indicator)
            self.onLayoutFinish.append(self._refit_live_summary)
            self.onLayoutFinish.append(self._refit_live_channel_names)
        setup_broadcast(self, 'live', self._footer_layout_items)

    def show(self):
        if getattr(self, '_closed', False) or getattr(self, '_preview_fullscreen_surface_hidden', False):
            _trace_fullscreen_osd(self, 'list-show-blocked')
            return
        _trace_fullscreen_osd(self, 'list-show-before')
        Screen.show(self)
        _trace_fullscreen_osd(self, 'list-show-after')

    def hide(self):
        _trace_fullscreen_osd(self, 'list-hide-before')
        Screen.hide(self)
        _trace_fullscreen_osd(self, 'list-hide-after')

    def _live_list_action(self, name, action):

        def dispatch():
            if self._preview_fullscreen_pending is not None:
                if name == 'ok':
                    return
                self._cancel_preview_fullscreen()
                if name in ('cancel', 'stop'):
                    return
            return action()
        return dispatch

    def _cancel_preview_fullscreen(self):
        self._preview_fullscreen_timer.stop()
        self._preview_fullscreen_pending = None
        self._fullscreen_active = False
        self._fullscreen_link_snapshot = None
        self._restore_preview_fullscreen_surface()

    def _restore_preview_fullscreen_surface(self):
        if not self._preview_fullscreen_surface_hidden or self._closed or self._fullscreen_active or (getattr(self.session, 'current_dialog', None) is not self):
            return
        reference = self._current_navigation_reference()
        kept = self._preview_service_active and self._same_service_reference(reference, self._playing_preview_reference)
        self._fullscreen_closed({'kind': LIVE_FULLSCREEN_RESULT_KIND, 'kept': bool(kept), 'reference': reference if kept else None, 'selected_index': self.selected_index, 'channel_item': self._playing_preview_item if kept else None})

    def _show_preview_fullscreen_list(self):
        if not self._preview_fullscreen_surface_hidden or self._closed or self._fullscreen_active or (getattr(self.session, 'current_dialog', None) is not self):
            return
        if self._preview_service_active:
            self._resume_preview_pig()
        self._preview_fullscreen_surface_hidden = False
        self.show()

    def _finish_preview_fullscreen(self):
        self._preview_fullscreen_timer.stop()
        pending = self._preview_fullscreen_pending
        if pending is None:
            return
        item, index, reference, recovery, selection_key = pending
        if self._closed:
            self._preview_fullscreen_pending = None
            return
        if getattr(self.session, 'current_dialog', None) is not self or not 0 <= index < len(self._entries) or index != self.selected_index or (self._selection_key(self._entries[index]) != selection_key) or (not self._same_service_reference(self._current_navigation_reference(), reference)):
            self._cancel_preview_fullscreen()
            return
        remaining = self._preview_fullscreen_ready_at - time.monotonic()
        if remaining > 0:
            try:
                self._preview_fullscreen_timer.start(max(1, int(remaining * 1000) + 1), True)
            except Exception:
                _trace_fullscreen_osd(self, 'list-timer-rearm-failed')
                self._cancel_preview_fullscreen()
            return
        self._preview_fullscreen_pending = None
        self._suspend_preview_pig()
        self._open_preview_fullscreen_player(item, index, reference, recovery)
        if not self._fullscreen_active:
            self._show_preview_fullscreen_list()

    def open_content_edit(self):
        return content_sort_ui.open_edit(self)

    def _bind_live_channel_order(self):
        source = str(getattr(self.client, 'source_type', 'xtream') or 'xtream').lower()
        self._channel_move_enabled = self.content_type == 'live' and source in ('xtream', 'stalker')
        self._channel_order_scope = channel_order_scope(resume_account_scope(self.client), getattr(self.category, 'category_id', '')) if self._channel_move_enabled else ''
        self._channel_move_enabled = bool(self._channel_order_scope)
        self._channel_order = self._channel_order_store.load(self._channel_order_scope) if self._channel_move_enabled else ()

    def load_items(self):
        return content_sort_ui.load_live(self, _GTStreamListBase.load_items(self))

    def _reindex_live_preview(self):
        for key_name, index_name in (('_playing_preview_key', '_playing_preview_index'), ('_preview_target_key', '_preview_target_index')):
            key = getattr(self, key_name, '')
            if key:
                setattr(self, index_name, next((i for i, item in enumerate(self._entries) if self._selection_key(item) == key), -1))

    def _apply_live_channel_order(self):
        if self._channel_move is not None or not self._entries:
            return
        selected = self._selection_key(self._entries[self.selected_index])
        content_sort_ui.apply_live_order(self)
        self.selected_index = next((i for i, item in enumerate(self._entries) if self._selection_key(item) == selected), 0)
        self._reindex_live_preview()

    @staticmethod
    def _channel_move_guidance():
        return _('Move channel')

    def start_channel_move(self):
        if not self._channel_move_enabled or self._channel_move is not None or (not self._entries) or self._loading or self._fullscreen_active or self._live_modal_active:
            return
        with self._preview_link_lock:
            self._preview_generation += 1
        self._cancel_preview_resolution(clear_target=True)
        self._live_pending_target_index = None
        self._channel_move = ChannelMove(self._entries, self.selected_index)
        set_remote_footer(self, LIVE_CHANNEL_MOVE_FOOTER_ITEMS)
        self._refresh_live_streams()

    def finish_channel_move(self):
        if self._channel_move is None:
            return
        order = content_sort_ui.save_manual(self)
        if order is None:
            self.session.open(MessageBox, _('Could not save channel order.'), type=MessageBox.TYPE_ERROR)
            return
        self._channel_order = order
        self._channel_move = None
        self._reindex_live_preview()
        set_remote_footer(self, self._footer_layout_items)
        self._refresh_live_streams()
        fit_dynamic_text(self['list_caption'], _('Channel moved.'), max_lines=1)

    def cancel_channel_move(self):
        if self._channel_move is None:
            return
        self.selected_index = self._channel_move.cancel(self._entries)
        self._channel_move = None
        self._reindex_live_preview()
        set_remote_footer(self, self._footer_layout_items)
        self._refresh_live_streams()
        fit_dynamic_text(self['list_caption'], _('Move cancelled.'), max_lines=1)

    def _setup_live_widgets(self, px):
        self['root_fill'] = Label('')
        self['left_panel'] = Label('')
        self['center_panel'] = Label('')
        self['right_panel'] = Label('')
        self['footer_backdrop'] = Label('')
        self['right_separator_bottom'] = Label('')
        self['clock_time'] = Label('')
        self['clock_date'] = Label('')
        self['detail_picon'] = Pixmap()
        self['detail_picon'].hide()
        self['detail_programme_poster'] = Pixmap()
        self['detail_programme_poster'].hide()
        self['left_panel_art'] = Pixmap()
        self['right_panel_art'] = Pixmap()
        for panel in ('left', 'right'):
            for color in ('cyan', 'magenta'):
                self['{}_panel_accent_{}'.format(panel, color)] = Label('')
        self['list_caption'] = Label(_('CHANNEL LIST'))
        self['list_caption_accent'] = Label('')
        for index in range(LIVE_STREAM_PAGE_SIZE):
            self['stream_row_bg_{}'.format(index)] = Label('')
            self['stream_row_bg_{}'.format(index)].hide()
            self['stream_row_art_{}'.format(index)] = Pixmap()
            self['stream_row_art_{}'.format(index)].hide()
            self['stream_focus_art_{}'.format(index)] = Pixmap()
            self['stream_focus_art_{}'.format(index)].hide()
            self['stream_picon_{}'.format(index)] = Pixmap()
            self['stream_picon_{}'.format(index)].hide()
            self['stream_number_{}'.format(index)] = Label('')
            self['stream_name_{}'.format(index)] = Label('')
            self['stream_program_{}'.format(index)] = Label('')
            self['stream_progress_bg_{}'.format(index)] = Label('')
            self['stream_progress_fill_{}'.format(index)] = Label('')
            self['stream_progress_bg_{}'.format(index)].hide()
            self['stream_progress_fill_{}'.format(index)].hide()
            self['stream_separator_{}'.format(index)] = Label('')
            self['stream_separator_{}'.format(index)].hide()
            self['stream_favorite_{}'.format(index)] = Pixmap()
            self['stream_favorite_{}'.format(index)].hide()
            for edge in ('fill', 'top', 'bottom', 'left', 'right'):
                self['stream_focus_{}_{}'.format(index, edge)] = Label('')
        self['epg_ribbon'] = Label('')
        self['ribbon_now_caption'] = Label(_('NOW'))
        self['ribbon_channel'] = Label(_('SELECT A CHANNEL'))
        self['ribbon_now_title'] = Label(_('Waiting for programme information'))
        self['ribbon_now_time'] = Label('--:--')
        self['ribbon_divider'] = Label('')
        self['ribbon_next_caption'] = Label(_('NEXT'))
        self['ribbon_clock'] = Label('')
        self['ribbon_next_title'] = Label('')
        self['ribbon_next_time'] = Label('')
        self['preview_frame'] = Pixmap()
        if VideoWindow is not None:
            self['preview_video'] = VideoWindow(decoder=0, fb_width=_desktop_size()[0], fb_height=_desktop_size()[1])
        else:
            self['preview_video'] = Label('')
        self['preview_mask'] = Label('')
        self['preview_hint'] = Label(_('SELECT A CHANNEL'))
        self['epg_panel'] = Label('')
        self['summary_panel_art'] = Pixmap()
        self['summary_separator'] = Label('')
        self['epg_title'] = Label(_('NEXT'))
        for row in ('now', 'next', 'third', 'fourth', 'fifth'):
            self['epg_{}_time'.format(row)] = Label('')
            self['epg_{}_title'.format(row)] = Label('')
            self['epg_{}_status'.format(row)] = Label('')
        self['epg_now_time'].hide()
        self['epg_now_title'].hide()
        self['epg_now_status'].hide()
        self['epg_progress_bg'] = Label('')
        self['epg_progress_fill'] = Label('')
        self['epg_progress'] = Label('')
        self['summary_caption'] = Label(_('PROGRAMME SUMMARY'))
        self['summary'] = Label(_('Waiting for programme information'))
        self['summary_scroll'] = Label('')
        self['summary_scrollbar_track'] = Label('')
        self['summary_scrollbar_thumb'] = Label('')
        hide_scrollbar(self, 'summary_scrollbar')
        output_format = 'M3U' if _is_m3u_client(self.client) else str(getattr(self.account, 'output_format', 'ts') or 'ts').upper()
        self['output_badge'] = Label(output_format)
        self['engine_badge'] = Label(service_engine_label(self._active_live_service_type))
        self._progress_width = max(1, px(362))
        self._progress_height = max(1, px(10))
        self._row_progress_width = max(1, px(96))
        self._row_progress_height = max(1, px(10))
        self._clear_epg_progress()
        self._clear_live_active_picon()
        self._show_preview_fallback(_('SELECT A CHANNEL'))
        self._update_epg_clock()

    @staticmethod
    def _same_service_reference(left, right):
        if left is None or right is None:
            return False
        if left is right:
            return True
        left_method = getattr(left, 'toString', None)
        right_method = getattr(right, 'toString', None)
        if left_method is not None and right_method is not None:
            try:
                return left_method() == right_method()
            except Exception:
                pass
        return False

    def _current_navigation_reference(self):
        navigation = getattr(self.session, 'nav', None)
        getter = getattr(navigation, 'getCurrentlyPlayingServiceReference', None)
        if getter is None:
            return None
        try:
            return getter()
        except Exception:
            return None

    def _capture_pre_list_service(self):
        if self._pre_list_reference_captured:
            return
        self._pre_list_reference = self._current_navigation_reference()
        self._pre_list_reference_captured = True

    def _preview_layout_ready(self):
        self._capture_preview_video_geometry()
        if self._preview_service_active:
            self._resume_preview_pig()
        else:
            self._show_preview_fallback(_('SELECT A CHANNEL'))

    def _capture_preview_video_geometry(self):
        if VideoWindow is None:
            return
        try:
            instance = self['preview_video'].instance
            position = instance.position()
            size = instance.size()
            width = int(size.width())
            height = int(size.height())
            if width > 0 and height > 0:
                self._preview_video_geometry = (int(position.x()), int(position.y()), width, height)
        except Exception:
            pass

    def _show_preview_fallback(self, message=None):
        self._preview_fallback_visible = True
        try:
            self['preview_mask'].show()
            self['preview_hint'].setText(str(message or ''))
            self['preview_hint'].show()
        except Exception:
            pass

    def _hide_preview_fallback(self):
        self._preview_fallback_visible = False
        try:
            self['preview_mask'].hide()
            self['preview_hint'].hide()
        except Exception:
            pass

    def _reapply_preview_video_window(self):
        """Force decoder 0 back into this screen's PIG rectangle."""
        if VideoWindow is None:
            return
        try:
            video = self['preview_video']
            instance = video.instance
            geometry = self._preview_video_geometry
            if geometry is None:
                self._capture_preview_video_geometry()
                geometry = self._preview_video_geometry
            if geometry is None:
                position = instance.position()
                size = instance.size()
                geometry = (int(position.x()), int(position.y()), int(size.width()), int(size.height()))
            left, top, width, height = geometry
            instance.resize(eSize(width, height))
            instance.move(ePoint(left + 1, top))
            instance.move(ePoint(left, top))
        except Exception:
            try:
                video.hide()
                video.show()
            except Exception:
                pass

    def _preview_video_ready(self):
        navigation = getattr(self.session, 'nav', None)
        getter = getattr(navigation, 'getCurrentService', None)
        try:
            service = getter() if getter is not None else None
        except Exception:
            service = None
        try:
            info = service.info() if service is not None else None
        except Exception:
            info = None
        width = -1
        height = -1
        if info is not None:
            try:
                width = int(info.getInfo(iServiceInformation.sVideoWidth))
            except Exception:
                width = -1
            try:
                height = int(info.getInfo(iServiceInformation.sVideoHeight))
            except Exception:
                height = -1
        if width <= 0:
            width = _read_proc_number('/proc/stb/vmpeg/0/xres', 16)
        if height <= 0:
            height = _read_proc_number('/proc/stb/vmpeg/0/yres', 16)
        return width > 0 and height > 0

    def _resume_preview_pig(self):
        if not self._preview_service_active or self._fullscreen_active:
            return
        try:
            self['preview_video'].show()
        except Exception:
            pass
        self._preview_video_attempts = 0
        self._show_preview_fallback(_('Please wait'))
        self._reapply_preview_video_window()
        self._preview_video_timer.start(LIVE_PREVIEW_VIDEO_INITIAL_MS, True)

    def _suspend_preview_pig(self):
        try:
            self._preview_video_timer.stop()
        except Exception:
            pass
        if VideoWindow is not None:
            try:
                self._capture_preview_video_geometry()
                video = self['preview_video']
                instance = video.instance
                restore = getattr(instance, 'restoreFullsize', None)
                if callable(restore):
                    restore()
                desktop_width, desktop_height = _desktop_size()
                instance.resize(eSize(desktop_width, desktop_height))
                instance.move(ePoint(0, 0))
            except Exception:
                pass
        try:
            self['preview_video'].hide()
        except Exception:
            pass

    def _preview_video_tick(self):
        if self._closed or self._fullscreen_active or (not self._preview_service_active):
            return
        if VideoWindow is None:
            self._show_preview_fallback(_('Could not open the stream.'))
            return
        current = self._current_navigation_reference()
        active = self._same_service_reference(current, self._playing_preview_reference)
        if active:
            self._reapply_preview_video_window()
            self._ensure_live_active_picon()
        if active and self._preview_video_ready():
            self._hide_preview_fallback()
            return
        self._preview_video_attempts += 1
        if self._preview_video_attempts == LIVE_PREVIEW_VIDEO_ERROR_CHECKS:
            self._show_preview_fallback(_('Could not open the stream.'))
        if self._preview_video_attempts < LIVE_PREVIEW_VIDEO_MAX_CHECKS:
            self._preview_video_timer.start(LIVE_PREVIEW_VIDEO_RETRY_MS, True)
            return

    def _favorite_stream_list_shown(self):
        if self._closed or self._fullscreen_active:
            return
        if self._favorite_screen_shown() and self._entries:
            self._live_render_page_key = None
            self._refresh_live_streams()

    def toggle_selected_favorite(self):
        if self._channel_move is not None:
            return
        if not self._entries or self._loading or self._fullscreen_active:
            return
        item = self._entries[self.selected_index]
        added = self._toggle_favorite_item(item)
        self._live_render_page_key = None
        self._refresh_live_streams()
        ellipsize_dynamic_text(self['message'], self._favorite_message(item, added), fallback_chars=60)

    def open_channel_search(self):
        if self._channel_move is not None:
            return
        if not _is_m3u_client(self.client) or not _client_supports(self.client, 'search') or self._fullscreen_active:
            return
        with self._preview_link_lock:
            self._preview_generation += 1
        self._cancel_preview_resolution(clear_target=True)
        self._live_modal_active = True
        self._cancel_live_page_epg()
        cancel_prefetch = getattr(self, '_cancel_live_picon_prefetch', None)
        if callable(cancel_prefetch):
            cancel_prefetch(clear_render=True)
        self._suspend_preview_pig()
        self._cancel_selected_detail(clear_selection=False)
        try:
            self._epg_clock_timer.stop()
        except Exception:
            pass
        opener = getattr(self.session, 'openWithCallback', None)
        if callable(opener):
            opener(self._m3u_search_closed, GTM3UChannelSearchScreen, self.account, self.client, self.favorite_store, self._hidden_category_ids)
            return
        dialog = self.session.open(GTM3UChannelSearchScreen, self.account, self.client, self.favorite_store, self._hidden_category_ids)
        callbacks = getattr(dialog, 'onClose', None)
        if isinstance(callbacks, list):
            callbacks.append(self._m3u_search_closed)

    def _m3u_search_closed(self, *args):
        if self._closed:
            return
        self._live_modal_active = False
        self._reload_favorite_keys()
        self._live_render_page_key = None
        self._refresh_live_streams()
        self.start_epg_clock()

    def refresh_streams(self):
        if self._channel_move is not None:
            return
        if self._hidden_category_ids and (not str(getattr(self.category, 'category_id', '') or '').strip()) and (not self._entries) and self._live_has_more and (self._live_empty_more_batches >= 3) and _is_stalker_client(self.client):
            return self._start_live_more(force=True)
        self._live_active_picon_reload_pending = bool(getattr(self, '_preview_service_active', False))
        self._cancel_live_active_picon_fetch(clear_target=True)
        self._clear_live_active_picon()
        self._cancel_selected_detail(clear_selection=True)
        with self._detail_store_lock:
            self._detail_store_epoch += 1
            self._detail_store.clear()
        state = _client_epg_state(self.client)
        if state is not None:
            with state['lock']:
                state['epoch'] = int(state.get('epoch', 0) or 0) + 1
                state['cache'].clear()
                for pending in state['inflight'].values():
                    pending['event'].set()
                state['inflight'].clear()
        return _GTStreamListBase.refresh_streams(self)

    def start_load(self):
        content_sort_ui.cancel(self)
        content_sort_ui.release_live(self)
        if getattr(self, '_preview_service_active', False):
            self._live_active_picon_reload_pending = True
        with self._preview_link_lock:
            self._preview_generation += 1
        self._cancel_preview_resolution(clear_target=True)
        self._cancel_live_active_picon_fetch(clear_target=True)
        cancel_prefetch = getattr(self, '_cancel_live_picon_prefetch', None)
        if callable(cancel_prefetch):
            cancel_prefetch(clear_render=True)
        self._cancel_live_page_epg()
        self._cancel_selected_detail(clear_selection=True)
        with self._live_more_worker_events_lock:
            tokens = tuple(self._live_more_worker_tokens)
        for token in tokens:
            _cancel_worker_token(token)
        return _GTStreamListBase.start_load(self)

    def _refresh(self):
        self._refresh_live_streams()
        if self._live_active_picon_reload_pending:
            self._live_active_picon_reload_pending = not bool(self._ensure_live_active_picon())

    def _set_live_channel_name(self, row_index, value):
        return fit_dynamic_text(self['stream_name_{}'.format(row_index)], value, max_lines=2, preferred_size=self._live_channel_name_font, min_size=self._live_channel_name_min_font)

    def _refit_live_channel_names(self):
        if self._closed or not self._entries:
            return
        page_start = int(self.selected_index / self.page_size) * self.page_size
        for row_index, item in enumerate(self._entries[page_start:page_start + self.page_size]):
            self._set_live_channel_name(row_index, str(item.name or ''))

    @broadcast_refresh_after
    def _refresh_live_streams(self):
        if self._closed or self._fullscreen_active:
            self._live_render_page_key = None
            return
        fit_dynamic_text(self['list_caption'], self._channel_move_guidance() if self._channel_move is not None else _('CHANNEL LIST'), max_lines=1)
        if self._error:
            ellipsize_dynamic_text(self['message'], _(self._error), fallback_chars=80)
            self._clear_live_stream_rows()
            hide_scrollbar(self, 'list_scroll')
            return
        if not self._entries:
            self['message'].setText(_('No channels found; press GREEN to try again.'))
            self._clear_live_stream_rows()
            hide_scrollbar(self, 'list_scroll')
            self._maybe_load_more_live()
            return
        self.selected_index %= len(self._entries)
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_items = self._entries[page_start:page_start + self.page_size]
        page_key = (page_start, tuple(((self._selection_key(item), str(getattr(item, 'name', '') or ''), self._is_favorite(item)) for item in page_items)))
        selected_row = self.selected_index - page_start
        page_changed = page_key != self._live_render_page_key
        if page_changed:
            plain_lines = []
            for row_index in range(self.page_size):
                present = row_index < len(page_items)
                if present:
                    item = page_items[row_index]
                    absolute_index = page_start + row_index
                    self['stream_row_bg_{}'.format(row_index)].show()
                    self['stream_separator_{}'.format(row_index)].show()
                    self['stream_number_{}'.format(row_index)].setText('{:04d}'.format(absolute_index + 1))
                    rendered_name = self._set_live_channel_name(row_index, str(item.name or ''))
                    plain_lines.append(rendered_name)
                    if self._is_favorite(item):
                        self['stream_favorite_{}'.format(row_index)].show()
                    else:
                        self['stream_favorite_{}'.format(row_index)].hide()
                else:
                    self['stream_row_bg_{}'.format(row_index)].hide()
                    self['stream_separator_{}'.format(row_index)].hide()
                    self['stream_number_{}'.format(row_index)].setText('')
                    self['stream_name_{}'.format(row_index)].setText('')
                    self['stream_program_{}'.format(row_index)].setText('')
                    self['stream_progress_bg_{}'.format(row_index)].hide()
                    self['stream_progress_fill_{}'.format(row_index)].hide()
                    self['stream_row_art_{}'.format(row_index)].hide()
                    self['stream_favorite_{}'.format(row_index)].hide()
            self['items'].setText('\n'.join(plain_lines))
        if page_changed or selected_row != self._live_render_selected_row:
            affected_rows = range(self.page_size) if page_changed else (self._live_render_selected_row, selected_row)
            for row_index in affected_rows:
                if row_index < 0 or row_index >= self.page_size:
                    continue
                selected = row_index == selected_row
                self['stream_focus_art_{}'.format(row_index)].hide()
                for edge in ('fill', 'top', 'bottom', 'left', 'right'):
                    focus = self['stream_focus_{}_{}'.format(row_index, edge)]
                    if selected and row_index < len(page_items):
                        focus.show()
                    else:
                        focus.hide()
        self._live_render_page_key = page_key
        self._live_render_selected_row = selected_row
        self._refresh_live_row_epg(page_start, page_items)
        self._schedule_live_page_epg(page_start, page_items)
        self._queue_live_category_picons(page_start, page_items)
        self._refresh_live_active_picon(page_start=page_start, page_items=page_items)
        self._refresh_selected_picon()
        self['message'].setText('{} / {}'.format(self.selected_index + 1, len(self._entries)))
        update_scrollbar(self, 'list_scroll', len(self._entries), self.selected_index, self.page_size, self._list_scroll_geometry)
        self._schedule_selected_detail()
        self._schedule_preview_for_selection()
        self._maybe_load_more_live()
        self._prefetch_remaining_live_catalog()

    def _prefetch_remaining_live_catalog(self):
        """Read only enough catalogue rows for the visible and next page."""
        if self._channel_move is not None or self._closed or self._fullscreen_active or getattr(self, '_live_modal_active', False) or self._loading or self._live_more_loading or (not self._live_has_more) or (self._live_empty_more_batches >= 3):
            return False
        page_start = self.selected_index // self.page_size * self.page_size
        if len(self._entries) >= page_start + self.page_size * 2:
            return False
        return self._start_live_more(force=True)

    def _clear_live_stream_rows(self):
        self._clear_live_active_picon()
        self._clear_live_visible_picons()
        self._clear_live_selected_picon()
        self._live_render_page_key = None
        self._live_render_selected_row = -1
        for row_index in range(LIVE_STREAM_PAGE_SIZE):
            self['stream_number_{}'.format(row_index)].setText('')
            self['stream_name_{}'.format(row_index)].setText('')
            self['stream_program_{}'.format(row_index)].setText('')
            self['stream_row_bg_{}'.format(row_index)].hide()
            self['stream_separator_{}'.format(row_index)].hide()
            self['stream_progress_bg_{}'.format(row_index)].hide()
            self['stream_progress_fill_{}'.format(row_index)].hide()
            self['stream_row_art_{}'.format(row_index)].hide()
            self['stream_focus_art_{}'.format(row_index)].hide()
            self['stream_picon_{}'.format(row_index)].hide()
            self['stream_favorite_{}'.format(row_index)].hide()
            for edge in ('fill', 'top', 'bottom', 'left', 'right'):
                self['stream_focus_{}_{}'.format(row_index, edge)].hide()

    def _select(self, index):
        if self._channel_move is not None:
            if index >= len(self._entries) and self._live_has_more:
                self._start_live_more(force=True)
            self.selected_index = self._channel_move.move(self._entries, index)
            self._reindex_live_preview()
            self._refresh_live_streams()
            return
        if self._loading or self._fullscreen_active:
            return
        if not self._entries:
            if index >= 0 and self._live_has_more:
                self._live_pending_target_index = 0
                self._start_live_more(force=True)
            return
        if index >= len(self._entries) and self._live_has_more:
            self._live_pending_target_index = max(int(index), int(self._live_pending_target_index or 0))
            self._start_live_more(force=True)
            return
        GTAsyncListScreen._select(self, index)

    def _cancel_preview_resolution(self, clear_target=False):
        for timer in (self._preview_dwell_timer, self._preview_link_timer):
            try:
                timer.stop()
            except Exception:
                pass
        with self._preview_link_lock:
            token = self._preview_link_token
            self._preview_link_token = None
            self._preview_link_pending = False
            self._preview_link_result = None
            self._preview_link_deadline = 0.0
            self._preview_fullscreen_requested = False
            if clear_target:
                self._preview_target_item = None
                self._preview_target_key = ''
                self._preview_target_index = -1
        _cancel_worker_token(token)
        _cancel_download_pending(self.client)

    def _schedule_preview_for_selection(self):
        """Keep explicit OK work aligned; highlight movement never tunes."""
        if self._closed or self._fullscreen_active or (not self._entries):
            return
        try:
            self._preview_dwell_timer.stop()
        except Exception:
            pass
        item = self._entries[self.selected_index]
        key = self._selection_key(item)
        if not key:
            self._cancel_preview_resolution(clear_target=True)
            self._show_preview_fallback(_('SELECT A CHANNEL'))
            return
        with self._preview_link_lock:
            pending = bool(self._preview_link_pending)
            pending_key = str(self._preview_target_key or '')
        cancelled_pending = False
        if pending and key == pending_key:
            self._show_preview_fallback(_('Please wait'))
            return
        if pending:
            with self._preview_link_lock:
                self._preview_generation += 1
            self._cancel_preview_resolution(clear_target=True)
            cancelled_pending = True
        current = self._current_navigation_reference()
        if self._preview_service_active and self._same_service_reference(current, self._playing_preview_reference):
            if key == self._playing_preview_key:
                self._playing_preview_item = item
                self._playing_preview_index = int(self.selected_index)
                self._resume_preview_pig()
            elif cancelled_pending:
                self._resume_preview_pig()
            return
        self._show_preview_fallback(_('SELECT A CHANNEL'))

    def _preview_dwell_fired(self):
        """Legacy timer callback kept inert: explicit previews require OK."""
        return

    def _stop_preview_stream_diag(self):
        probe = getattr(self, '_stream_diag_probe', None)
        self._stream_diag_probe = None
        if probe is not None:
            probe.stop()

    def _preview_stream_diag(self, stage, detail='', play_id=None, started_at=None):
        if not _is_stalker_client(self.client):
            return
        play_id = play_id or self._stream_diag_play_id
        if not play_id:
            return
        started_at = started_at or self._stream_diag_started_at
        elapsed_ms = max(0, int((time.monotonic() - started_at) * 1000))
        log_event('stream_diag', 'play_id={} phase=preview engine={} elapsed_ms={} stage={} {} geo=unknown'.format(play_id, self._active_live_service_type, elapsed_ms, stage, detail))

    def _begin_preview_link(self, item, index, request_fullscreen=False):
        """Start one resolver with an immutable mini-TV/fullscreen intent."""
        if not _allow_account_playback(self.session, self.client):
            return
        key = self._selection_key(item)
        if not key:
            return
        self._cancel_live_page_epg()
        try:
            timeout = max(3, min(15, int(getattr(self.client, 'timeout', 12))))
        except (TypeError, ValueError, OverflowError):
            timeout = 12
        with self._preview_link_lock:
            if self._preview_link_pending and key == self._preview_target_key:
                return
            if callable(getattr(self, '_cancel_live_startup', None)) and self._cancel_live_startup(restore_released=True) is False:
                return
            old_token = self._preview_link_token
            self._preview_generation += 1
            generation = self._preview_generation
            token = _WorkerToken(min(WORKER_PLAYBACK_LINK_TIMEOUT_SECONDS, timeout + 1))
            self._preview_target_item = item
            self._preview_target_key = key
            self._preview_target_index = int(index)
            self._preview_fullscreen_requested = bool(request_fullscreen)
            self._preview_link_pending = True
            self._preview_link_result = None
            self._preview_link_token = token
            self._preview_link_deadline = token.deadline
            if _is_stalker_client(self.client):
                self._stream_diag_play_id = uuid.uuid4().hex[:10]
                self._stream_diag_started_at = time.monotonic()
        if _is_stalker_client(self.client):
            self._stop_preview_stream_diag()
            self._preview_stream_diag('link-request', 'outcome=started')
        _cancel_worker_token(old_token)
        for timer in (self._preview_dwell_timer, self._preview_link_timer):
            try:
                timer.stop()
            except Exception:
                pass
        worker = threading.Thread(target=self._resolve_preview_link, args=(generation, item, int(index), key, token))
        worker.daemon = True
        worker.start()
        self._show_preview_fallback(_('Please wait'))
        self._preview_link_timer.start(LIVE_PREVIEW_LINK_POLL_MS, True)

    def _resolve_preview_link(self, generation, item, index, key, worker_token=None):
        resolver = getattr(self.client, 'playback_url', None)
        if not callable(resolver):
            resolver = getattr(self.client, 'refresh_playback_url', None)
        try:
            url = str(_bounded_client_call(self.client, worker_token, lambda: resolver(item)) or '').strip() if callable(resolver) else ''
        except Exception as error:
            url = ''
            error_type = error.__class__.__name__
        else:
            error_type = 'none'
        diagnostic = None
        with self._preview_link_lock:
            if self._closed or not self._preview_link_pending or generation != self._preview_generation or (key != self._preview_target_key) or (worker_token is not self._preview_link_token):
                return
            self._preview_link_result = (generation, item, int(index), key, url)
            if _is_stalker_client(self.client):
                diagnostic = (self._stream_diag_play_id, self._stream_diag_started_at)
        if diagnostic is not None:
            self._preview_stream_diag('link-result', 'outcome={} error_type={}'.format('ready' if url else 'empty', error_type), play_id=diagnostic[0], started_at=diagnostic[1])

    def _live_channel_list_context(self):
        context = getattr(self, '_live_recall_context', None)
        category_id = str(getattr(self.category, 'category_id', '') or '').strip()
        if context is None or context.category_id != category_id or context.entries is not self._entries:
            context = LiveChannelListContext(self.category, self._entries, getattr(self, '_live_has_more', False))
            self._live_recall_context = context
        context.has_more = bool(getattr(self, '_live_has_more', False))
        return context

    def _preview_link_tick(self):
        restart_poll = False
        failed = False
        consumed = None
        with self._preview_link_lock:
            if self._closed or not self._preview_link_pending:
                return
            result = self._preview_link_result
            if result is None:
                if time.monotonic() < self._preview_link_deadline:
                    restart_poll = True
                else:
                    failed = True
            else:
                generation, item, index, key, url = result
                result_matches = bool(generation == self._preview_generation and key == self._preview_target_key)
                if not result_matches:
                    self._preview_link_result = None
                    if time.monotonic() < self._preview_link_deadline:
                        restart_poll = True
                    else:
                        failed = True
                else:
                    request_fullscreen = self._preview_fullscreen_requested
                    self._preview_link_result = None
                    self._preview_link_pending = False
                    self._preview_link_token = None
                    self._preview_link_deadline = 0.0
                    consumed = (generation, item, int(index), key, url, request_fullscreen)
        if restart_poll:
            self._preview_link_timer.start(LIVE_PREVIEW_LINK_POLL_MS, True)
            return
        if failed:
            self._preview_link_failed('timeout')
            return
        if consumed is None:
            return
        generation, item, index, key, url, request_fullscreen = consumed
        if not url:
            self._preview_link_failed('empty-link')
            return
        if VideoWindow is None and (not request_fullscreen):
            self._preview_link_failed('preview-unavailable')
            return
        try:
            from .browser_player import build_extplayer_reference
            reference = build_extplayer_reference(url, item.name, getattr(item, 'content_type', 'live'), service_type=self._active_live_service_type)
        except Exception as error:
            self._preview_link_failed('reference-error', error)
            return
        navigation = getattr(self.session, 'nav', None)
        if navigation is None:
            self._preview_link_failed('navigation-unavailable')
            return
        from .browser_player import reference_service_type
        self._preview_stream_diag('service-reference', 'outcome=built actual_engine={}'.format(reference_service_type(reference, 4097)))
        try:
            from .browser_player import _play_service
            _play_service(navigation, reference)
        except Exception as error:
            self._preview_link_failed('play-service-error', error)
            return
        self._preview_stream_diag('play-service', 'outcome=accepted')
        if _is_stalker_client(self.client):
            self._stop_preview_stream_diag()
            self._stream_diag_probe = _StalkerStreamProbe(navigation, reference, self._stream_diag_play_id, started_at=self._stream_diag_started_at, phase='preview')
            self._stream_diag_probe.start()
        self._pre_list_restored = False
        self._preview_auto_suspended_key = ''
        self._playing_preview_item = item
        self._playing_preview_key = key
        self._playing_preview_index = int(index)
        self._playing_preview_reference = reference
        self._preview_service_active = True
        self._preview_service_started_at = time.monotonic()
        remember_live_channel(self.client, item, resume_account_scope(self.client), category_id=getattr(self.category, 'category_id', ''), context=self._live_channel_list_context())
        arm_startup = getattr(self, '_arm_live_startup', None)
        if callable(arm_startup):
            arm_startup()
        if not request_fullscreen:
            self._schedule_live_active_picon(item, key)
        with self._preview_link_lock:
            self._preview_fullscreen_requested = False
        from .browser_player import reference_service_type
        _debug('live preview started selected={} engine={} reason={}'.format(_debug_identifier(key), reference_service_type(reference, 4097), 'ok_fullscreen' if request_fullscreen else 'ok_mini_tv'))
        if request_fullscreen:
            self._open_preview_fullscreen(item, index, reference)
            return
        self._schedule_selected_detail()
        self._resume_preview_pig()
        self._schedule_live_page_epg()

    def _preview_link_failed(self, reason='unavailable', error=None):
        self._preview_stream_diag('start-failed', 'reason={} error_type={}'.format(reason, error.__class__.__name__ if error is not None else 'none'))
        requested = self._preview_fullscreen_requested
        self._cancel_preview_resolution(clear_target=False)
        self._show_preview_fallback(_('Could not open the stream.'))
        self._schedule_live_page_epg()
        if requested:
            self['message'].setText(_('Could not open the stream.'))

    def _start_live_more(self, force=False):
        """Start and track every catalogue worker across list refreshes."""
        local_hidden_scan = bool(self._hidden_category_ids and (not str(getattr(self.category, 'category_id', '') or '').strip()) and (str(getattr(self.client, 'source_type', 'xtream') or 'xtream').lower() in ('xtream', 'm3u')))
        if self.content_type != 'live' or self._closed or self._fullscreen_active or self._loading or self._live_more_loading or (not self._live_has_more) or (not force and self._live_empty_more_batches >= 3 and (not local_hidden_scan)):
            return False
        self._live_more_loading = True
        generation = self._live_catalog_generation
        worker_token = _WorkerToken(WORKER_LIST_TIMEOUT_SECONDS)
        self._live_more_token = worker_token
        with self._live_more_worker_events_lock:
            self._live_more_worker_tokens.add(worker_token)
        worker = threading.Thread(target=self._run_live_more, args=(generation, worker_token))
        worker.daemon = True
        worker.start()
        self._timer.start(100, True)
        return True

    def _run_live_more(self, generation, worker_token=None):
        try:
            return _GTStreamListBase._run_live_more(self, generation, worker_token)
        finally:
            with self._live_more_worker_events_lock:
                self._live_more_worker_tokens.discard(worker_token)

    def _selection_key(self, item):
        return str(getattr(item, 'stream_id', '') or '')

    def _shared_epg_cache_snapshot(self, stream_id):
        """Read the provider EPG cache without starting provider work."""
        state = _client_epg_state(self.client)
        if state is None:
            return []
        now = time.monotonic()
        with state['lock']:
            cached = state['cache'].get(str(stream_id or ''))
            if cached is None:
                return []
            loaded_at, events = cached
            if not events or now - float(loaded_at) >= LIVE_EPG_CACHE_TTL_SECONDS:
                return []
            return list(events[:LIVE_EPG_FETCH_LIMIT])

    def _cached_epg_snapshot(self, stream_id):
        """Return current/next from RAM only; never navigate through HTTP."""
        events = self._shared_epg_cache_snapshot(stream_id)
        if events:
            return events
        if _is_m3u_client(self.client):
            loader = getattr(self.client, 'peek_epg', None)
            if callable(loader):
                try:
                    return list(loader(stream_id, LIVE_EPG_FETCH_LIMIT) or [])
                except Exception:
                    return []
        if _is_stalker_client(self.client):
            try:
                meta = dict(getattr(self.client, '_live_channel_meta', {}).get(str(stream_id or ''), {}))
                identities = tuple(meta.get('identities') or ())
                if not identities:
                    identities = (('stream_id', str(stream_id or '')),)
                parser = getattr(self.client, '_epg_events', None)
                if callable(parser):
                    for embedded in meta.get('embedded') or ():
                        for _unused_kind, identity in identities:
                            events = list(parser(embedded, identity, LIVE_EPG_FETCH_LIMIT) or [])
                            if events:
                                return events
            except Exception:
                return []
        return []

    def _store_detail_component(self, stream_id, component, value, expected_epoch=None):
        """Commit a completed component independently of the current row."""
        stream_id = str(stream_id or '')
        if not stream_id:
            return False
        now_mono = time.monotonic()
        now_wall = time.time()
        with self._detail_store_lock:
            if expected_epoch is not None and expected_epoch != self._detail_store_epoch:
                return False
            entry = self._detail_store.setdefault(stream_id, {})
            if component == 'events':
                events = list(value or [])
                if events:
                    entry['events'] = events
                    entry['epg_cached_at'] = now_mono
                    entry['epg_captured_at'] = now_wall
                    entry.pop('epg_retry_after', None)
                else:
                    entry.pop('events', None)
                    entry.pop('epg_cached_at', None)
                    entry.pop('epg_captured_at', None)
                    entry['epg_retry_after'] = now_mono + LIVE_DETAIL_EMPTY_RETRY_SECONDS
            elif component == 'picon_path':
                path = str(value or '').strip()
                if path and _valid_live_picon_file(path):
                    entry['picon_path'] = path
                    entry['picon_cached_at'] = now_mono
                    entry['picon_captured_at'] = now_wall
                else:
                    entry.pop('picon_path', None)
                    entry.pop('picon_cached_at', None)
                    entry.pop('picon_captured_at', None)
            entry['updated_at'] = now_mono
            if not entry.get('events') and (not entry.get('picon_path')) and (not entry.get('epg_retry_after')):
                self._detail_store.pop(stream_id, None)
            if len(self._detail_store) > LIVE_EPG_CACHE_MAX_ENTRIES:
                oldest = sorted(self._detail_store, key=lambda key: self._detail_store[key].get('updated_at', 0.0))
                for key in oldest[:len(self._detail_store) - LIVE_EPG_CACHE_MAX_ENTRIES]:
                    self._detail_store.pop(key, None)
        return True

    def _detail_store_snapshot(self, stream_id, item=None):
        """Return fresh local EPG/logo data; this method never uses HTTP."""
        stream_id = str(stream_id or '')
        now = time.monotonic()
        with self._detail_store_lock:
            entry = dict(self._detail_store.get(stream_id, {}))
        events = []
        epg_cached_at = float(entry.get('epg_cached_at', 0.0) or 0.0)
        if entry.get('events') and now - epg_cached_at < LIVE_DETAIL_CACHE_TTL_SECONDS:
            events = list(entry['events'])
        if not events:
            events = self._cached_epg_snapshot(stream_id)
            if events:
                self._store_detail_component(stream_id, 'events', events)
        epg_retry_after = float(entry.get('epg_retry_after', 0.0) or 0.0)
        epg_checked = bool(events) or now < epg_retry_after
        path = artwork_path(entry.get('picon_path', '') or '')
        picon_cached_at = float(entry.get('picon_cached_at', 0.0) or 0.0)
        if not path or not _valid_live_picon_file(path) or now - picon_cached_at >= LIVE_DETAIL_CACHE_TTL_SECONDS:
            path = ''
        if not path and item is not None:
            url = _safe_picon_url(getattr(item, 'icon', ''))
            cached_path = _cached_picon_path(url) if url else ''
            if cached_path and _valid_live_picon_file(cached_path):
                path = cached_path
                self._store_detail_component(stream_id, 'picon_path', path)
        else:
            url = ''
        picon_checked = bool(path or not url or _picon_failure_active(url))
        with self._detail_store_lock:
            stored = dict(self._detail_store.get(stream_id, {}))
        captured_at = max(float(stored.get('epg_captured_at', 0.0) or 0.0), float(stored.get('picon_captured_at', 0.0) or 0.0))
        return {'stream_id': stream_id, 'events': events, 'epg_checked': epg_checked, 'picon_path': path, 'picon_checked': picon_checked, 'captured_at': captured_at or time.time()}

    def _publish_detail_component(self, generation, stream_id, component, value, store_epoch=None):
        if self._closed:
            return
        stored = self._store_detail_component(stream_id, component, value, expected_epoch=store_epoch)
        if not stored:
            return
        if generation != self._detail_generation:
            return
        with self._detail_results_lock:
            self._detail_results.setdefault(generation, []).append((str(stream_id or ''), component, value))

    def _visible_detail_page(self):
        page_start = int(self.selected_index / self.page_size) * self.page_size
        items = tuple(self._entries[page_start:page_start + self.page_size])
        page_key = (page_start, tuple((self._selection_key(item) for item in items)))
        return (page_key, items)

    def _show_selected_local_detail(self, item, key):
        if key != self._detail_selection_key or not self._detail_selection_started_at:
            self._detail_selection_started_at = time.monotonic()
        ellipsize_dynamic_text(self['ribbon_channel'], localized_upper(item.name), fallback_chars=58)
        for timer in (self._detail_progress_timer,):
            try:
                timer.stop()
            except Exception:
                pass
        self._detail_selection_key = key
        self._preview_item = item
        cached = self._detail_store_snapshot(key, item=item)
        self._preview_epg_events = list(cached.get('events', ()))
        self._preview_epg_loaded_at = time.time() if self._preview_epg_events else 0.0
        self._detail_picon_path = artwork_path(cached.get('picon_path', '') or '')
        self._detail_picon_loaded_at = time.monotonic() if self._detail_picon_path else 0.0
        self._refresh_live_active_picon()
        if self._preview_epg_events:
            self._render_selected_epg(self._preview_epg_events)
            self._detail_progress_timer.start(LIVE_DETAIL_PROGRESS_INTERVAL_MS, True)
        elif cached.get('epg_checked') or self._provider_epg_loader() is None:
            self._render_selected_epg([])
        else:
            self._render_detail_waiting(item)

    def _schedule_selected_detail(self):
        if self._closed or self._fullscreen_active or (not self._entries):
            return
        item = self._entries[self.selected_index]
        key = self._selection_key(item)
        page_key = self._visible_detail_page()[0]
        page_changed = page_key != self._detail_page_key
        selection_changed = key != self._detail_selection_key
        if selection_changed:
            self._detail_epg_retry_count = 0
        if selection_changed or page_changed:
            self._cancel_selected_detail(clear_selection=False)
        if page_changed:
            self._detail_page_key = page_key
        if selection_changed or page_changed:
            self._show_selected_local_detail(item, key)
        cached = self._detail_store_snapshot(key, item=item)
        epg_complete = bool(cached.get('epg_checked') or self._selected_epg_loader() is None)
        selected_complete = epg_complete
        preview_ready = self._selected_detail_preview_ready(key)
        if selected_complete:
            if preview_ready:
                if not cached.get('events') and self._provider_epg_loader() is not None:
                    self._schedule_selected_epg_retry(key)
                self._schedule_detail_prefetch()
            return
        if not preview_ready:
            return
        if self._detail_state_active(self._detail_foreground_state):
            return
        detail_dwell_ms = _live_detail_dwell_ms(self.client)
        self._detail_deadline = time.monotonic() + detail_dwell_ms / 1000.0
        self._detail_dwell_timer.start(detail_dwell_ms, True)
        _debug('page detail dwell scheduled page={} selected={} delay_ms={}'.format(page_key[0], key, detail_dwell_ms))

    @staticmethod
    def _detail_state_active(state):
        if not state:
            return False
        events = state.get('events', {})
        tokens = state.get('tokens', {})
        for component, event in events.items():
            token = tokens.get(component)
            if not event.is_set() and (token is None or token.active()):
                return True
        return False

    @staticmethod
    def _cancel_detail_state(state):
        if not state:
            return
        for token in state.get('tokens', {}).values():
            _cancel_worker_token(token)
        _cancel_worker_token(state.get('token'))

    def _cancel_selected_detail(self, clear_selection=False):
        self._cancel_programme_poster()
        self._detail_epg_retry_key = ''
        self._detail_epg_retry_deadline = 0.0
        for timer in (self._detail_dwell_timer, self._detail_epg_retry_timer, self._detail_poll_timer, self._detail_prefetch_timer, self._detail_progress_timer, self._summary_scroll_timer):
            try:
                timer.stop()
            except Exception:
                pass
        self._summary_selection_key = None
        hide_scrollbar(self, 'summary_scrollbar')
        self['summary_scroll'].setText('')
        self._detail_generation += 1
        self._cancel_detail_state(self._detail_foreground_state)
        self._cancel_detail_state(self._detail_prefetch_state)
        self._detail_foreground_state = None
        self._detail_prefetch_state = None
        self._detail_deadline = 0.0
        self._detail_prefetch_deadline = 0.0
        with self._detail_results_lock:
            self._detail_results = {}
        if clear_selection:
            self._detail_selection_key = None
            self._detail_page_key = None
            self._detail_epg_retry_count = 0
            self._detail_selection_started_at = 0.0

    def _schedule_selected_epg_retry(self, key):
        """Retry a still-selected channel at most twice after its existing cooldown."""
        if self._closed or self._fullscreen_active or getattr(self, '_live_modal_active', False) or (not key) or (key != self._detail_selection_key) or (self._detail_epg_retry_count >= LIVE_DETAIL_EPG_RETRY_LIMIT) or (not self._selected_detail_preview_ready(key)):
            return
        now = time.monotonic()
        if self._detail_epg_retry_key == key and self._detail_epg_retry_deadline > now:
            return
        with self._detail_store_lock:
            entry = self._detail_store.get(key, {})
            retry_at = float(entry.get('epg_retry_after', 0.0) or 0.0)
        if retry_at <= now:
            retry_at = now + LIVE_DETAIL_EMPTY_RETRY_SECONDS
        self._detail_epg_retry_key = key
        self._detail_epg_retry_deadline = retry_at + 0.1
        self._detail_epg_retry_count += 1
        self._detail_epg_retry_timer.start(max(100, int((self._detail_epg_retry_deadline - now) * 1000)), True)
        log_event('programme_art', 'stage=epg outcome=retry-scheduled attempt={} delay_ms={}'.format(self._detail_epg_retry_count, int((self._detail_epg_retry_deadline - now) * 1000)))

    def _selected_epg_retry_fired(self):
        key = self._detail_epg_retry_key
        self._detail_epg_retry_key = ''
        self._detail_epg_retry_deadline = 0.0
        if self._closed or self._fullscreen_active or getattr(self, '_live_modal_active', False) or (key != self._detail_selection_key) or (not self._selected_detail_preview_ready(key)):
            return
        self._schedule_selected_detail()

    def _detail_dwell_fired(self):
        if self._closed or self._fullscreen_active or (not self._entries):
            return
        remaining = self._detail_deadline - time.monotonic()
        if remaining > 0.01:
            self._detail_dwell_timer.start(max(10, int(round(remaining * 1000))), True)
            return
        page_key = self._visible_detail_page()[0]
        if page_key != self._detail_page_key:
            return
        if self._detail_state_active(self._detail_foreground_state):
            return
        selected = self._entries[self.selected_index]
        stream_id = self._selection_key(selected)
        if not stream_id or stream_id != self._detail_selection_key:
            return
        if not self._selected_detail_preview_ready(stream_id):
            return
        if _is_stalker_client(self.client):
            now = time.monotonic()
            started_at = float(self._preview_service_started_at or 0.0)
            elapsed_ms = max(0, int(round((now - started_at) * 1000))) if started_at > 0.0 else 0
            quiet_remaining = STALKER_LIVE_EPG_QUIET_MS - elapsed_ms
            if quiet_remaining > 0:
                delay_ms = max(10, int(quiet_remaining))
                self._detail_deadline = now + delay_ms / 1000.0
                self._detail_dwell_timer.start(delay_ms, True)
                return
        epg_loader = self._selected_epg_loader()
        cached = self._detail_store_snapshot(stream_id, item=selected)
        needs_epg = bool(epg_loader is not None and (not cached.get('epg_checked')))
        if not needs_epg:
            self._schedule_detail_prefetch()
            return
        generation = self._detail_generation
        store_epoch = self._detail_store_epoch
        state = {'generation': generation, 'stream_id': stream_id, 'events': {}, 'tokens': {}}
        workers = []
        if needs_epg:
            event = threading.Event()
            token = _WorkerToken(LIVE_DETAIL_EPG_TIMEOUT_SECONDS)
            state['events']['events'] = event
            state['tokens']['events'] = token
            workers.append(threading.Thread(target=self._load_selected_epg_worker, args=(generation, stream_id, event, store_epoch, token)))
        self._detail_foreground_state = state
        for worker in workers:
            worker.daemon = True
            worker.start()
        self._detail_poll_timer.start(75, True)
        _debug('selected detail started page={} selected={} epg={}'.format(page_key[0], stream_id, needs_epg))

    def _provider_epg_loader(self):
        loader = getattr(self.client, 'load_epg', None)
        if callable(loader):
            return loader
        loader = getattr(self.client, 'load_short_epg', None)
        return loader if callable(loader) else None

    def _selected_detail_preview_ready(self, stream_id):
        if VideoWindow is None or self._closed or self._fullscreen_active or (not self._preview_service_active) or (str(stream_id or '') != self._playing_preview_key):
            return False
        return self._same_service_reference(self._current_navigation_reference(), self._playing_preview_reference)

    def _selected_epg_loader(self):
        if not self._selected_detail_preview_ready(self._detail_selection_key):
            return None
        return self._provider_epg_loader()

    def _load_selected_epg_worker(self, generation, stream_id, worker_event, store_epoch=None, worker_token=None):
        started = time.monotonic()
        outcome = 'empty'
        events = []
        try:
            if worker_token is not None:
                worker_token.check()
            loader = self._provider_epg_loader()
            if loader is not None:
                events = _bounded_client_call(self.client, worker_token, lambda: _load_shared_live_epg(self.client, stream_id, LIVE_EPG_FETCH_LIMIT, loader, token=worker_token))
            if worker_token is not None:
                worker_token.check()
            outcome = 'ready' if events else 'empty'
            self._publish_detail_component(generation, stream_id, 'events', list(events or []), store_epoch=store_epoch)
        except _WorkerCancelled:
            outcome = 'cancelled'
        except Exception:
            outcome = 'error'
            if worker_token is None or worker_token.active():
                self._publish_detail_component(generation, stream_id, 'events', [], store_epoch=store_epoch)
        finally:
            log_event('programme_art', 'stage=epg outcome={} elapsed_ms={} events={}'.format(outcome, int((time.monotonic() - started) * 1000), len(events or [])))
            worker_event.set()

    def _load_detail_picon_path(self, item, worker_token=None):
        return _load_live_picon_path(self.client, item, worker_token=worker_token)

    @staticmethod
    def _current_live_row_event(events, now=None):
        """Return the programme airing now without starting provider work."""
        now = int(time.time() if now is None else now)
        fallback = None
        for event in tuple(events or ()):
            try:
                start = int(getattr(event, 'start_timestamp', 0) or 0)
                end = int(getattr(event, 'end_timestamp', 0) or 0)
            except (TypeError, ValueError, OverflowError):
                start, end = (0, 0)
            if start and end and (start <= now < end):
                return event
            if fallback is None and (not start or start <= now) and (not (end and end <= now)):
                fallback = event
        return fallback

    @broadcast_refresh_after
    def _refresh_live_row_epg(self, page_start=None, page_items=None):
        """Paint current titles/progress from RAM only for visible rows."""
        if self._closed or self._fullscreen_active or getattr(self, '_live_modal_active', False):
            return
        if page_start is None:
            page_start = int(self.selected_index / self.page_size) * self.page_size
        if page_items is None:
            page_items = self._entries[page_start:page_start + self.page_size]
        now = int(time.time())
        for row_index in range(self.page_size):
            program = self['stream_program_{}'.format(row_index)]
            progress_bg = self['stream_progress_bg_{}'.format(row_index)]
            progress_fill = self['stream_progress_fill_{}'.format(row_index)]
            if row_index >= len(page_items):
                program.setText('')
                progress_bg.hide()
                progress_fill.hide()
                continue
            item = page_items[row_index]
            key = self._selection_key(item)
            cached = self._detail_store_snapshot(key, item=item)
            event = self._current_live_row_event(cached.get('events'), now=now)
            if event is None:
                program.setText('')
                progress_bg.hide()
                progress_fill.hide()
                continue
            ellipsize_dynamic_text(program, str(getattr(event, 'title', '') or ''), fallback_chars=21)
            ratio, unused_label = self._progress_details(event)
            if ratio <= 0.0:
                progress_bg.hide()
                progress_fill.hide()
                continue
            try:
                progress_fill.instance.resize(eSize(max(1, int(round(self._row_progress_width * ratio))), self._row_progress_height))
            except Exception:
                pass
            progress_bg.show()
            progress_fill.show()

    def _cancel_live_page_epg(self):
        self._live_page_epg_generation += 1
        self._live_page_epg_signature = None
        self._live_page_epg_pending = ()
        for timer in (self._live_page_epg_dwell_timer, self._live_page_epg_poll_timer):
            try:
                timer.stop()
            except Exception:
                pass
        state = self._live_page_epg_state
        self._live_page_epg_state = None
        if state is not None:
            with state['lock']:
                state['cancelled'].set()
                _cancel_worker_token(state.get('token'))
                state['results'].clear()

    def _schedule_live_page_epg(self, page_start=None, page_items=None):
        """Queue missing EPG for the visible page, keeping navigation local."""
        if self._closed or self._fullscreen_active or self._loading or self._live_modal_active or self._preview_link_pending:
            return
        if page_start is None:
            page_start = int(self.selected_index / self.page_size) * self.page_size
        if page_items is None:
            page_items = self._entries[page_start:page_start + self.page_size]
        signature = (page_start, tuple((self._selection_key(item) for item in page_items)))
        if signature == self._live_page_epg_signature:
            return
        self._cancel_live_page_epg()
        self._live_page_epg_signature = signature
        if not page_items or self._provider_epg_loader() is None:
            return
        missing = []
        seen = set()
        for item in page_items:
            key = self._selection_key(item)
            if not key or key in seen:
                continue
            seen.add(key)
            if not self._detail_store_snapshot(key, item=item).get('epg_checked'):
                missing.append(key)
        if not missing:
            return
        selected_key = self._selection_key(self._entries[self.selected_index])
        if selected_key in missing and missing[0] != selected_key:
            missing.remove(selected_key)
            missing.insert(0, selected_key)
        self._live_page_epg_pending = tuple(missing)
        dwell = STALKER_LIVE_PAGE_EPG_DWELL_MS if _is_stalker_client(self.client) else LIVE_PAGE_EPG_DWELL_MS
        self._live_page_epg_dwell_timer.start(dwell, True)

    def _live_page_epg_start(self):
        if self._closed or self._fullscreen_active or self._loading:
            return
        if self._live_modal_active or self._preview_link_pending:
            self._live_page_epg_dwell_timer.start(500, True)
            return
        pending = self._live_page_epg_pending
        if not pending or self._provider_epg_loader() is None:
            return
        if _is_stalker_client(self.client) and self._preview_service_active:
            elapsed_ms = int(1000 * max(0.0, time.monotonic() - self._preview_service_started_at))
            remaining = STALKER_LIVE_EPG_QUIET_MS - elapsed_ms
            if remaining > 0:
                self._live_page_epg_dwell_timer.start(remaining, True)
                return
        with self._detail_store_lock:
            store_epoch = self._detail_store_epoch
        state = {'generation': self._live_page_epg_generation, 'signature': self._live_page_epg_signature, 'keys': pending, 'epoch': store_epoch, 'cancelled': threading.Event(), 'done': threading.Event(), 'lock': threading.Lock(), 'token': None, 'results': deque()}
        self._live_page_epg_pending = ()
        self._live_page_epg_state = state
        worker = threading.Thread(target=self._load_live_page_epg_worker, args=(state,))
        worker.daemon = True
        worker.start()
        self._live_page_epg_poll_timer.start(LIVE_PAGE_EPG_POLL_MS, True)

    def _load_live_page_epg_worker(self, state):
        try:
            with self._live_page_epg_worker_lock:
                for key in state['keys']:
                    if state['cancelled'].is_set():
                        break
                    token = _WorkerToken(LIVE_DETAIL_EPG_TIMEOUT_SECONDS)
                    with state['lock']:
                        if state['cancelled'].is_set():
                            break
                        state['token'] = token
                    try:
                        loader = self._provider_epg_loader()
                        events = _bounded_client_call(self.client, token, lambda sid=key, fetch=loader: _load_shared_live_epg(self.client, sid, LIVE_EPG_FETCH_LIMIT, fetch, token=token)) if loader is not None else []
                    except Exception:
                        events = []
                    finally:
                        with state['lock']:
                            if state['token'] is token:
                                state['token'] = None
                    if state['cancelled'].is_set():
                        break
                    with state['lock']:
                        state['results'].append((key, list(events or [])))
                    if state['cancelled'].wait(LIVE_PAGE_EPG_REQUEST_PAUSE_MS / 1000.0):
                        break
        finally:
            state['done'].set()

    def _live_page_epg_poll(self):
        state = self._live_page_epg_state
        if state is None or self._closed or self._fullscreen_active or (state['generation'] != self._live_page_epg_generation) or (state['signature'] != self._live_page_epg_signature):
            return
        with state['lock']:
            results = list(state['results'])
            state['results'].clear()
            finished = state['done'].is_set()
        changed = False
        for key, events in results:
            if not self._store_detail_component(key, 'events', events, expected_epoch=state['epoch']):
                continue
            changed = True
            if self._entries and key == self._detail_selection_key and (key == self._selection_key(self._entries[self.selected_index])):
                self._show_selected_local_detail(self._entries[self.selected_index], key)
        if changed:
            self._refresh_live_row_epg()
        if finished:
            self._live_page_epg_state = None
        else:
            self._live_page_epg_poll_timer.start(LIVE_PAGE_EPG_POLL_MS, True)

    @staticmethod
    def _close_live_pixmap_loader(screen, key):
        try:
            loader = getattr(screen, '_gt_pixmap_loaders', {}).pop(key, None)
            if loader is not None:
                loader.close()
        except Exception:
            pass

    def _clear_live_visible_picons(self, reset_signature=True):
        self._live_row_picon_generation += 1
        for row_index in range(int(getattr(self, 'page_size', LIVE_STREAM_PAGE_SIZE))):
            self._close_live_pixmap_loader(self, 'live-row-picon-{}'.format(row_index))
            try:
                self['stream_picon_{}'.format(row_index)].hide()
            except Exception:
                pass
        if reset_signature:
            self._live_row_picon_signature = None

    def _clear_live_selected_picon(self, reset_signature=True):
        self._live_selected_picon_generation += 1
        self._live_selected_picon_ready = False
        self._close_live_pixmap_loader(self, 'live-selected-picon')
        try:
            self['detail_picon'].hide()
        except Exception:
            pass
        if reset_signature:
            self._live_selected_picon_signature = None
            self._cancel_programme_poster()

    @broadcast_refresh_after
    def _refresh_live_visible_picons(self, force=False, page_start=None, page_items=None):
        """Decode only the picons currently visible on the ten-row page."""
        if self._closed or self._fullscreen_active or getattr(self, '_live_modal_active', False) or (not self._entries):
            self._clear_live_visible_picons()
            return
        if page_start is None:
            page_start = int(self.selected_index / self.page_size) * self.page_size
        if page_items is None:
            page_items = self._entries[page_start:page_start + self.page_size]
        page_items = tuple(page_items or ())
        paths = []
        for item in page_items:
            key = self._selection_key(item)
            cached = self._detail_store_snapshot(key, item=item)
            path = artwork_path(cached.get('picon_path', '') or '')
            if not path or not _valid_live_picon_file(path):
                path = ''
            paths.append((key, path))
        signature = (int(page_start), tuple(paths))
        if not force and signature == self._live_row_picon_signature:
            return
        self._clear_live_visible_picons(reset_signature=False)
        self._live_row_picon_signature = signature
        generation = self._live_row_picon_generation
        for row_index, unused_pair in enumerate(paths):
            unused_key, path = unused_pair
            if not path:
                continue
            widget_name = 'stream_picon_{}'.format(row_index)
            loader_key = 'live-row-picon-{}'.format(row_index)

            def loaded(success, row=row_index, expected=signature, owner=generation):
                widget = 'stream_picon_{}'.format(row)
                if owner != self._live_row_picon_generation or expected != self._live_row_picon_signature or self._closed or self._fullscreen_active:
                    return
                try:
                    if success:
                        self[widget].show()
                    else:
                        self[widget].hide()
                except Exception:
                    pass
            attach_native_alpha_pixmap(self, widget_name, path, key=loader_key, on_loaded=loaded)

    def _refresh_selected_picon(self, force=False):
        """Refresh only the right poster; large channel logos were removed."""
        if self._closed or self._fullscreen_active or getattr(self, '_live_modal_active', False) or (not self._entries):
            self._clear_live_selected_picon()
            return
        self._schedule_programme_poster()

    def _cancel_live_picon_prefetch(self, clear_render=False):
        """Cancel the bounded category queue without touching disk cache."""
        try:
            self._live_picon_prefetch_poll_timer.stop()
        except Exception:
            pass
        with self._live_picon_prefetch_lock:
            self._live_picon_prefetch_generation += 1
            tokens = tuple(self._live_picon_prefetch_tokens)
            self._live_picon_prefetch_queue.clear()
            self._live_picon_prefetch_jobs.clear()
            self._live_picon_prefetch_known.clear()
            self._live_picon_prefetch_catalog = None
            self._live_picon_prefetch_catalog_count = 0
            self._live_picon_prefetch_catalog_epoch = None
            self._live_picon_prefetch_priority = None
            self._live_picon_prefetch_completed.clear()
            self._live_picon_prefetch_tokens.clear()
        for token in tokens:
            _cancel_worker_token(token)
        if clear_render:
            self._clear_live_visible_picons()
            self._clear_live_selected_picon()

    def _queue_live_category_picons(self, page_start=None, page_items=None):
        """Prioritize ten visible logos and at most the following page."""
        if self._closed or self._fullscreen_active or getattr(self, '_live_modal_active', False) or (not self._entries):
            return
        if page_start is None:
            page_start = self.selected_index // self.page_size * self.page_size
        if page_items is None:
            page_items = self._entries[page_start:page_start + self.page_size]
        visible_items = tuple(page_items or ())
        ordered_items = tuple(self._entries[page_start:page_start + self.page_size * 2])
        target_keys = {self._selection_key(item) for item in ordered_items}
        with self._live_picon_prefetch_lock:
            generation = self._live_picon_prefetch_generation
            self._live_picon_prefetch_known.intersection_update(target_keys)
            jobs = self._live_picon_prefetch_jobs
            for key in tuple(jobs):
                if key not in target_keys:
                    jobs.pop(key, None)
            self._live_picon_prefetch_queue = deque((key for key in self._live_picon_prefetch_queue if key in jobs))
            for item in ordered_items:
                key = self._selection_key(item)
                if not key or key in self._live_picon_prefetch_known:
                    continue
                url = _safe_picon_url(getattr(item, 'icon', ''))
                if not url:
                    continue
                self._live_picon_prefetch_known.add(key)
                jobs[key] = (item, self._detail_store_epoch)
                self._live_picon_prefetch_queue.append(key)
            visible_keys = tuple((self._selection_key(item) for item in visible_items))
            priority = [key for key in visible_keys if key in jobs]
            visible_set = set(priority)
            remainder = [key for key in self._live_picon_prefetch_queue if key not in visible_set and key in jobs]
            self._live_picon_prefetch_queue = deque(priority + remainder)
            self._live_picon_prefetch_priority = (page_start, visible_keys)
            pending = bool(self._live_picon_prefetch_queue)
        if pending:
            self._start_live_picon_prefetch_workers(generation)
            self._live_picon_prefetch_poll_timer.start(LIVE_PICON_PREFETCH_POLL_MS, True)
        self._refresh_live_visible_picons(page_start=page_start, page_items=visible_items)

    def _start_live_picon_prefetch_workers(self, generation=None):
        threads = []
        with self._live_picon_prefetch_lock:
            current = self._live_picon_prefetch_generation
            generation = current if generation is None else int(generation)
            if generation != current or not self._live_picon_prefetch_queue:
                return
            active = int(self._live_picon_prefetch_worker_counts.get(generation, 0))
            count = min(LIVE_PICON_PREFETCH_WORKERS - active, len(self._live_picon_prefetch_queue))
            if count <= 0:
                return
            self._live_picon_prefetch_worker_counts[generation] = active + count
            for unused_index in range(count):
                threads.append(threading.Thread(target=self._live_picon_prefetch_worker, args=(generation,)))
        for worker in threads:
            worker.daemon = True
            try:
                worker.start()
            except Exception:
                with self._live_picon_prefetch_lock:
                    active = int(self._live_picon_prefetch_worker_counts.get(generation, 0))
                    if active <= 1:
                        self._live_picon_prefetch_worker_counts.pop(generation, None)
                    else:
                        self._live_picon_prefetch_worker_counts[generation] = active - 1

    def _live_picon_prefetch_worker(self, generation):
        try:
            while True:
                with self._live_picon_prefetch_lock:
                    if generation != self._live_picon_prefetch_generation or self._closed or self._fullscreen_active or getattr(self, '_live_modal_active', False) or (not self._live_picon_prefetch_queue):
                        return
                    key = self._live_picon_prefetch_queue.popleft()
                    job = self._live_picon_prefetch_jobs.pop(key, None)
                if job is None:
                    continue
                item, store_epoch = job
                token = _WorkerToken(max(3, LIVE_PICON_DOWNLOAD_TIMEOUT + 2))
                with self._live_picon_prefetch_lock:
                    if generation != self._live_picon_prefetch_generation:
                        token.cancel()
                        return
                    self._live_picon_prefetch_tokens.add(token)
                path = ''
                try:
                    path = self._load_detail_picon_path(item, token)
                except _WorkerCancelled:
                    pass
                except Exception:
                    url = _safe_picon_url(getattr(item, 'icon', ''))
                    if url:
                        _mark_picon_failure(url)
                finally:
                    with self._live_picon_prefetch_lock:
                        self._live_picon_prefetch_tokens.discard(token)
                with self._live_picon_prefetch_lock:
                    current = bool(generation == self._live_picon_prefetch_generation and (not self._closed) and (not self._fullscreen_active) and (not getattr(self, '_live_modal_active', False)))
                if not current:
                    return
                self._store_detail_component(key, 'picon_path', path, expected_epoch=store_epoch)
                with self._live_picon_prefetch_lock:
                    if generation == self._live_picon_prefetch_generation:
                        self._live_picon_prefetch_completed.add(key)
        finally:
            with self._live_picon_prefetch_lock:
                active = int(self._live_picon_prefetch_worker_counts.get(generation, 0))
                if active <= 1:
                    self._live_picon_prefetch_worker_counts.pop(generation, None)
                else:
                    self._live_picon_prefetch_worker_counts[generation] = active - 1

    def _live_picon_prefetch_poll(self):
        if self._closed or self._fullscreen_active or getattr(self, '_live_modal_active', False):
            return
        with self._live_picon_prefetch_lock:
            generation = self._live_picon_prefetch_generation
            completed = set(self._live_picon_prefetch_completed)
            self._live_picon_prefetch_completed.clear()
            pending = bool(self._live_picon_prefetch_queue)
            active = bool(self._live_picon_prefetch_worker_counts.get(generation, 0))
        if completed and self._entries:
            page_start = int(self.selected_index / self.page_size) * self.page_size
            page_items = self._entries[page_start:page_start + self.page_size]
            visible_keys = {self._selection_key(item) for item in page_items}
            if completed.intersection(visible_keys):
                self._refresh_live_visible_picons(force=True, page_start=page_start, page_items=page_items)
            selected_key = self._selection_key(self._entries[self.selected_index])
            if selected_key in completed:
                self._refresh_selected_picon(force=True)
        if pending:
            self._start_live_picon_prefetch_workers(generation)
        with self._live_picon_prefetch_lock:
            keep_polling = bool(self._live_picon_prefetch_queue or self._live_picon_prefetch_worker_counts.get(generation, 0) or self._live_picon_prefetch_completed)
        if keep_polling:
            self._live_picon_prefetch_poll_timer.start(LIVE_PICON_PREFETCH_POLL_MS, True)

    def _cancel_live_active_picon_fetch(self, clear_target=False):
        for timer in (self._live_active_picon_timer, self._live_active_picon_poll_timer):
            try:
                timer.stop()
            except Exception:
                pass
        self._live_active_picon_fetch_generation += 1
        state = self._live_active_picon_fetch_state
        self._live_active_picon_fetch_state = None
        if state is not None:
            _cancel_worker_token(state.get('token'))
        self._live_active_picon_fetch_deadline = 0.0
        if clear_target:
            self._live_active_picon_fetch_item = None
            self._live_active_picon_fetch_key = ''

    def _schedule_live_active_picon(self, item, key):
        """Queue artwork only for the exact channel opened with OK."""
        self._cancel_live_active_picon_fetch(clear_target=True)
        key = str(key or '')
        if not key or item is None or (not self._selected_detail_preview_ready(key)):
            self._refresh_live_active_picon(force=True)
            return
        self._live_active_picon_fetch_item = item
        self._live_active_picon_fetch_key = key
        self._refresh_live_active_picon(force=True)
        cached = self._detail_store_snapshot(key, item=item)
        if cached.get('picon_checked'):
            if not cached.get('picon_path'):
                self._defer_live_active_picon_failure(item)
            return
        if not _safe_picon_url(getattr(item, 'icon', '')):
            return
        delay_ms = _live_detail_dwell_ms(self.client)
        self._live_active_picon_fetch_deadline = time.monotonic() + delay_ms / 1000.0
        self._live_active_picon_timer.start(delay_ms, True)

    def _defer_live_active_picon_failure(self, item):
        url = _safe_picon_url(getattr(item, 'icon', ''))
        remaining = _picon_failure_remaining(url) if url else 0.0
        if remaining <= 0.0:
            return False
        delay_ms = max(1000, int(math.ceil(remaining * 1000.0)))
        self._live_active_picon_fetch_deadline = time.monotonic() + delay_ms / 1000.0
        self._live_active_picon_timer.start(delay_ms, True)
        return True

    def _ensure_live_active_picon(self):
        """Recover deferred exact-reference activation without row input."""
        key = str(getattr(self, '_playing_preview_key', '') or '')
        if not key:
            return False
        item = getattr(self, '_playing_preview_item', None)
        for candidate in tuple(getattr(self, '_entries', ()) or ()):
            if self._selection_key(candidate) == key:
                item = candidate
                break
        if key == self._live_active_picon_fetch_key:
            self._refresh_live_active_picon()
            if not self._selected_detail_preview_ready(key) or item is None:
                return True
            cached = self._detail_store_snapshot(key, item=item)
            cached_path = artwork_path(cached.get('picon_path', '') or '')
            if cached_path and _valid_live_picon_file(cached_path):
                return True
            if not _safe_picon_url(getattr(item, 'icon', '')):
                return True
            if getattr(self, '_live_active_picon_fetch_state', None) is not None:
                return True
            deadline = float(getattr(self, '_live_active_picon_fetch_deadline', 0.0) or 0.0)
            if deadline > time.monotonic():
                return True
            self._schedule_live_active_picon(item, key)
            return True
        if not self._selected_detail_preview_ready(key):
            return False
        if item is not None:
            self._schedule_live_active_picon(item, key)
            return True
        return False

    def _live_active_picon_fired(self):
        if self._closed or self._fullscreen_active:
            return
        item = self._live_active_picon_fetch_item
        key = str(self._live_active_picon_fetch_key or '')
        if item is None or not key or (not self._selected_detail_preview_ready(key)):
            self._cancel_live_active_picon_fetch(clear_target=True)
            self._refresh_live_active_picon(force=True)
            return
        now = time.monotonic()
        remaining = self._live_active_picon_fetch_deadline - now
        if _is_stalker_client(self.client):
            started_at = float(self._preview_service_started_at or 0.0)
            if started_at > 0.0:
                quiet_remaining = STALKER_LIVE_PICON_QUIET_MS - max(0, int(round((now - started_at) * 1000)))
                remaining = max(remaining, quiet_remaining / 1000.0)
        if remaining > 0.01:
            self._live_active_picon_timer.start(max(10, int(round(remaining * 1000))), True)
            return
        cached = self._detail_store_snapshot(key, item=item)
        if cached.get('picon_checked'):
            if not cached.get('picon_path'):
                self._defer_live_active_picon_failure(item)
            self._refresh_live_active_picon(force=True)
            return
        generation = self._live_active_picon_fetch_generation
        event = threading.Event()
        token = _WorkerToken(max(3, LIVE_PICON_DOWNLOAD_TIMEOUT + 1))
        self._live_active_picon_fetch_state = {'generation': generation, 'stream_id': key, 'event': event, 'token': token}
        worker = threading.Thread(target=self._load_live_active_picon_worker, args=(generation, key, item, event, self._detail_store_epoch, token))
        worker.daemon = True
        worker.start()
        self._live_active_picon_poll_timer.start(75, True)

    def _load_live_active_picon_worker(self, generation, stream_id, item, worker_event, store_epoch=None, worker_token=None):
        try:
            path = self._load_detail_picon_path(item, worker_token)
            if worker_token is not None:
                worker_token.check()
            if generation != self._live_active_picon_fetch_generation:
                return
            self._store_detail_component(stream_id, 'picon_path', path, expected_epoch=store_epoch)
        except _WorkerCancelled:
            pass
        except Exception:
            url = _safe_picon_url(getattr(item, 'icon', ''))
            if url:
                _mark_picon_failure(url)
        finally:
            worker_event.set()

    def _live_active_picon_poll(self):
        if self._closed or self._fullscreen_active:
            return
        state = self._live_active_picon_fetch_state
        if state is None:
            return
        generation = int(state.get('generation', -1))
        event = state.get('event')
        token = state.get('token')
        timed_out = False
        if event is not None and (not event.is_set()):
            if token is not None and token.expired():
                token.cancel()
                timed_out = True
            else:
                self._live_active_picon_poll_timer.start(75, True)
                return
        if generation != self._live_active_picon_fetch_generation:
            self._live_active_picon_fetch_state = None
            return
        key = str(state.get('stream_id', '') or '')
        if not self._selected_detail_preview_ready(key):
            can_settle_reference = bool(not timed_out and (event is None or event.is_set()) and (VideoWindow is not None) and getattr(self, '_preview_service_active', False) and (key == str(getattr(self, '_playing_preview_key', '') or '')) and (getattr(self, '_playing_preview_reference', None) is not None))
            if can_settle_reference:
                now = time.monotonic()
                settle_deadline = float(state.get('reference_settle_deadline', 0.0) or 0.0)
                if settle_deadline <= 0.0:
                    settle_seconds = (LIVE_PREVIEW_VIDEO_INITIAL_MS + LIVE_PREVIEW_VIDEO_MAX_CHECKS * LIVE_PREVIEW_VIDEO_RETRY_MS) / 1000.0
                    settle_deadline = now + settle_seconds
                    state['reference_settle_deadline'] = settle_deadline
                if now < settle_deadline:
                    self._live_active_picon_poll_timer.start(75, True)
                    return
            self._live_active_picon_fetch_state = None
            return
        self._live_active_picon_fetch_state = None
        item = self._live_active_picon_fetch_item
        if timed_out and item is not None:
            url = _safe_picon_url(getattr(item, 'icon', ''))
            if url:
                _mark_picon_failure(url)
        cached = self._detail_store_snapshot(key, item=item)
        if not cached.get('picon_path') and item is not None:
            self._defer_live_active_picon_failure(item)
        self._refresh_live_active_picon(force=True)

    def _load_selected_picon_worker(self, generation, stream_id, item, worker_event, store_epoch=None, worker_token=None):
        try:
            path = self._load_detail_picon_path(item, worker_token)
            self._publish_detail_component(generation, stream_id, 'picon_path', path, store_epoch=store_epoch)
        except _WorkerCancelled:
            pass
        finally:
            worker_event.set()

    def _schedule_detail_prefetch(self):
        return

    def _detail_prefetch_fired(self):
        return

    def _load_detail_prefetch_worker(self, generation, jobs, worker_event, store_epoch=None, worker_token=None):
        try:
            for stream_id, item, needs_epg, needs_picon in jobs:
                if worker_token is not None:
                    worker_token.check()
                if generation != self._detail_generation:
                    return
                if needs_epg:
                    events = []
                    loader = self._selected_epg_loader()
                    if loader is not None:
                        events = _bounded_client_call(self.client, worker_token, lambda sid=stream_id, selected_loader=loader: _load_shared_live_epg(self.client, sid, LIVE_EPG_FETCH_LIMIT, selected_loader, token=worker_token))
                    if worker_token is not None:
                        worker_token.check()
                    self._store_detail_component(stream_id, 'events', list(events or []), expected_epoch=store_epoch)
                if needs_picon:
                    path = self._load_detail_picon_path(item, worker_token)
                    self._store_detail_component(stream_id, 'picon_path', path, expected_epoch=store_epoch)
        except _WorkerCancelled:
            pass
        finally:
            worker_event.set()

    def _detail_poll(self):
        if self._closed or self._fullscreen_active:
            return
        generation = self._detail_generation
        state = self._detail_foreground_state
        worker_active = False
        worker_finished = False
        timed_out_components = []
        if state is not None and state.get('generation') == generation:
            for component, event in state.get('events', {}).items():
                token = state.get('tokens', {}).get(component)
                if not event.is_set() and token is not None and token.expired():
                    token.cancel()
                    timed_out_components.append(component)
            worker_active = self._detail_state_active(state)
            worker_finished = not worker_active
        with self._detail_results_lock:
            results = self._detail_results.pop(generation, [])
        if 'events' in timed_out_components and state is not None:
            stream_id = str(state.get('stream_id', '') or '')
            result_already_published = any((result[0] == stream_id and result[1] == 'events' for result in results))
            if stream_id and (not result_already_published):
                self._store_detail_component(stream_id, 'events', [])
                results.append((stream_id, 'events', []))
        for stream_id, component, value in results:
            if stream_id != self._detail_selection_key:
                continue
            if component == 'events':
                self._preview_epg_events = list(value or [])
                self._preview_epg_loaded_at = time.time()
                self._render_selected_epg(self._preview_epg_events)
                self._refresh_live_row_epg()
                if self._preview_epg_events:
                    self._detail_progress_timer.start(LIVE_DETAIL_PROGRESS_INTERVAL_MS, True)
                else:
                    self._schedule_selected_epg_retry(stream_id)
            elif component == 'picon_path':
                path = str(value or '').strip()
                self._detail_picon_path = path if path and _valid_live_picon_file(path) else ''
                self._detail_picon_loaded_at = time.monotonic() if self._detail_picon_path else 0.0
                self._refresh_live_active_picon(force=True)
        if worker_active:
            self._detail_poll_timer.start(75, True)
        elif worker_finished:
            self._detail_foreground_state = None
            self._schedule_detail_prefetch()

    def _detail_window(self, now=None):
        events = list(self._preview_epg_events or [])
        if not events:
            return []
        current_time = int(time.time() if now is None else now)
        for index, event in enumerate(events):
            start = int(getattr(event, 'start_timestamp', 0) or 0)
            end = int(getattr(event, 'end_timestamp', 0) or 0)
            if start and end and (start <= current_time < end) or (end and end > current_time):
                return events[index:]
        if any((not int(getattr(event, 'end_timestamp', 0) or 0) for event in events)):
            return events
        return []

    def _detail_progress_tick(self):
        if self._closed or self._fullscreen_active:
            return
        window = self._detail_window()
        if not window:
            self._clear_epg_progress()
            return
        if _epg_event_starts_in_future(window[0]):
            self._clear_epg_progress()
            self._detail_progress_timer.start(LIVE_DETAIL_PROGRESS_INTERVAL_MS, True)
            return
        if window[0] is not self._preview_epg_events[0]:
            self._preview_epg_events = window
            self._render_selected_epg(window)
        else:
            self._set_epg_progress(window[0])
        self._detail_progress_timer.start(LIVE_DETAIL_PROGRESS_INTERVAL_MS, True)

    def _render_detail_waiting(self, item):
        self._schedule_programme_poster()
        ellipsize_dynamic_text(self['ribbon_channel'], localized_upper(item.name), fallback_chars=58)
        self['ribbon_now_time'].setText('--:--')
        self._set_static_now_title(_('Waiting for programme information'))
        self['ribbon_next_caption'].setText(_('NEXT'))
        self['ribbon_next_time'].setText('')
        self._set_static_next_title('')
        for row in ('now', 'next', 'third', 'fourth', 'fifth'):
            try:
                self['epg_{}_time'.format(row)].setText('')
                self['epg_{}_title'.format(row)].setText('')
                self['epg_{}_status'.format(row)].setText('')
            except KeyError:
                pass
        self._clear_epg_progress()
        self._set_summary(_('Waiting for programme information'))

    @broadcast_refresh_after
    def _render_selected_epg(self, events):
        self._schedule_programme_poster()
        unused_width, unused_height, px = _scale()
        events = self._detail_window() if events else []
        if not events:
            self['ribbon_now_time'].setText('--:--')
            self._set_static_now_title(_('EPG information unavailable.'))
            self['ribbon_next_caption'].setText(_('NEXT'))
            self['ribbon_next_time'].setText('')
            self._set_static_next_title(_('EPG information unavailable.'))
            self['epg_now_time'].setText('')
            self['epg_now_title'].setText('')
            self['epg_now_status'].setText('')
            for row in ('next', 'third', 'fourth', 'fifth'):
                try:
                    self['epg_{}_time'.format(row)].setText('')
                    self['epg_{}_title'.format(row)].setText('')
                    self['epg_{}_status'.format(row)].setText('')
                except KeyError:
                    pass
            self._clear_epg_progress()
            self._set_summary(_('No summary is available for this programme.'))
            return
        future_only = _epg_event_starts_in_future(events[0])
        current = None if future_only else events[0]
        if current is None:
            self['ribbon_now_time'].setText('--:--')
            self._set_static_now_title(_('EPG information unavailable.'))
            self['epg_now_time'].setText('')
            self['epg_now_title'].setText('')
            self['epg_now_status'].setText('')
            self._clear_epg_progress()
        else:
            self['ribbon_now_time'].setText(current.time_text() or '--:--')
            self._set_now_title(current.title)
            self['epg_now_time'].setText(current.start or current.time_text() or '--:--')
            ellipsize_dynamic_text(self['epg_now_title'], current.title, fallback_chars=54)
            self['epg_now_status'].setText(_('NOW'))
            self._set_epg_progress(current)
        rows = ('next', 'third', 'fourth', 'fifth')
        statuses = (_('NEXT'), _('LATER'), _('LATER'), _('LATER'))
        first_upcoming = 0 if future_only else 1
        for row_offset, row in enumerate(rows):
            try:
                time_label = self['epg_{}_time'.format(row)]
                title_label = self['epg_{}_title'.format(row)]
                status_label = self['epg_{}_status'.format(row)]
            except KeyError:
                continue
            offset = first_upcoming + row_offset
            event = events[offset] if offset < len(events) else None
            time_label.setText(event.start or event.time_text() if event is not None else '')
            if event is None:
                title_label.setText('')
            else:
                fit_dynamic_text(title_label, event.title, max_lines=2, preferred_size=font_px(px, 22, max_height=64, vertical_padding=8), min_size=font_px(px, 18, max_height=64, vertical_padding=8), fallback_chars=54)
            status_label.setText(statuses[row_offset] if event is not None else '')
        upcoming = events[first_upcoming] if first_upcoming < len(events) else None
        self['ribbon_next_caption'].setText(_('NEXT'))
        self['ribbon_next_time'].setText(upcoming.start or upcoming.time_text() if upcoming is not None else '')
        if upcoming is None:
            self._set_static_next_title(_('EPG information unavailable.'))
        else:
            fit_dynamic_text(self['ribbon_next_title'], upcoming.title, max_lines=1, preferred_size=self._ribbon_next_preferred_font, min_size=self._ribbon_next_min_font, fallback_chars=36)
        summary = getattr(current, 'description', '') if current is not None else ''
        if summary:
            self._set_summary(summary)
        else:
            self._set_summary(_('No summary is available for this programme.'))

    def _set_now_title(self, value):
        return fit_dynamic_text(self['ribbon_now_title'], value, max_lines=2, preferred_size=self._ribbon_now_preferred_font, min_size=self._ribbon_now_min_font, fallback_chars=36)

    def _set_static_now_title(self, value):
        return set_static_text(self['ribbon_now_title'], value, preferred_size=self._ribbon_now_preferred_font)

    def _set_static_next_title(self, value):
        return set_static_text(self['ribbon_next_title'], value, preferred_size=self._ribbon_next_preferred_font)

    def _clear_live_active_picon(self, reset_signature=True):
        self._live_active_picon_generation += 1
        self._live_active_picon_widget_owners = {}
        try:
            loaders = getattr(self, '_gt_pixmap_loaders', {})
            loader = loaders.pop('live-active-row-picon', None)
            if loader is not None:
                loader.close()
        except Exception:
            pass
        if not callable(getattr(self, '_refresh_live_visible_picons', None)):
            for row_index in range(int(getattr(self, 'page_size', LIVE_STREAM_PAGE_SIZE))):
                try:
                    self['stream_picon_{}'.format(row_index)].hide()
                except Exception:
                    pass
        if reset_signature:
            self._live_active_picon_signature = None

    def _live_active_picon_target(self, page_start=None, page_items=None):
        """Return the visible exact-service row and its local picon path."""
        entries = list(getattr(self, '_entries', ()) or ())
        if page_start is None:
            selected_index = int(getattr(self, 'selected_index', 0) or 0)
            page_size = max(1, int(getattr(self, 'page_size', 1) or 1))
            page_start = int(selected_index / page_size) * page_size
        page_start = int(page_start)
        if page_items is None:
            page_size = max(1, int(getattr(self, 'page_size', 1) or 1))
            page_items = entries[page_start:page_start + page_size]
        page_items = tuple(page_items or ())
        key = str(getattr(self, '_playing_preview_key', '') or '')
        if not key or not getattr(self, '_preview_service_active', False) or getattr(self, '_fullscreen_active', False) or (not self._same_service_reference(self._current_navigation_reference(), getattr(self, '_playing_preview_reference', None))):
            return (page_start, -1, '', '')
        row_index = -1
        try:
            active_index = int(getattr(self, '_playing_preview_index', -1))
        except (TypeError, ValueError, OverflowError):
            active_index = -1
        candidate_row = active_index - page_start
        if 0 <= candidate_row < len(page_items):
            if self._selection_key(page_items[candidate_row]) == key:
                row_index = candidate_row
        if row_index < 0:
            for offset, item in enumerate(page_items):
                if self._selection_key(item) == key:
                    row_index = offset
                    break
        if row_index < 0:
            return (page_start, -1, key, '')
        item = page_items[row_index]
        cached = self._detail_store_snapshot(key, item=item)
        path = artwork_path(cached.get('picon_path', '') or '')
        if not path or not _valid_live_picon_file(path):
            path = ''
        return (page_start, row_index, key, path)

    @broadcast_refresh_after
    def _refresh_live_active_picon(self, force=False, page_start=None, page_items=None):
        signature = self._live_active_picon_target(page_start=page_start, page_items=page_items)
        visible_refresh = getattr(self, '_refresh_live_visible_picons', None)
        selected_refresh = getattr(self, '_refresh_selected_picon', None)
        if not callable(visible_refresh):
            if not force and signature == self._live_active_picon_signature:
                return
            self._clear_live_active_picon(reset_signature=False)
            self._live_active_picon_signature = signature
            unused_page, row_index, unused_key, path = signature
            if row_index < 0 or not path:
                return
            generation = self._live_active_picon_generation
            widget_name = 'stream_picon_{}'.format(row_index)
            self._live_active_picon_widget_owners[widget_name] = generation

            def loaded(success):
                if generation != self._live_active_picon_generation or signature != self._live_active_picon_signature:
                    if widget_name not in self._live_active_picon_widget_owners:
                        try:
                            self[widget_name].hide()
                        except Exception:
                            pass
                    return
                if getattr(self, '_closed', False) or self._fullscreen_active or signature != self._live_active_picon_target():
                    try:
                        self[widget_name].hide()
                    except Exception:
                        pass
                    return
                if success:
                    self[widget_name].show()
                else:
                    self[widget_name].hide()
            attach_native_alpha_pixmap(self, widget_name, path, key='live-active-row-picon', on_loaded=loaded)
            return
        self._live_active_picon_signature = signature
        visible_refresh(force=force, page_start=page_start, page_items=page_items)
        if callable(selected_refresh):
            selected_refresh(force=force)

    @broadcast_refresh_after
    def _update_epg_clock(self):
        now = datetime.datetime.now()
        try:
            self['ribbon_clock'].setText(now.strftime('%H:%M'))
            self['clock_time'].setText(now.strftime('%H:%M:%S'))
            localized = localized_date_text(now)
            year = str(now.year)
            if year not in localized:
                parts = localized.rsplit(' ', 1)
                if len(parts) == 2:
                    weekday = parts[1]
                    date_line = '{} {}'.format(parts[0], year)
                else:
                    weekday = localized_short_weekday(now.date().isoformat())
                    date_line = '{} {}'.format(localized, year)
            else:
                weekday = localized_short_weekday(now.date().isoformat())
                date_line = localized
            self['clock_date'].setText('{}\n{}'.format(weekday, date_line))
        except Exception:
            pass
        minute = now.strftime('%Y%m%d%H%M')
        if minute != self._live_clock_minute:
            self._live_clock_minute = minute
            self._refresh_live_row_epg()

    def start_epg_clock(self):
        if self._closed or self._fullscreen_active:
            return
        self._update_epg_clock()
        self._epg_clock_timer.start(1000, True)

    def _epg_clock_tick(self):
        if self._closed or self._fullscreen_active:
            return
        self._update_epg_clock()
        self._schedule_programme_poster()
        self._epg_clock_timer.start(1000, True)

    def _clear_epg_progress(self):
        self['epg_progress'].setText('')
        self['epg_progress_fill'].hide()

    def _set_epg_progress(self, event):
        ratio, value = self._progress_details(event)
        percent = value.split('•', 1)[0].strip() if value else ''
        self['epg_progress'].setText(percent)
        fill = self['epg_progress_fill']
        if not value:
            fill.hide()
            return
        try:
            fill.instance.resize(eSize(max(1, int(round(self._progress_width * ratio))), self._progress_height))
        except Exception:
            pass
        fill.show()

    def _refit_live_summary(self):
        if not self._closed and self._summary_text:
            self._set_summary(self._summary_text, force=True)

    @broadcast_refresh_after
    def _set_summary(self, summary, force=False):
        rendered = clean_dynamic_text(summary)
        selection_key = self._detail_selection_key
        text_changed = rendered != self._summary_text
        selection_changed = selection_key != self._summary_selection_key
        if not force and (not text_changed) and (not selection_changed):
            return rendered
        self._summary_scroll_timer.stop()
        self._summary_text = rendered
        self._summary_selection_key = selection_key
        self._summary_lines = _wrap_live_summary(rendered, self._summary_line_capacity, fits=lambda value: _line_fits(self['summary'], value, self._summary_text_width, self._summary_font_size, None))
        self._summary_line_offset = 0
        self._render_summary_window()
        if len(self._summary_lines) > self._summary_visible_lines:
            self._summary_scroll_timer.start(3500, True)
        return rendered

    def _render_summary_window(self):
        lines = self._summary_lines
        offset = self._summary_line_offset
        visible = self._summary_visible_lines
        self['summary'].setText('\n'.join(lines[offset:offset + visible]))
        self._refresh_summary_scroll_indicator()

    def _summary_scroll_tick(self):
        if self._closed or self._fullscreen_active:
            return
        last = max(0, len(self._summary_lines) - self._summary_visible_lines)
        if not last:
            return
        if self._summary_line_offset >= last:
            self._summary_line_offset = 0
            delay = 3500
        else:
            self._summary_line_offset += 1
            delay = 4500 if self._summary_line_offset == last else 3500
        self._render_summary_window()
        self._summary_scroll_timer.start(delay, True)

    def _refresh_summary_scroll_indicator(self):
        if self._closed or self._fullscreen_active:
            return
        total = len(self._summary_lines)
        visible = self._summary_visible_lines
        if total <= visible:
            self['summary_scroll'].setText('')
            hide_scrollbar(self, 'summary_scrollbar')
            return
        position = min(max(0, self._summary_line_offset), total - visible)
        pages = max(2, int(math.ceil(float(total) / visible)))
        current = 1 + int(round(float(position) * (pages - 1) / (total - visible)))
        self['summary_scroll'].setText('{}/{}'.format(current, pages))
        selected = int(round(float(position) * (total - 1) / (total - visible)))
        update_scrollbar(self, 'summary_scrollbar', total, selected, visible, self._summary_scrollbar_geometry)

    def _snapshot_for_selected(self, item):
        stream_id = self._selection_key(item)
        selected = self._detail_store_snapshot(stream_id, item=item)
        matches = self._preview_item is not None and self._selection_key(self._preview_item) == stream_id
        now = time.monotonic()
        preview_epg_fresh = bool(self._preview_epg_events and (not self._preview_epg_loaded_at or time.time() - self._preview_epg_loaded_at < LIVE_DETAIL_CACHE_TTL_SECONDS))
        preview_picon_fresh = bool(self._detail_picon_path and _valid_live_picon_file(self._detail_picon_path) and (not self._detail_picon_loaded_at or now - self._detail_picon_loaded_at < LIVE_DETAIL_CACHE_TTL_SECONDS))
        if matches and (not selected.get('events')) and preview_epg_fresh:
            selected['events'] = list(self._preview_epg_events or [])
        if matches and (not selected.get('picon_path')) and preview_picon_fresh:
            path = artwork_path(self._detail_picon_path or '')
            selected['picon_path'] = path
        channels = {}
        with self._detail_store_lock:
            stored_channels = {key: dict(value) for key, value in self._detail_store.items()}
        for key, entry in stored_channels.items():
            events = []
            if entry.get('events') and now - float(entry.get('epg_cached_at', 0.0) or 0.0) < LIVE_DETAIL_CACHE_TTL_SECONDS:
                events = list(entry['events'])
            path = artwork_path(entry.get('picon_path', '') or '')
            if not path or not _valid_live_picon_file(path) or now - float(entry.get('picon_cached_at', 0.0) or 0.0) >= LIVE_DETAIL_CACHE_TTL_SECONDS:
                path = ''
            if not events and (not path):
                continue
            channels[str(key)] = {'stream_id': str(key), 'events': events, 'picon_path': path, 'captured_at': max(float(entry.get('epg_captured_at', 0.0) or 0.0), float(entry.get('picon_captured_at', 0.0) or 0.0)) or time.time()}
        channels[stream_id] = {'stream_id': stream_id, 'events': list(selected.get('events', ())), 'picon_path': artwork_path(selected.get('picon_path', '') or ''), 'captured_at': float(selected.get('captured_at', time.time()) or time.time())}
        return {'stream_id': stream_id, 'events': list(selected.get('events', ())), 'picon_path': artwork_path(selected.get('picon_path', '') or ''), 'weather_forecast': self._weather_forecast_snapshot, 'captured_at': time.time(), 'channels': channels}

    def open_selected(self):
        if self._channel_move is not None:
            self.finish_channel_move()
            return
        if not self._entries or self._loading or self._fullscreen_active:
            return
        item = self._entries[self.selected_index]
        key = self._selection_key(item)
        current = self._current_navigation_reference()
        if self._preview_service_active and key == self._playing_preview_key and self._same_service_reference(current, self._playing_preview_reference):
            self._open_preview_fullscreen(item, self.selected_index, self._playing_preview_reference)
            return
        if VideoWindow is not None and self._preview_service_active and (key == self._playing_preview_key):
            started_at = float(self._preview_service_started_at or 0.0)
            settle_seconds = (LIVE_PREVIEW_VIDEO_INITIAL_MS + LIVE_PREVIEW_VIDEO_MAX_CHECKS * LIVE_PREVIEW_VIDEO_RETRY_MS) / 1000.0
            if started_at <= 0.0 or time.monotonic() - started_at <= settle_seconds:
                self['message'].setText(_('Please wait'))
                self._resume_preview_pig()
                return
            self._open_preview_fullscreen(item, self.selected_index, self._playing_preview_reference)
            return
        with self._preview_link_lock:
            same_request_pending = bool(self._preview_link_pending and key == self._preview_target_key)
        if same_request_pending:
            self['message'].setText(_('Please wait'))
            return
        try:
            self._preview_dwell_timer.stop()
        except Exception:
            pass
        self._preview_auto_suspended_key = ''
        self._begin_preview_link(item, self.selected_index, request_fullscreen=VideoWindow is None)

    def _open_preview_fullscreen(self, item, index, reference):
        if self._closed or self._fullscreen_active:
            return
        recovery = getattr(self, '_live_startup_recovery', None)
        if recovery is not None and recovery.busy:
            return
        current = self._current_navigation_reference()
        if not self._same_service_reference(current, reference):
            self._restore_pre_list_service()
            if self._preview_service_active and VideoWindow is None:
                self._relinquish_hidden_preview(self._selection_key(item))
            if VideoWindow is None:
                message = _('Could not open the stream.')
                self['message'].setText(message)
                self._show_preview_fallback(message)
                return
            if self._preview_service_active:
                message = _('Could not open the stream.')
                self['message'].setText(message)
                self._show_preview_fallback(message)
                return
            self._begin_preview_link(item, index, request_fullscreen=False)
            return
        self._fullscreen_link_snapshot = self._snapshot_for_selected(item)
        self._cancel_live_page_epg()
        self._cancel_preview_resolution(clear_target=True)
        self._cancel_selected_detail(clear_selection=False)
        cancel_prefetch = getattr(self, '_cancel_live_picon_prefetch', None)
        if callable(cancel_prefetch):
            cancel_prefetch(clear_render=True)
        self._cancel_live_active_picon_fetch(clear_target=True)
        self._clear_live_active_picon()
        with self._live_more_worker_events_lock:
            active_catalog_tokens = tuple(self._live_more_worker_tokens)
            self._live_more_token = None
            self._live_more_results = {}
        for token in active_catalog_tokens:
            _cancel_worker_token(token)
        self._live_more_loading = False
        for timer in (self._timer, self._epg_clock_timer):
            try:
                timer.stop()
            except Exception:
                pass
        if recovery is not None:
            recovery.detach()
        self._fullscreen_active = True
        self._stop_preview_stream_diag()
        instance = getattr(self, 'instance', None)
        if VideoWindow is not None and bool(getattr(self, 'shown', False)) and callable(getattr(instance, 'hide', None)) and callable(getattr(instance, 'show', None)):
            self._fullscreen_osd_trace = {'id': uuid.uuid4().hex[:8], 'started': time.monotonic(), 'count': 0}
            _trace_fullscreen_osd(self, 'list-handoff-begin')
            self._preview_fullscreen_pending = (item, int(index), reference, recovery, self._selection_key(item))
            self._preview_fullscreen_surface_hidden = True
            self._preview_fullscreen_ready_at = time.monotonic() + 0.12
            try:
                self._capture_preview_video_geometry()
                self._preview_video_timer.stop()
                _disable_live_window_animation(self)
                self.hide()
                self._preview_fullscreen_timer.start(120, True)
                return
            except Exception:
                self._preview_fullscreen_timer.stop()
                self._preview_fullscreen_pending = None
                self._fullscreen_active = False
                self._restore_preview_fullscreen_surface()
                self._fullscreen_active = True
        self._suspend_preview_pig()
        self._open_preview_fullscreen_player(item, index, reference, recovery)

    def _open_preview_fullscreen_player(self, item, index, reference, recovery):
        _trace_fullscreen_osd(self, 'player-open-request')
        from .browser_player import GTExternalPlayerScreen
        arguments = (GTExternalPlayerScreen, reference, item, self.client, list(self._entries), int(index))
        opener = getattr(self.session, 'openWithCallback', None)
        try:
            if opener is not None:
                opener(self._fullscreen_closed, *arguments, keep_playing_on_exit=True, live_snapshot=self._fullscreen_link_snapshot, favorite_store=self.favorite_store, favorite_keys=self._favorite_keys, adopt_playing_service=True, fullscreen_osd_trace=self._fullscreen_osd_trace, old_reference_override=self._pre_list_reference, live_startup_recovery=recovery, stream_diag_play_id=self._stream_diag_play_id, stream_diag_started_at=self._stream_diag_started_at, live_hidden_category_ids=self._hidden_category_ids, live_category_id=getattr(self.category, 'category_id', ''), live_list_context=self._live_channel_list_context(), live_source_categories=getattr(self, '_source_categories', None))
                return
            dialog = self.session.open(*arguments, keep_playing_on_exit=True, live_snapshot=self._fullscreen_link_snapshot, favorite_store=self.favorite_store, favorite_keys=self._favorite_keys, adopt_playing_service=True, fullscreen_osd_trace=self._fullscreen_osd_trace, old_reference_override=self._pre_list_reference, live_startup_recovery=recovery, stream_diag_play_id=self._stream_diag_play_id, stream_diag_started_at=self._stream_diag_started_at, live_hidden_category_ids=self._hidden_category_ids, live_category_id=getattr(self.category, 'category_id', ''), live_list_context=self._live_channel_list_context(), live_source_categories=getattr(self, '_source_categories', None))
            on_close = getattr(dialog, 'onClose', None)
            if isinstance(on_close, list):
                on_close.append(lambda: self._fullscreen_closed(getattr(dialog, '_fullscreen_close_result', None)))
                return
        except Exception as error:
            _debug('fullscreen open failed error={}'.format(error.__class__.__name__))
        self._fullscreen_open_failed()

    def _fullscreen_open_failed(self):
        self._preview_stream_diag('fullscreen', 'outcome=error')
        self._fullscreen_link_snapshot = None
        self._fullscreen_active = False
        if getattr(self, '_live_startup_recovery', None) is not None:
            arm_startup = getattr(self, '_arm_live_startup', None)
            if callable(arm_startup):
                arm_startup()
        self['message'].setText(_('Could not open the stream.'))
        if self._live_more_loading:
            self._timer.start(100, True)
        self.start_epg_clock()
        self._detail_selection_key = None
        self._refresh_live_streams()
        if VideoWindow is None:
            suspended_key = self._playing_preview_key
            if self._entries:
                suspended_key = self._selection_key(self._entries[self.selected_index])
            self._relinquish_hidden_preview(suspended_key)
            self._show_preview_fallback(_('Could not open the stream.'))
            return
        self._schedule_selected_detail()
        if self._preview_service_active:
            ensure_picon = getattr(self, '_ensure_live_active_picon', None)
            if callable(ensure_picon):
                ensure_picon()
            self._resume_preview_pig()
        else:
            self._show_preview_fallback(_('Could not open the stream.'))

    def _adopt_recalled_live_category(self, context, item):
        if context is None or not live_channel_key(item):
            return False
        if context.category_id == str(getattr(self.category, 'category_id', '') or ''):
            return False
        if context.category_id in self._hidden_category_ids:
            return False
        entries = filter_hidden_category_items(context.entries, self._hidden_category_ids, context.category_id)
        index = next((index for index, entry in enumerate(entries) if live_channel_key(entry) == live_channel_key(item)), None)
        if index is None:
            return False
        _cancel_worker_token(getattr(self, '_load_token', None))
        self._load_token = None
        self._load_generation += 1
        self._cancel_live_page_epg()
        self._cancel_preview_resolution(clear_target=True)
        self._cancel_selected_detail(clear_selection=True)
        self._cancel_live_picon_prefetch(clear_render=True)
        self._cancel_live_active_picon_fetch(clear_target=True)
        self._clear_live_active_picon()
        with self._live_more_worker_events_lock:
            tokens = tuple(self._live_more_worker_tokens)
            self._live_more_token = None
            self._live_more_results = {}
            self._live_catalog_generation += 1
        for token in tokens:
            _cancel_worker_token(token)
        self.category = context.category
        self._bind_live_channel_order()
        entries = arrange_channels(entries, self._channel_order)
        index = next((i for i, entry in enumerate(entries) if live_channel_key(entry) == live_channel_key(item)))
        self._entries = entries
        self.selected_index = index
        self._loading = False
        self._ready = True
        self._error = ''
        self._live_initial_applied = True
        self._live_has_more = context.has_more
        self._live_more_loading = False
        self._live_pending_target_index = None
        self._live_empty_more_batches = 0
        self._live_recall_context = None
        self._live_render_page_key = None
        self._set_dynamic_breadcrumb('{}  >  {}'.format(self.account.display_name, self.category.name))
        return True

    def _fullscreen_closed(self, result=None, *args):
        _trace_fullscreen_osd(self, 'player-return-callback')
        if self._closed:
            return
        selected_index = self.selected_index
        kept = False
        released = False
        reference = None
        returned_item = None
        returned_index = None
        if isinstance(result, dict) and result.get('kind') == LIVE_FULLSCREEN_RESULT_KIND:
            selected_index = result.get('selected_index', selected_index)
            kept = bool(result.get('kept'))
            released = bool(result.get('released')) and (not kept)
            reference = result.get('reference') if kept else None
            candidate = result.get('channel_item')
            if live_channel_key(candidate):
                if kept and reference is not None:
                    self._adopt_recalled_live_category(result.get('live_list_context'), candidate)
                returned_item = candidate
                returned_index = next((index for index, entry in enumerate(self._entries) if live_channel_key(entry) == live_channel_key(candidate)), -1)
                selected_index = returned_index if returned_index >= 0 else self.selected_index
        if self._entries:
            try:
                self.selected_index = int(selected_index) % len(self._entries)
            except (TypeError, ValueError, OverflowError):
                pass
        self._fullscreen_link_snapshot = None
        self._fullscreen_active = False
        if kept and reference is not None and self._entries:
            item = returned_item if returned_item is not None else self._entries[self.selected_index]
            self._playing_preview_item = item
            self._playing_preview_key = self._selection_key(item)
            self._playing_preview_index = returned_index if returned_item is not None else int(self.selected_index)
            self._playing_preview_reference = reference
            self._preview_service_active = True
            self._preview_service_started_at = time.monotonic()
            self._live_startup_recovery = result.get('live_startup_recovery', getattr(self, '_live_startup_recovery', None))
            if self._live_startup_recovery is not None:
                arm_startup = getattr(self, '_arm_live_startup', None)
                if callable(arm_startup):
                    arm_startup()
            self._pre_list_restored = False
            try:
                from .browser_player import reference_service_type
                self._active_live_service_type = int(result.get('service_type', reference_service_type(reference, 4097)))
            except (TypeError, ValueError, OverflowError):
                from .browser_player import reference_service_type
                self._active_live_service_type = reference_service_type(reference, 4097)
            self['engine_badge'].setText(service_engine_label(self._active_live_service_type))
            if VideoWindow is None:
                self._relinquish_hidden_preview(self._selection_key(item))
                kept = False
                reference = None
        else:
            suspended_key = ''
            if self._entries:
                suspended_key = self._selection_key(self._entries[self.selected_index])
            if released:
                self._pre_list_restored = True
                self._preview_auto_suspended_key = suspended_key if VideoWindow is None else ''
                self._clear_preview_service_state()
            elif self._preview_service_active:
                if VideoWindow is None:
                    self._preview_auto_suspended_key = suspended_key
                self._release_preview_for_exit()
            else:
                self._clear_preview_service_state()
                self._pre_list_restored = True
                self._preview_auto_suspended_key = suspended_key
        self._detail_selection_key = None
        self._reload_favorite_keys()
        self._live_render_page_key = None
        if self._live_more_loading:
            self._timer.start(100, True)
        self._refresh_live_streams()
        ensure_picon = getattr(self, '_ensure_live_active_picon', None)
        if callable(ensure_picon):
            ensure_picon()
        self.start_epg_clock()
        self._show_preview_fullscreen_list()

    def close_list(self):
        if self._channel_move is not None:
            self.cancel_channel_move()
            return
        if not self._release_preview_for_exit():
            message = _('Keeping the current stream')
            self['message'].setText(message)
            if VideoWindow is None:
                self._show_preview_fallback(message)
            elif self._preview_service_active:
                self._resume_preview_pig()
            return
        self.close({'kind': 'live-list-category', 'category_id': str(getattr(self.category, 'category_id', '') or '')})

    def _clear_preview_service_state(self):
        from .browser_player import _m3u_navigation_changed
        _m3u_navigation_changed(getattr(self.session, 'nav', None))
        self._stop_preview_stream_diag()
        cancel_startup = getattr(self, '_cancel_live_startup', None)
        if callable(cancel_startup):
            cancel_startup()
        cancel_picon = getattr(self, '_cancel_live_active_picon_fetch', None)
        if callable(cancel_picon):
            cancel_picon(clear_target=True)
        self._playing_preview_item = None
        self._playing_preview_key = ''
        self._playing_preview_index = -1
        self._playing_preview_reference = None
        self._preview_service_active = False
        self._preview_service_started_at = 0.0
        self._preview_target_item = None
        self._preview_target_key = ''
        self._preview_target_index = -1
        self._live_active_picon_reload_pending = False
        clear_picon = getattr(self, '_clear_live_active_picon', None)
        if callable(clear_picon):
            clear_picon()

    def _relinquish_hidden_preview(self, suspended_key=''):
        """Restore or stop a retained service which cannot be shown in PIG."""
        self._preview_auto_suspended_key = str(suspended_key or '')
        if not self._preview_service_active:
            return True
        self._restore_pre_list_service()
        if not self._preview_service_active:
            return True
        navigation = getattr(self.session, 'nav', None)
        stopped = False
        if navigation is not None:
            try:
                navigation.stopService()
                stopped = True
            except Exception:
                pass
        if stopped:
            self._pre_list_restored = True
            self._clear_preview_service_state()
        return not self._preview_service_active

    def _release_preview_for_exit(self):
        """Relinquish decoder ownership before the list actually closes."""
        if self._preview_service_active:
            suspended_key = ''
            if VideoWindow is None:
                suspended_key = self._preview_auto_suspended_key or self._playing_preview_key
            return self._relinquish_hidden_preview(suspended_key)
        self._restore_pre_list_service()
        return not self._preview_service_active

    def _restore_pre_list_service(self):
        """Restore the service present before this list, at most once."""
        cancel_startup = getattr(self, '_cancel_live_startup', None)
        if callable(cancel_startup):
            cancel_startup()
        self._cancel_preview_resolution(clear_target=True)
        self._suspend_preview_pig()
        if self._pre_list_restored or not self._preview_service_active:
            return
        navigation = getattr(self.session, 'nav', None)
        restored = False
        if navigation is not None:
            if self._pre_list_reference is None:
                try:
                    navigation.stopService()
                    restored = True
                except Exception:
                    pass
            else:
                try:
                    from .browser_player import _play_service
                    _play_service(navigation, self._pre_list_reference)
                    restored = True
                except Exception:
                    pass
        if restored:
            self._pre_list_restored = True
            self._clear_preview_service_state()

    def _stop(self):
        _trace_fullscreen_osd(self, 'list-close')
        self._preview_fullscreen_timer.stop()
        self._preview_fullscreen_pending = None
        self._preview_fullscreen_surface_hidden = False
        self._stop_preview_stream_diag()
        self._release_preview_for_exit()
        GTAsyncListScreen._stop(self)
        with self._live_more_worker_events_lock:
            active_catalog_tokens = tuple(self._live_more_worker_tokens)
            self._live_more_token = None
            self._live_more_results = {}
        for token in active_catalog_tokens:
            _cancel_worker_token(token)
        self._live_catalog_generation += 1
        self._live_more_loading = False
        self._cancel_live_page_epg()
        self._cancel_selected_detail(clear_selection=True)
        cancel_prefetch = getattr(self, '_cancel_live_picon_prefetch', None)
        if callable(cancel_prefetch):
            cancel_prefetch(clear_render=True)
        self._cancel_live_active_picon_fetch(clear_target=True)
        self._clear_live_active_picon()
        with self._preview_link_lock:
            self._preview_generation += 1
        self._cancel_preview_resolution(clear_target=True)
        for timer in (self._timer, self._epg_clock_timer, self._preview_dwell_timer, self._preview_link_timer, self._preview_video_timer, self._live_active_picon_timer, self._live_active_picon_poll_timer, self._live_picon_prefetch_poll_timer):
            try:
                timer.stop()
            except Exception:
                pass
