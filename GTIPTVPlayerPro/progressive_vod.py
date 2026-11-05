# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Refresh admitted rows and apply a saved order after the first GUI frame."""
import threading

from .content import ContentError
from .content_sort import INCOMPLETE
from .i18n import _
from .movie_browse import _CatalogToken
from .movie_catalog import CatalogLimitError, catalog_failure_message


def _error(caught):
    if isinstance(caught, (MemoryError, CatalogLimitError)):
        return catalog_failure_message(caught)
    return str(caught) or INCOMPLETE


class _FinalOrder:
    def __init__(self, catalog, items, category, hidden):
        self.catalog, self.items, self.index = catalog, items, catalog.index
        self.category, self.mode = category, catalog.sort_mode
        self.hidden, self.token = hidden, _CatalogToken()
        self.lock, self.done, self.cancelled = threading.Lock(), False, False
        self.view, self.error = None, ''
        self.native_menu_rows=None

    def run(self):
        view, error = None, ''
        try:
            view = self.catalog.prepare_sort(self.mode, self.category, self.hidden, self.token, _display=True)
            if getattr(self.items,'native_menu_rows',None) is not None:
                rows=self.items.generation.rows
                self.native_menu_rows=[(rows.native_labels[i][1],rows.native_labels[i][0]) for i in view.order]
            self.token.check()
        except Exception as caught:
            if view is not None:
                view.close()
                view = None
            error = _error(caught)
        with self.lock:
            if self.cancelled:
                if view is not None:
                    view.close()
            else:
                self.view, self.error = view, error
            self.done = True

    def cancel(self):
        self.token.cancel_event.set()
        with self.lock:
            self.cancelled = True
            if self.view is not None:
                self.view.close()
                self.view = None


def cancel(screen, kind):
    key = '_'+kind+'_final_order'
    job = getattr(screen, key, None)
    setattr(screen, key, None)
    if job is not None:
        job.cancel()


def defer_selection(screen, kind, index):
    prefix = '_'+kind+'_'
    items = getattr(screen, prefix+'entries', None)
    catalog = getattr(screen, prefix+'browser_catalog', None)
    if (getattr(items, 'progressive', False) and index >= len(items)
            and catalog is not None and (catalog.state.get('preparing') or items.pending)):
        setattr(screen, prefix+'pending_advance', True)
        setattr(screen, prefix+'pending_selection_index', index)
        return True
    return False


def poll(screen, kind):
    """GUI-thread work is bounded; full filtering/sorting uses a worker."""
    prefix = '_'+kind+'_'
    items = getattr(screen, prefix+'entries', None)
    catalog = getattr(screen, prefix+'browser_catalog', None)
    if (catalog is None or not getattr(items, 'progressive', False)
            or getattr(screen, '_closed', True) or getattr(screen, prefix+'loading', False)):
        return False
    if not catalog._started:
        cancel(screen, kind)
        return False
    state, changed = catalog.state, False
    was_finalized=items.finalized
    try:
        changed = items.refresh(blocking=False,batch=512)
    except ContentError:
        cancel(screen, kind)
        return False
    except Exception as caught:
        items.sort_error = _error(caught)
        items.finalized = True
    selected = getattr(screen, prefix+'selected_index', 0)
    old_selected = selected
    requested = getattr(screen, prefix+'pending_selection_index', None)
    if getattr(screen, prefix+'pending_advance', False) and requested is not None:
        ready = requested < len(items)
        terminal = not state.get('preparing') and not items.pending
        if ready or terminal:
            setattr(screen, prefix+'pending_advance', False)
            setattr(screen, prefix+'pending_selection_index', None)
            if items:
                getattr(screen, '_select_'+kind)(min(requested, len(items)-1))
                selected = getattr(screen, prefix+'selected_index', 0)
                changed = True
    restore = getattr(items, 'restore_identity', None)
    if restore is not None:
        identity, version = restore
        if version != getattr(screen, prefix+'generation', None):
            items.restore_identity = None
        else:
            position = items.position(identity)
            if position is not None:
                setattr(screen, prefix+'selected_index', position)
                selected, changed, items.restore_identity = position, True, None
    key = prefix+'final_order'
    job = getattr(screen, key, None)
    if job is not None and (job.items is not items or job.index is not catalog.index
                            or job.mode != catalog.sort_mode or not catalog._started):
        cancel(screen, kind)
        job = None
    if job is not None and job.done:
        setattr(screen, key, None)
        with job.lock:
            view, job.view = job.view, None
            error = job.error
        if view is not None:
            try:
                identity = (items.labels(selected,selected+1)[0][0]
                            if items and items.native_menu_rows is not None
                            else items[selected].stream_id if items else '')
                with catalog.condition:
                    if catalog._closed or catalog._full_list is not items or job.index is not catalog.index:
                        raise ContentError(reason=ContentError.CANCELLED)
                    items.apply_sort(view,job.native_menu_rows)
                    previous, catalog._sort_view = catalog._sort_view, view
                    if previous is not None and previous is not view:
                        previous.close()
                position = items.position(identity)
                setattr(screen, prefix+'selected_index', position if position is not None else 0)
                changed = True
            except Exception as caught:
                view.close()
                error = _error(caught)
        if error:
            items.sort_error, items.finalized = error, True
        job = None
    if (not state.get('preparing') and state.get('complete') and not items.pending
            and not items.finalized and job is None):
        if catalog.sort_mode == 'provider' and (items.direct or items.native_menu_rows is not None):
            items.finalized = True
        else:
            job = _FinalOrder(catalog, items, str(getattr(screen, prefix+'loaded_category_id', '') or ''),
                              getattr(screen, '_hidden_category_ids', ()))
            setattr(screen, key, job)
            worker = threading.Thread(target=job.run, daemon=True)
            try:
                worker.start()
            except (RuntimeError, OSError):
                cancel(screen, kind)
                items.sort_error, items.finalized = INCOMPLETE, True
                job = None
    if not state.get('preparing') and not state.get('complete') and not items.pending:
        items.finalized=True
    finalized_now=items.finalized!=was_finalized
    changed=changed or finalized_now
    if changed:
        getattr(screen, '_update_'+kind+'_rail_caption')()
        from .vod_render import refresh_growth
        if not refresh_growth(screen, kind, old_selected):
            getattr(screen, '_render_movie_posters' if kind == 'movie' else '_render_series_tiles')()
        remember = getattr(screen, '_remember_series_selection', None) if kind == 'series' else None
        if callable(remember):
            remember()
    # A page can finish between the state snapshot and the GUI refresh.
    # Keep one more poll when that snapshot still showed a running producer,
    # so EOF/final ordering cannot be missed after playback or navigation.
    pending = bool(job is not None or items.pending or state.get('preparing'))
    if items.sort_error:
        screen['message'].setText(_(items.sort_error))
    elif pending and not state.get('preparing'):
        screen['message'].setText(_('Loading...'))
    elif finalized_now and state.get('complete'):
        if getattr(screen,'_light_vod_enabled',False):screen._light_catalog_text=None
        getattr(screen,'_update_'+kind+'_catalog_message')()
    return pending
