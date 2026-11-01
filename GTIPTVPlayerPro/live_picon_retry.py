"""A small per-channel retry budget; no network, timer or receiver dependency."""
DOWNLOAD_TIMEOUT = 5
DOWNLOAD_BUDGET = 2 * DOWNLOAD_TIMEOUT + 1
MAX_ATTEMPTS = 3
WINDOW_SECONDS = 60
DECODE_TIMEOUT = 5


class LivePiconRetry:
    def __init__(self, key, now):
        self.key = key
        self.deadline = float(now) + WINDOW_SECONDS
        self.attempts = 0
        self.decode_attempts = 0
        self.phase = ''
        self.retry_at = 0.0
        self.decode_deadline = 0.0

    def ready(self, now):
        return (not self.phase and self.attempts < MAX_ATTEMPTS and
                now < self.deadline and now >= self.retry_at)

    def can_wait(self, now):
        return (not self.phase and self.attempts < MAX_ATTEMPTS and
                max(now, self.retry_at) < self.deadline)

    def begin(self, phase, now):
        # Download and decode belong to one attempt, not two retries.
        if not (phase == 'decode' and self.phase == 'download'):
            if not self.ready(now):return False
            self.attempts += 1
        self.phase = phase
        self.retry_at = 0.0
        if phase == 'decode':
            self.decode_attempts += 1
            self.decode_deadline = min(self.deadline, now + DECODE_TIMEOUT)
        return True

    def defer(self, now, cooldown=0.0):
        self.phase = ''
        self.decode_deadline = 0.0
        due = now + max(1.0, float(cooldown))
        if self.attempts >= MAX_ATTEMPTS or due >= self.deadline:
            # Exhausted work must remain stopped even if the infobar reopens.
            self.retry_at = self.deadline
            return None
        self.retry_at = due
        return max(1, int(round((due - now) * 1000)))

    def succeeded(self):
        self.phase = 'ready'
        self.retry_at = self.decode_deadline = 0.0
