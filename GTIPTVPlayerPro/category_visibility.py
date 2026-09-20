# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Carry category filter scope through existing list and search boundaries."""

from .category_filters import category_filter_ids
from .category_preferences import (
    content_item_category_ids,
    hidden_category_match_ids as _hidden_category_match_ids,
)


class CategoryVisibility(frozenset):
    """A hidden-ID set with the positive scope of a user-enabled filter.

    The set interface preserves legacy provider arguments.  The extra scope
    remains attached when a child screen normalizes its hidden IDs, including
    when there are no excluded IDs but an inclusion filter is active.
    """

    def __new__(cls, hidden, allowed, known, rule_key):
        value = frozenset.__new__(cls, hidden)
        value.allowed = frozenset(allowed)
        value.known = frozenset(known)
        value.rule_key = rule_key
        return value

    def __bool__(self):
        return True


def category_visibility_ids(categories, preferences):
    """Compute one immutable visibility scope without downloading any items."""

    categories = list(categories or ())
    include = tuple(getattr(preferences, "include_terms", ()))
    exclude = tuple(getattr(preferences, "exclude_terms", ()))
    manual_hidden = getattr(preferences, "hidden", ())
    if not include and not exclude:
        return _hidden_category_match_ids(categories, manual_hidden)
    allowed, excluded = category_filter_ids(categories, include, exclude)
    all_ids = set()
    for category in categories:
        if isinstance(category, dict):
            category_id = category.get("category_id", "")
        else:
            category_id = getattr(category, "category_id", "")
        if category_id is not None and str(category_id).strip():
            all_ids.add(str(category_id).strip())
    hidden = _hidden_category_match_ids(
        categories, set(manual_hidden).union(excluded)
    )
    allowed = _hidden_category_match_ids(
        categories, set(allowed).difference(manual_hidden)
    )
    known = _hidden_category_match_ids(categories, all_ids)
    return CategoryVisibility(hidden, allowed, known, (include, exclude))


def hidden_category_match_ids(categories, hidden_ids):
    if isinstance(hidden_ids, CategoryVisibility):
        return hidden_ids
    return _hidden_category_match_ids(categories, hidden_ids)


def category_ids_visible(category_ids, hidden_ids, category_id=""):
    """Allow known selected membership; explicit exclusions always win.

    All/search rows without usable membership cannot establish a match and
    stay out while filters are enabled.  A category-scoped response can use
    its requested category as evidence only if the row has no known category
    of its own.  No network lookup is made to fill missing provider metadata.
    """

    ids = frozenset(str(value).strip() for value in category_ids if value)
    if ids.intersection(hidden_ids or ()):
        return False
    if not isinstance(hidden_ids, CategoryVisibility):
        return True
    if ids.intersection(hidden_ids.allowed):
        return True
    requested = str(category_id or "").strip()
    return bool(
        requested
        and requested in hidden_ids.allowed
        and requested not in hidden_ids
        and not ids.intersection(hidden_ids.known)
    )


def filter_hidden_category_items(items, hidden_ids, category_id=""):
    values = list(items or ())
    hidden = hidden_category_match_ids((), hidden_ids)
    if not hidden:
        return values
    return [
        item for item in values
        if category_ids_visible(content_item_category_ids(item), hidden, category_id)
    ]


def category_filter_active(hidden_ids):
    return isinstance(hidden_ids, CategoryVisibility)
