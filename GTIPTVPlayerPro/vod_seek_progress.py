"""Bounded VOD seek observations, independent of native timers and services."""
SEEK_SECONDS = 12.0
RESUME_SECONDS = 4.0
ACQUIRE_SECONDS = 12.0
OVERALL_SECONDS = 45.0
POLL_MS = 650
RELEASE_MS = 500
PTS_MASK = (1 << 33) - 1


class SeekProgress:
    def __init__(self, origin, target, now):
        self.origin, self.target = int(origin), int(target)
        self.started = self.last_sample = float(now)
        self.deadline = float(now) + SEEK_SECONDS
        self.arrived_at = None
        self.last_position = int(origin)
        self.last_pts = None
        self.position_steps = self.pts_steps = self.observations = 0
        self.last_movement = None

    def hold(self, now):
        self.deadline += max(0.0, now - self.last_sample)
        self.last_sample = now
        self.last_pts = None
        self.position_steps = self.pts_steps = 0

    def observe(self, position, pts, now):
        # Some unsupported drivers expose a permanent zero clock. It is not
        # evidence that a video with an advancing service clock is frozen.
        if pts == 0:pts = None
        elapsed = max(0.0, now - self.last_sample)
        self.last_sample = now
        if position >= 0 or pts is not None:
            self.observations += 1
        drift = max(0.0, now - self.arrived_at) if self.arrived_at is not None else 0.0
        near = position >= 0 and self.target - 5 <= position <= self.target + 5 + drift
        if self.arrived_at is None and near:
            self.arrived_at = now
            # The seek's position/PTS discontinuity is not playback progress.
            self.last_position, self.last_pts = position, pts
            self.position_steps = self.pts_steps = 0
            self.last_movement = None
            return False
        position_step = position - self.last_position if position >= 0 and self.last_position >= 0 else 0
        pts_step = ((pts - self.last_pts) & PTS_MASK) if pts is not None and self.last_pts is not None else 0
        if 0 < position_step <= max(3, elapsed + 2):
            self.position_steps += 1
            if pts is None:self.last_movement = now
        elif position_step < 0 or position_step > max(3, elapsed + 2):
            self.position_steps = 0
        if 0 < pts_step <= 90000 * max(3, elapsed + 2):
            self.pts_steps += 1
            self.last_movement = now
        elif pts_step:
            self.pts_steps = 0
        if pts is None:self.pts_steps = 0
        self.last_position, self.last_pts = position, pts
        if self.arrived_at is None or now - self.arrived_at < 1.0:
            return False
        # Known video PTS takes precedence over a possibly cached time label.
        return self.pts_steps >= 2 if pts is not None else self.position_steps >= 2

    def moving(self, now):
        return self.last_movement is not None and now - self.last_movement < 3.0

    def stalled(self, now):
        return self.observations >= 3 and now - self.started >= 3.0 and not self.moving(now)
