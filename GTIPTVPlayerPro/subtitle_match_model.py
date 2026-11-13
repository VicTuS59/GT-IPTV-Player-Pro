# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Local subtitle ranker fitted by scripts/train-subtitle-match.py."""

_INTERCEPT = -2.368406
_WEIGHTS = (0.422345, 1.255839, 1.539529, -0.778845, 0.764439, 1.390579,
            1.471879, 1.376982, 0.240318, 0.0, 0.0, 0.374107)
_STATE_VALUES = {"match": 1, "mismatch": -1}
_TITLE_YEAR_INTERCEPT = -0.321651
_TITLE_YEAR_WEIGHTS = (1.735152, 0.585517, 0.09183, 1.504125)


def score_title_year(*, known_year, recent_year, release_suffix, preceding_number):
    """Rank the two interpretations of a trailing four-digit title token."""
    features = (known_year, recent_year, release_suffix, preceding_number)
    return _TITLE_YEAR_INTERCEPT + sum(
        weight * value for weight, value in zip(_TITLE_YEAR_WEIGHTS, features)
    )


def score_match(*, identity, exact, group, title_conflict, source, platform,
                edition, revision, fps, resolution, codec, duration):
    """Return an integer ranking score. Absent or unverified hints are neutral."""
    states = (source, platform, edition, revision, fps, resolution, codec, duration)
    features = (int(identity), int(exact), int(group), int(title_conflict)) + tuple(
        _STATE_VALUES.get(state, 0) for state in states
    )
    score = _INTERCEPT + sum(weight * value for weight, value in zip(_WEIGHTS, features))
    return round(score * 1000)
