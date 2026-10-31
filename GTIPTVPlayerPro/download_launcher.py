# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Short-lived, widget-free parent for the boot-scoped download worker."""
import os
import subprocess
import sys


def main():
    worker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download_worker.py")
    try:
        process = subprocess.Popen(
            [sys.executable, worker, "run"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True,
            start_new_session=True, cwd="/", umask=0o077,
        )
    except (OSError, subprocess.SubprocessError):
        return 1
    # Popen has confirmed exec success. Keep its object alive until _exit:
    # the worker belongs to init after this parent leaves, not to Enigma2.
    # Do not wait for a boot-long worker or run Popen's live-child finalizer.
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
