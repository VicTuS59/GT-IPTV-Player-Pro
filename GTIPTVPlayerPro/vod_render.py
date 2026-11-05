"""Update a VOD page only when its visible content changes."""


def page_changed(screen, kind, key, page, focused):
    """Same-page navigation changes at most the old and new focus widgets."""
    prefix = '_' + kind + '_'
    signature = (key, getattr(screen, prefix + 'poster_generation', 0), tuple((item.name, getattr(item, 'icon', ''),
                            screen._is_favorite(item)) for item in page))
    selected = getattr(screen, prefix + 'selected_index', 0)
    slot = selected % screen._poster_page_size if focused and page else None
    previous = getattr(screen, prefix + 'paint_focus', None)
    changed = signature != getattr(screen, prefix + 'paint_signature', None)
    setattr(screen, prefix + 'paint_signature', signature)
    setattr(screen, prefix + 'paint_focus', slot)
    if not changed and previous != slot:
        if previous is not None:
            screen[kind + '_tile_focus_' + str(previous)].hide()
        if slot is not None:
            screen[kind + '_tile_focus_' + str(slot)].show()
    return changed


def refresh_count(screen, kind):
    """A growing catalogue must not restart artwork or repaint its first page."""
    if getattr(screen, '_light_vod_enabled', False):
        screen._light_refresh_count()
        return
    from .typography import set_text_if_changed
    from .scrollbar import update_horizontal_scrollbar
    entries = getattr(screen, '_' + kind + '_entries', ())
    selected = getattr(screen, '_' + kind + '_selected_index', 0)
    if getattr(screen, '_poster_grid_enabled', False):
        screen._update_grid_position(kind)
        return
    base = screen._vod_window_base(kind)
    update_horizontal_scrollbar(screen, 'poster_scroll', base + len(entries),
        base + selected, screen._poster_page_size, screen._poster_scroll_geometry)
    if getattr(screen, '_vod_discovery_layout', False):
        set_text_if_changed(screen['vod_counter'], '{} / {}{}'.format(
            base + selected + 1 if entries else 0, base + len(entries),
            '+' if getattr(screen, '_' + kind + '_has_more', False) else ''))


def refresh_growth(screen, kind, old_selected):
    """Return true when only the count changed outside the visible page."""
    if getattr(screen,'_light_vod_enabled',False):
        # Native list replacement publishes the appended text references in
        # one GUI transaction and restores the existing selection index.
        screen._light_render_rows()
        return True
    entries = getattr(screen, '_' + kind + '_entries', ())
    selected = getattr(screen, '_' + kind + '_selected_index', 0)
    size = getattr(screen, '_poster_page_size', 6)
    start = selected // size * size
    key = (start, tuple(str(item.stream_id) for item in entries[start:start + size]))
    if selected != old_selected or key != getattr(screen, '_' + kind + '_poster_page_key', None):
        return False
    refresh_count(screen, kind)
    return True
