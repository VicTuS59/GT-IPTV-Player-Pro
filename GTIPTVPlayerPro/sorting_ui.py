# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Yellow edit menus; background preparation commits only to its own screen."""
import sys
import threading

from .i18n import _, N_
from .content_sort import (SortStore, SortUnavailable, LABELS, SORT_TITLE, SORT_CONTENT,
    SORT_CHANNELS, NO_DATES, INCOMPLETE, SAVE_ERROR, Lease, heap_order,
    normalized_name, sort_live_rows, preference_scope, VOD_LABELS)

EDIT_CATEGORIES = N_('Edit categories')
SORT_CATEGORIES = N_('Sort categories')
ALPHA_DESC = N_('Alphabetical (Z–A)')
_worker = threading.local()


def live_worker(screen, token, callback):
    previous = getattr(_worker, 'token', None)
    _worker.token = token
    try: return callback()
    finally: _worker.token = previous


def list_timeout(screen, default):
    if (getattr(screen, 'content_type', None) == 'live'
            and hasattr(screen, '_channel_order_scope')
            and mode_for(screen, 'live', screen.category.category_id) in ('alpha','added')):
        return 120
    return default


def context(screen):
    kind = screen.content_type
    channels = hasattr(screen, '_channel_order_scope')
    category = screen.category if channels else (screen._entries[screen.selected_index] if screen._entries else None)
    if category is None: return None
    return kind, str(getattr(category, 'category_id', '') or ''), channels, category


def scope(screen, kind, category):
    from .playback import resume_account_scope
    return preference_scope(resume_account_scope(screen.client), kind, category)


def mode_for(screen, kind, category):
    default = 'manual' if kind == 'live' and getattr(screen, '_channel_order', ()) else 'provider'
    return SortStore().load(scope(screen, kind, category), default)


def _supported(screen):
    return str(getattr(screen.client, 'source_type', '') or getattr(getattr(screen.client, 'account', None), 'source_type', '')) in ('xtream','stalker')


def _message(screen, text):
    from Screens.MessageBox import MessageBox
    screen.session.open(MessageBox, _(text), type=MessageBox.TYPE_INFO)


def _choice(screen, title, options, callback):
    from Screens.ChoiceBox import ChoiceBox
    live = hasattr(screen, '_channel_order_scope')
    if live: screen._live_modal_active = True
    def selected(choice):
        if live: screen._live_modal_active = False
        if not getattr(screen, '_closed', True) and choice:
            callback(choice[1])
    screen.session.openWithCallback(selected, ChoiceBox, title=_(title), list=options)


def open_edit(screen):
    if not _supported(screen):
        return screen.open_category_manager() if not hasattr(screen, '_channel_order_scope') else screen.start_channel_move()
    if (getattr(screen, '_closed', True) or getattr(screen, '_loading', False)
            or getattr(screen, '_channel_move', None) is not None):
        return
    if getattr(screen, '_sorting_job', None) is not None: cancel(screen)
    ctx = context(screen)
    if ctx is None: return
    kind, category, channels, item = ctx
    if channels:
        options = [(_('Move channel'), 'move'), (_(SORT_CHANNELS), 'sort')]
    else:
        options = [(_(EDIT_CATEGORIES), 'categories'), (_(SORT_CATEGORIES), 'category_sort'),
                   (_(SORT_CHANNELS if kind == 'live' else SORT_CONTENT), 'sort')]
    def selected(action):
        if action == 'categories': screen.open_category_manager()
        elif action == 'category_sort': screen.open_category_manager('sort')
        elif action == 'move': screen.start_channel_move()
        else: open_sort(screen)
    _choice(screen, 'Edit', options, selected)


def _date_support(screen, ctx, mode):
    if mode not in ('added', 'release', 'year'): return True
    kind, category, channels, item = ctx
    if kind == 'live':
        if not channels or getattr(screen, '_live_has_more', True): return None
        return any(getattr(row, 'sort_added', 0) for row in screen._entries)
    catalog = getattr(screen, '_'+kind+'_browser_catalog', None)
    backend = getattr(getattr(catalog, 'index', None), '_memory', None)
    generation = backend.session.active if backend is not None else None
    if generation is None: return None
    return generation.rows.has_sort_added if mode == 'added' else generation.rows.has_sort_release


def open_sort(screen):
    ctx = context(screen)
    if ctx is None: return
    kind, category, channels, item = ctx
    current = mode_for(screen, kind, category)
    modes = ('provider','added','alpha','manual') if kind == 'live' else ('alpha','alpha_desc','added','year','provider')
    options = []
    for mode in modes:
        available = _date_support(screen, ctx, mode)
        marker = '✓ ' if mode == current else '— ' if available is False else '  '
        options.append((marker + _((LABELS if kind == 'live' else VOD_LABELS)[mode]), (mode, available)))
    def selected(value):
        mode, available = value
        if available is False: _message(screen, NO_DATES)
        else: prepare_choice(screen, ctx, mode)
    _choice(screen, SORT_TITLE, options, selected)


def cancel(screen):
    job, screen._sorting_job = getattr(screen, '_sorting_job', None), None
    if job is not None:
        job['token'].cancel()
        job['timer'].stop()
        job['cancelled'].set()
        with job['lock']:
            if job.get('done'): _dispose(job)
    if hasattr(screen, '_live_modal_active'): screen._live_modal_active = False


def _dispose(job):
    job.pop('entries', None)
    for key in ('view', 'lease'):
        value = job.pop(key, None)
        if value is not None: value.close()


def category_started(screen, kind, category):
    job = getattr(screen, '_sorting_job', None)
    if job is not None and job['identity'] != (kind, str(category or '')): cancel(screen)
    catalog = getattr(screen, '_'+kind+'_browser_catalog', None)
    if catalog is not None: catalog.configure_sort(mode_for(screen, kind, category))


def _full_live(screen, ctx, token, mode):
    from .category_visibility import filter_hidden_category_items
    from .live_channel_order import arrange_channels, channel_order_scope, LiveChannelOrderStore
    from .playback import resume_account_scope
    kind, category, channels, item = ctx
    lease = Lease()
    try:
        values, known = [], set()
        def append(batch):
            for entry in batch or ():
                token.check()
                key = str(entry.stream_id)
                if key in known: continue
                weight = sys.getsizeof(entry) + sys.getsizeof(vars(entry)) + sum(sys.getsizeof(v) for v in vars(entry).values()) + 256
                lease.reserve(weight)
                values.append(entry);known.add(key)
        append(screen.client.load_items('live', category))
        checker = getattr(screen.client, 'has_more_items', None)
        loader = getattr(screen.client, 'load_more_items', None)
        pages = 0
        while callable(checker) and checker('live', category):
            token.check();pages += 1
            if not callable(loader) or pages > 2000: raise SortUnavailable(INCOMPLETE)
            append(loader('live', category))
        token.check()
        values = filter_hidden_category_items(values, getattr(screen, '_hidden_category_ids', ()), category)
        if mode == 'manual':
            order = LiveChannelOrderStore().load(channel_order_scope(resume_account_scope(screen.client), category))
            values = arrange_channels(values, order)
        else: values = sort_live_rows(values, mode, token, lease)
        return values, lease
    except BaseException:
        lease.close()
        raise


def prepare_choice(screen, ctx, mode):
    from enigma import eTimer
    from .browser import _WorkerToken, _connect_timer, _bounded_client_call
    cancel(screen)
    if not getattr(screen, '_sorting_close_hook', False):
        screen._sorting_close_hook = True
        screen.onClose.append(lambda:cancel(screen))
        screen.onClose.append(lambda:release_live(screen))
        if hasattr(screen, 'onHide'):
            screen.onHide.append(lambda:cancel(screen) if getattr(screen, '_sorting_job', None) is not None else None)
    kind, category, channels, item = ctx
    token = _WorkerToken(120)
    timer = eTimer()
    job = dict(token=token, timer=timer, identity=(kind,category), lock=threading.Lock(), cancelled=threading.Event(), done=False)
    screen._sorting_job = job
    if channels:
        screen._live_modal_active = True
        more = getattr(screen, '_live_more_token', None)
        if more is not None: more.cancel()
        screen._live_catalog_generation += 1
        screen._live_more_loading = False
    screen['message'].setText(_('Loading...'))
    def work():
        try:
            if kind == 'live':
                entries, lease = _bounded_client_call(screen.client, token, lambda:_full_live(screen, ctx, token, mode))
                job['entries'], job['lease'] = entries, lease
            else:
                catalog = getattr(screen, '_'+kind+'_browser_catalog')
                if str(getattr(catalog, '_category_id', '')) != category:
                    catalog.start(preferred_category=category)
                job['view'] = (catalog.prepare_sort(mode, category, screen._hidden_category_ids, token)
                               if mode != 'provider' else None)
            token.check()
            if not SortStore().save(scope(screen, kind, category), mode): raise SortUnavailable(SAVE_ERROR)
        except Exception as error:
            job['error'] = str(error) if isinstance(error, SortUnavailable) else INCOMPLETE
        finally:
            with job['lock']:
                job['done'] = True
                if job['cancelled'].is_set(): _dispose(job)
    def poll():
        if screen._sorting_job is not job:
            timer.stop()
            return
        current = context(screen)
        if (getattr(screen,'_closed',True) or current is None
                or current[:2] != (kind,category)):
            cancel(screen);return
        if not job['done']:
            if token.expired():
                cancel(screen);_message(screen, INCOMPLETE);return
            timer.start(100, True);return
        screen._sorting_job = None
        timer.stop()
        if channels: screen._live_modal_active = False
        try:
            if job.get('error'):
                _message(screen, job['error']);return
            if kind == 'live' and channels:
                selected = screen._selection_key(screen._entries[screen.selected_index]) if screen._entries else ''
                release_live(screen)
                screen._sorting_live_lease = job.pop('lease')
                screen._entries = job.pop('entries')
                screen.selected_index = next((i for i,row in enumerate(screen._entries) if screen._selection_key(row)==selected),0)
                screen._live_has_more = False
                screen._reindex_live_preview();screen._refresh_live_streams()
            elif kind == 'live': _message(screen, 'Saved')
            else:
                catalog = getattr(screen, '_'+kind+'_browser_catalog')
                catalog.configure_sort(mode, job.pop('view'))
                # An explicit new ordering must show its first result. Regular
                # navigation/refresh still retain their own selection handling.
                setattr(screen, '_sorting_restore_'+kind, None)
                getattr(screen, '_start_'+kind+'_load')(item)
        finally: _dispose(job)
    _connect_timer(timer, poll)
    job['poll'] = poll
    threading.Thread(target=work, name='GT-ContentSorting', daemon=True).start()
    timer.start(100, True)


def restore_page(screen, catalog, page, kind, category, generation, token):
    request = getattr(screen, '_sorting_restore_'+kind, None)
    if request is not None and request[:2] == (generation, str(category or '')) and request[2]:
        position = catalog.sort_position(request[2], category, screen._hidden_category_ids, token)
        base = max(0, ((position-70)//70)*70)
        page = catalog.read_window(category, offset=base, size=240, hidden_category_ids=screen._hidden_category_ids, token=token)
        page.sort_base, page.sort_selected = base, position-base
    return page


def release_live(screen):
    lock = getattr(screen, '_sorting_live_lock', None)
    if lock is None: lock = screen._sorting_live_lock = threading.RLock()
    with lock:
        lease, screen._sorting_live_lease = getattr(screen, '_sorting_live_lease', None), None
        if lease is not None: lease.close()


def load_live(screen, initial):
    """Saved automatic orders load a complete bounded category on its worker."""
    if not getattr(screen, '_sorting_close_hook', False):
        screen._sorting_close_hook = True
        screen.onClose.append(lambda:cancel(screen))
        screen.onClose.append(lambda:release_live(screen))
        if hasattr(screen, 'onHide'):
            screen.onHide.append(lambda:cancel(screen) if getattr(screen, '_sorting_job', None) is not None else None)
    mode = mode_for(screen, 'live', screen.category.category_id)
    if mode in ('provider', 'manual'):
        from .live_channel_order import arrange_channels
        return arrange_channels(initial, screen._channel_order if mode == 'manual' else ())
    ctx = context(screen)
    token = getattr(_worker, 'token', None) or screen._load_token
    values, lease = _full_live(screen, ctx, token, mode)
    lock = getattr(screen, '_sorting_live_lock', None)
    if lock is None: lock = screen._sorting_live_lock = threading.RLock()
    with lock:
        if screen._closed or token is not screen._load_token or not token.active():
            lease.close();token.check();return []
        release_live(screen);screen._sorting_live_lease = lease
    screen._live_has_more = False
    return values


def apply_live_order(screen):
    from .live_channel_order import arrange_channels
    mode = mode_for(screen, 'live', screen.category.category_id)
    if mode == 'manual': screen._entries[:] = arrange_channels(screen._entries, screen._channel_order)


def save_manual(screen):
    store = SortStore()
    key = scope(screen, 'live', screen.category.category_id)
    previous = mode_for(screen, 'live', screen.category.category_id)
    if not store.save(key, 'manual'): return None
    order = screen._channel_order_store.save(screen._channel_order_scope, screen._entries, screen._channel_order)
    if order is None: store.save(key, previous)
    return order


def open_category_sort(screen):
    """Called through the same category-edit PIN gate as move/hide."""
    if not _supported(screen) or getattr(screen, '_closed', True):
        return
    current = screen.category_store.load(screen.favorite_scope).sort_mode
    options = []
    for mode in ('alpha', 'alpha_desc', 'provider'):
        label = VOD_LABELS[mode]
        options.append((('✓ ' if mode == current else '  ') + _(label), mode))
    _choice(screen, SORT_CATEGORIES, options, lambda mode: prepare_category_choice(screen, mode))


def prepare_category_choice(screen, mode):
    from enigma import eTimer
    from .browser import _WorkerToken, _connect_timer
    from .category_preferences import arrange_categories, CATEGORY_SORT_MODES
    if mode not in ('provider', 'alpha', 'alpha_desc', 'manual') or not _supported(screen):
        return
    cancel(screen)
    if not getattr(screen, '_sorting_close_hook', False):
        screen._sorting_close_hook = True
        screen.onClose.append(lambda: cancel(screen))
        if hasattr(screen, 'onHide'):
            screen.onHide.append(lambda: cancel(screen) if getattr(screen, '_sorting_job', None) is not None else None)
    raw, generation = screen._all_categories, screen._load_generation
    store, key = screen.category_store, screen.favorite_scope
    token, timer = _WorkerToken(20), eTimer()
    ctx = context(screen)
    job = dict(token=token, timer=timer, identity=ctx[:2] if ctx else None,
               lock=threading.Lock(), cancelled=threading.Event(), done=False)
    screen._sorting_job = job
    screen['message'].setText(_('Loading...'))
    def work():
        try:
            preferences = store.load(key)
            job['entries'] = arrange_categories(raw, preferences, sort_mode=mode, check=token.check)
            token.check()
            if not store.save_sort(key, mode):
                raise SortUnavailable(SAVE_ERROR)
        except Exception as error:
            job['error'] = str(error) if isinstance(error, SortUnavailable) else SAVE_ERROR
        finally:
            with job['lock']:
                job['done'] = True
                if job['cancelled'].is_set():
                    _dispose(job)
    def poll():
        if screen._sorting_job is not job:
            timer.stop()
            return
        if (getattr(screen, '_closed', True)
                or screen._load_generation != generation or screen._all_categories is not raw):
            cancel(screen)
            return
        if not job['done']:
            if token.expired():
                cancel(screen)
                _message(screen, SAVE_ERROR)
                return
            timer.start(100, True)
            return
        screen._sorting_job = None
        timer.stop()
        if job.get('error'):
            _dispose(job)
            _message(screen, job['error'])
            return
        selected = str(getattr(screen._entries[screen.selected_index], 'category_id', '') or '') if screen._entries else ''
        screen._entries = job.pop('entries')
        screen.selected_index = next((i for i, row in enumerate(screen._entries)
                                      if str(getattr(row, 'category_id', '') or '') == selected), 0)
        if screen.content_type == 'movie':
            screen._refresh_movie_categories(schedule_load=False)
        elif screen.content_type == 'series':
            screen._refresh_series_categories()
        else:
            screen._refresh_live_categories()
    _connect_timer(timer, poll)
    job['poll'] = poll
    threading.Thread(target=work, name='GT-CategorySorting', daemon=True).start()
    timer.start(100, True)
