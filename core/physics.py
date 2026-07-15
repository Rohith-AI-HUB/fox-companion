from core import config

class PhysicsState:
    def __init__(self, x, y, anchor_y, left, right):
        self.x = float(x)
        self.y = float(y)
        self.vx = 0.0
        self.vy = 0.0
        self.anchor_y = float(anchor_y)
        self.left = float(left)
        self.right = float(right)
        self.on_ground = True
        self.target_x = self.x
        self.walking = False
        self.walk_target_speed = config.WALK_MAX_SPEED
        self.landing_bounce = 0.0
        self.squash = 1.0
        self.stretch = 1.0

    def set_bounds(self, left, right):
        self.left = float(left)
        self.right = float(right)

    def walk_to(self, target_x, max_speed=None):
        self.target_x = float(target_x)
        self.walk_target_speed = max_speed if max_speed is not None else config.WALK_MAX_SPEED
        self.walking = True

    def stop_walk(self):
        self.target_x = self.x
        self.walking = False

    def apply_impulse(self, vx):
        self.vx = float(vx)
        self.target_x = self.x

    def jump(self, vy, vx=None):
        self.vy = float(vy)
        if vx is not None:
            self.vx = float(vx)
        self.on_ground = False
        self.target_x = self.x

    def release_from_drag(self, x, y, vx, vy):
        self.x = float(x)
        self.y = float(y)
        self.vx = float(vx)
        self.vy = float(vy)
        self.target_x = self.x
        self.on_ground = y >= self.anchor_y

    def step(self, dt):
        dt = min(dt, 0.05)

        if self.walking:
            dist = self.target_x - self.x
            if abs(dist) > config.POSITION_THRESHOLD:
                direction = 1.0 if dist > 0 else -1.0
                if abs(dist) < config.WALK_DECEL_RADIUS:
                    target_speed = self.walk_target_speed * (abs(dist) / config.WALK_DECEL_RADIUS)
                    target_speed = max(target_speed, 15.0)
                    self.vx = direction * target_speed
                else:
                    self.vx += direction * config.WALK_ACCEL * dt
                    self.vx = max(-self.walk_target_speed, min(self.walk_target_speed, self.vx))
            else:
                self.walking = False
                self.vx = 0.0
        else:
            self.vx -= config.FRICTION * self.vx * dt
            if abs(self.vx) < 2.0:
                self.vx = 0.0

        self.x += self.vx * dt

        if self.x < self.left:
            self.x = self.left
            self.vx = 0.0
            if self.walking:
                self.target_x = self.right
        elif self.x > self.right:
            self.x = self.right
            self.vx = 0.0
            if self.walking:
                self.target_x = self.left

        if not self.on_ground:
            self.vy += config.GRAVITY * dt
            self.y += self.vy * dt
            if self.y < self.anchor_y - config.CEILING_OFFSET:
                self.y = self.anchor_y - config.CEILING_OFFSET
                self.vy = 0.0
            if self.y >= self.anchor_y:
                self.y = self.anchor_y
                self.vy = -self.vy * 0.3
                if abs(self.vy) < config.BOUNCE_VELOCITY:
                    self.vy = 0.0
                    self.on_ground = True
                    self.landing_bounce = 1.0
                self.squash = 0.85

        if self.landing_bounce > 0:
            self.squash = 0.85 + 0.15 * (1 - self.landing_bounce)
            self.landing_bounce -= dt * 3.0
            if self.landing_bounce <= 0:
                self.landing_bounce = 0.0
                self.squash = 1.0

        if self.squash < 1.0 and self.landing_bounce <= 0:
            self.squash = min(1.0, self.squash + dt * 4.0)

        return self.vx
