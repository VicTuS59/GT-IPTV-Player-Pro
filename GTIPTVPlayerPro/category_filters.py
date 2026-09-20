# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded, provider-derived category filtering without network or UI work."""

import re
import unicodedata
from itertools import islice


MAX_FILTER_TERM_BYTES = 512
MAX_FILTER_TERMS = 20000
MAX_FILTER_CATEGORIES = 20000
MAX_SCANNED_FILTERS = 40000
_GROUP_SEPARATORS = re.compile(r"[|:\[\](){}<>/\\_\-+;•·]+")
_COLLECTION_TYPES = (list, tuple, set, frozenset)


def _valid_text(value):
    if not isinstance(value, str):
        return False
    try:
        return (
            len(value.encode("utf-8", "strict")) <= MAX_FILTER_TERM_BYTES
            and not any(
                (ord(char) < 32 and char not in "\t\r\n") or ord(char) == 127
                for char in value
            )
        )
    except UnicodeError:
        return False


def _words(text):
    """Keep Unicode letters, numbers and attached combining marks together."""

    word = []
    for character in text:
        kind = unicodedata.category(character)[0]
        if kind in ("L", "N") or (kind == "M" and word):
            word.append(character)
        elif word:
            yield "".join(word)
            word = []
    if word:
        yield "".join(word)


def normalize_filter_term(text):
    """Return a case-insensitive Unicode phrase, or empty for invalid input.

    Punctuation separates words.  There are deliberately no country, language
    or adult-content dictionaries: every rule comes from the provider's names.
    Turkish dotted capital I receives its usual lowercase spelling; other
    accents remain significant.
    """

    if not _valid_text(text):
        return ""
    text = unicodedata.normalize("NFKC", text).casefold().replace("i\u0307", "i")
    normalized = " ".join(_words(unicodedata.normalize("NFC", text)))
    return normalized if _valid_text(normalized) else ""


def clean_filter_terms(values):
    """Sanitize a bounded rules snapshot while preserving first-seen order."""

    if not isinstance(values, _COLLECTION_TYPES):
        return ()
    cleaned = []
    seen = set()
    for value in islice(iter(values), MAX_FILTER_TERMS):
        term = normalize_filter_term(value)
        if term and term not in seen:
            seen.add(term)
            cleaned.append(term)
    return tuple(cleaned)


def valid_filter_terms(values):
    """Reject malformed explicit saves instead of silently weakening a rule."""

    return (
        isinstance(values, _COLLECTION_TYPES)
        and len(values) <= MAX_FILTER_TERMS
        and all(bool(normalize_filter_term(value)) for value in values)
    )


def _category_records(categories):
    if not isinstance(categories, _COLLECTION_TYPES):
        return
    seen = set()
    for category in islice(iter(categories), MAX_FILTER_CATEGORIES):
        if isinstance(category, dict):
            raw_id = category.get("category_id")
            name = (
                category.get("name")
                or category.get("title")
                or category.get("category_name")
                or ""
            )
        else:
            raw_id = getattr(category, "category_id", "")
            name = getattr(category, "name", "")
        if raw_id is None or isinstance(raw_id, (bool, dict) + _COLLECTION_TYPES):
            continue
        category_id = str(raw_id).strip()
        try:
            safe_id = (
                category_id
                and category_id != "*"
                and len(category_id.encode("utf-8", "strict")) <= 128
                and not any(ord(char) < 32 or ord(char) == 127 for char in category_id)
            )
        except UnicodeError:
            safe_id = False
        if not safe_id or category_id in seen:
            continue
        seen.add(category_id)
        # An invalid name is still a real provider category.  Keep its ID so
        # default (no include rules) never removes it merely for its spelling.
        label = " ".join(name.split()) if _valid_text(name) else ""
        yield category_id, label, normalize_filter_term(name)


def category_matches_filter(name, term):
    """Match complete words or a contiguous phrase, never a substring token."""

    normalized_term = normalize_filter_term(term)
    normalized_name = normalize_filter_term(name)
    return bool(
        normalized_term
        and normalized_name
        and (" " + normalized_term + " ") in (" " + normalized_name + " ")
    )


def _matching_ids(records, terms):
    """Use token intersections before phrase matching to bound repeat work."""

    normalized_names = {}
    token_ids = {}
    for category_id, unused_label, normalized in records:
        normalized_names[category_id] = " " + normalized + " "
        for token in set(normalized.split()):
            token_ids.setdefault(token, set()).add(category_id)
    matches = {}
    for term in terms:
        words = term.split()
        candidates = [token_ids.get(word, set()) for word in set(words)]
        if not candidates:
            matches[term] = frozenset()
            continue
        candidates.sort(key=len)
        possible = candidates[0].intersection(*candidates[1:])
        needle = " " + term + " "
        matches[term] = frozenset(
            category_id
            for category_id in possible
            if needle in normalized_names[category_id]
        )
    return matches


def scan_category_filters(categories, include_terms=(), exclude_terms=()):
    """Discover full names, separated groups and words, with category counts.

    Each result contains ``term``, ``label`` and ``count``.  Saved terms that
    temporarily disappeared remain selectable with a zero count.  Categories
    sharing an ID are counted once; the synthetic All category is omitted.
    """

    records = list(_category_records(categories))
    labels = {}

    def add_label(label):
        term = normalize_filter_term(label)
        if term and term not in labels and len(labels) < MAX_SCANNED_FILTERS:
            labels[term] = " ".join(label.split())

    # Full provider names take priority over derived words when bounded.
    for unused_id, label, unused_normalized in records:
        add_label(label)
    for unused_id, label, unused_normalized in records:
        for group in _GROUP_SEPARATORS.split(label):
            add_label(group)
        for word in _words(label):
            add_label(word)
    # Retain all valid saved choices even when the discovery limit is reached.
    for term in clean_filter_terms(include_terms) + clean_filter_terms(exclude_terms):
        labels.setdefault(term, term)
    matches = _matching_ids(records, labels)
    return [
        {"term": term, "label": labels[term], "count": len(matches[term])}
        for term in sorted(labels)
    ]


def category_filter_ids(categories, include_terms=(), exclude_terms=()):
    """Return allowed and excluded provider IDs; exclusion always wins.

    Include rules use OR (any selected term); an empty include selection means
    every provider category.  Excluded IDs are only explicit exclusion matches:
    a content item in TR and DE remains allowed by TR unless DE was explicitly
    excluded.  Callers can apply these decisions to multi-category membership.
    """

    records = list(_category_records(categories))
    include = clean_filter_terms(include_terms)
    exclude = clean_filter_terms(exclude_terms)
    all_ids = frozenset(record[0] for record in records)
    if not include and not exclude:
        return all_ids, frozenset()
    matches = _matching_ids(records, set(include + exclude))
    allowed = set().union(*(matches[term] for term in include)) if include else set(all_ids)
    blocked = set().union(*(matches[term] for term in exclude)) if exclude else set()
    allowed.difference_update(blocked)
    return frozenset(allowed), frozenset(blocked)
