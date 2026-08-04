"""Tests for :class:`core.physics.PhysicsState`.

Covers the walk/decel, edge-clamp, gravity/jump/landing and idle-friction
semantics that feed the desktop pet's movement.
"""
import pytest

from core import config
from core.physics import PhysicsState


@pytest.fixture
def physics():
    """A fox grounded in the middle of a 1000px-wide floor."""
    return PhysicsState(x=500.0, y=500.0, anchor_y=500.0, left=0.0, right=1000.0)


def test_initial_state_is_grounded(physics):
    assert physics.x == 500.0
    assert physics.y == 500.0
    assert physics.vx == 0.0
    assert physics.vy == 0.0
    assert physics.on_ground is True
    assert physics.walking is False


def test_walk_to_decelerates_and_stops(physics):
    # Target a short hop away so the walk enters the deceleration radius.
    physics.walk_to(520.0)
    for _ in range(300):
        physics.step(1 / 30)

    # Stops within POSITION_THRESHOLD (2.0 px) of the target by design.
    assert physics.x == pytest.approx(520.0, abs=config.POSITION_THRESHOLD + 0.5)
    assert physics.walking is False
    assert physics.vx == 0.0


def test_walk_accelerates_toward_far_target(physics):
    physics.walk_to(900.0)
    for _ in range(5):
        physics.step(1 / 30)

    # Early frames: moving right at up to max speed, never exceeding it.
    assert physics.x > 500.0
    assert 0.0 <= physics.vx <= config.WALK_MAX_SPEED


def test_edge_left_reverses_direction(physics):
    physics.walk_to(-100.0)  # beyond the left bound
    hit = None
    for _ in range(400):
        physics.step(1 / 30)
        if physics.edge_hit:
            hit = (physics.x, physics.target_x)
            break

    # Clamped at the boundary on first contact, target flipped to the right.
    assert hit is not None
    assert hit[0] == pytest.approx(physics.left)
    assert hit[1] == physics.right


def test_edge_right_reverses_direction(physics):
    physics.walk_to(1200.0)  # beyond the right bound
    hit = None
    for _ in range(400):
        physics.step(1 / 30)
        if physics.edge_hit:
            hit = (physics.x, physics.target_x)
            break

    assert hit is not None
    assert hit[0] == pytest.approx(physics.right)
    assert hit[1] == physics.left


def test_jump_gravity_and_landing(physics):
    physics.jump(vy=config.BRAIN_JUMP_VY)  # -400
    assert physics.on_ground is False

    # Rise then fall; record the lowest wall-climb and the landing event.
    peak_y = physics.y
    landed = False
    for _ in range(600):
        physics.step(1 / 60)
        peak_y = min(peak_y, physics.y)
        if physics.just_landed:
            landed = True

    # Cleared the ground arc and touched back down exactly at the anchor.
    assert peak_y < 500.0
    assert landed is True
    assert physics.on_ground is True
    assert physics.y == pytest.approx(physics.anchor_y)


def test_idle_friction_decays_velocity(physics):
    physics.apply_impulse(150.0)
    assert physics.vx == 150.0

    # Idle (not walking): friction bleeds off horizontal velocity.
    for _ in range(300):
        physics.step(1 / 60)
        if physics.vx == 0.0:
            break

    assert physics.vx == 0.0
    assert abs(physics.x - 500.0) < 40.0


def test_set_bounds_updates_limits():
    p = PhysicsState(x=0.0, y=0.0, anchor_y=0.0, left=0.0, right=10.0)
    p.set_bounds(20.0, 80.0)
    assert p.left == 20.0
    assert p.right == 80.0


def test_step_caps_dt():
    p = PhysicsState(x=0.0, y=0.0, anchor_y=0.0, left=-100.0, right=100.0)
    p.walk_to(90.0)
    p.step(0.5)  # far above the 0.05 cap
    # Movement happened but the timestep was clamped to 0.05.
    assert p.x > 0.0
    assert p.x <= 90.0