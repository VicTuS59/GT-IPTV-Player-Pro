# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Direct provider lists for native VOD browsing; optional indexes are lazy."""
import sys
from collections import OrderedDict
from .plain_catalog import PlainRows, PortalRow, raw_entry
from .content import ContentError, _validate_provider_json_depth


class NativeRows(PlainRows):
    native = True

    def __init__(self, resize, owner):
        super().__init__(resize, owner)
        self.native_labels = []
        self._identity_index = None
        self._identity_weight = 0
        self._replay_position = self._replay_length = 0
        self._identity_lookups=OrderedDict()

    def find_identity(self,identity):
        """Saved selections scan only rows added since their previous lookup."""
        key=self.identity(str(identity))
        start,found=self._identity_lookups.get(key,(0,None))
        if found is None:
            for i in range(start,len(self.native_labels)):
                if self.identity(self.native_labels[i][0])==key:
                    found=i;break
        self._identity_lookups[key]=(len(self.native_labels),found)
        self._identity_lookups.move_to_end(key)
        while len(self._identity_lookups)>16:self._identity_lookups.popitem(last=False)
        return found

    def begin_resume(self):
        # Replay a cancelled portal iterator against the retained prefix.
        # Matching by position preserves legitimate duplicate source rows.
        self._replay_position,self._replay_length=0,len(self.values)

    @property
    def resume_pending(self):
        return self._replay_position<self._replay_length

    @property
    def by_id(self):
        if self._identity_index is None:
            result = {}
            for i, value in enumerate(self.values):
                result.setdefault(self.identity(self.raw_identity(value)), i)
            cost=sys.getsizeof(result)+len(result)*96
            self.resize(cost)
            self._identity_weight=cost
            self._identity_index = result
        return self._identity_index

    @by_id.setter
    def by_id(self, value):
        self._identity_index = None

    def extend(self, values, check, valid_id, max_items, commit_lock=None, byte_size=None):
        from .content import _clean_text
        from .vod_catalog import _compact_text
        from .i18n import _
        accepted, labels = [], []
        estimated = 0
        for i, value in enumerate(values):
            if i % 256 == 0: check()
            if not isinstance(value, (dict, PortalRow)): continue
            identity = self.raw_identity(value)
            if not valid_id(identity): continue
            if self.resume_pending:
                if identity!=self.native_labels[self._replay_position][0]:
                    raise ContentError(reason='incomplete')
                self._replay_position+=1
                continue
            entry = raw_entry(value)
            # Ordinary flat API rows need no recursive normalization, copies,
            # compression or per-row memory reservations.
            if any(isinstance(v, (dict, list)) for v in entry.values()):
                try: _validate_provider_json_depth(entry)
                except ValueError:
                    raise ContentError('Server returned an invalid content response', ContentError.INVALID_RESPONSE)
            accepted.append(value)
            title=(entry.get('name') or entry.get('title') or entry.get('o_name')
                   if isinstance(value,PortalRow) else entry.get('name'))
            labels.append((identity,_compact_text(_clean_text(title) or _('Untitled content'),180)))
            if byte_size is None:
                estimated += sum(len(v) for v in entry.values() if isinstance(v, str))
        if len(self.values)+len(accepted)>max_items:
            from .memory_catalog import limit
            raise limit('item-count')
        check()
        if not accepted:return
        from contextlib import nullcontext
        with commit_lock if commit_lock is not None else nullcontext():
            check()
            # One conservative reservation per decoded reply/page batch.
            self.resize((estimated if byte_size is None else byte_size)*2+len(accepted)*512)
            self.values.extend(accepted)
            if self._identity_weight:self.resize(-self._identity_weight)
            self._identity_index=None;self._identity_weight=0
            self._discard_columns()
            self.native_labels.extend(labels)

    def clear(self):
        self.values.clear();self.native_labels.clear();self.aliases.clear()
        self._identity_index=None
        self._identity_weight=0
        self._replay_position=self._replay_length=0
        self._identity_lookups.clear()
        self._groups=self._codes=self._names=self._ends=None
        self._added=self._released=None
        self._has_added=self._has_released=None
        self._columns_weight=0
