# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Credential-free account fingerprints for UI changes and source identity.

Account checks are deliberately not cached across account-screen visits.
"""

import hashlib
import json


def account_test_fingerprint(account):
    """Hash all connection credentials; never store an address, password or MAC."""
    kind = str(getattr(account, "source_type", "") or "").strip().lower()
    if kind == "xtream":
        if not all(hasattr(account, name) for name in (
            "scheme", "netloc", "base_path", "username", "password"
        )):
            return ""
        fields = [kind, str(account.scheme).lower(), str(account.netloc).lower(),
                  str(account.base_path), str(account.username), str(account.password),
                  str(getattr(account, "output_format", ""))]
    elif kind == "stalker":
        if not getattr(account, "portal_url", "") or not getattr(account, "mac", ""):
            return ""
        fields = [kind, str(account.portal_url), str(account.mac).upper().replace("-", ":"),
                  str(getattr(account, "expiry_annotation", ""))]
    else:
        return ""
    encoded = json.dumps(fields, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
