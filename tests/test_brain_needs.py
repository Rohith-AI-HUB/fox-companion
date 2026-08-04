"""Tests for :class:`core.brain.Brain` behaviour-needs model.

Exercises the need clamping, coding-focus multiplier, AFK nap replenishment
and the ``decide()`` priority chain (nap → night → exhausted → bored walk →
hungry sit → bored jump → slow wander → idle).
"""
import random

import pytest

from core import config
from core.brain import Brain


def _brain(**overrides):
    b = Brain()
    for attr, value in overrides.items():
        setattr(b, attr, value)
    return b


@pytest.fixture
def brain():
    return Brain()


def test_initial_needs_within_range(brain):
    assert 0.0 <= brain.energy <= 100.0
    assert 0.0 <= brain.boredom <= 100.0
    assert 0.0 <= brain.hunger <= 100.0


def test_needs_clamp_to_bounds(monkeypatch):
    # Amplify the idle rates so every need overshoots and must clamp to 100.
    monkeypatch.setattr(config, "ENERGY_IDLE_RATE", 1000.0)
    monkeypatch.setattr(config, "BOREDOM_IDLE_RATE", 1000.0)
    monkeypatch.setattr(config, "HUNGER_PASSIVE_RATE", 1000.0)
    b = _brain(energy=99.0, boredom=99.0, hunger=99.0)
    b.update(1.0, "idle")
    assert b.energy == 100.0
    assert b.boredom == 100.0
    assert b.hunger == 100.0


@pytest.mark.parametrize("state,expected_energy", [
    ("idle", 3.0),
    ("sit", 6.0),
    ("walk", -5.0),
    ("jump", -10.0),
])
def test_energy_rates_by_state(state, expected_energy):
    b = _brain(energy=50.0, boredom=50.0, hunger=50.0, user_idle_seconds=0.0)
    b.update(1.0, state)
    assert b.energy == pytest.approx(50.0 + expected_energy)


def test_coding_focus_slows_boredom(monkeypatch):
    monkeypatch.setattr(config, "CODING_FOCUS_MULTIPLIER", 0.5)
    b = _brain(boredom=50.0, activity_category="coding", user_idle_seconds=10.0)
    b.update(1.0, "idle")
    expected = 50.0 + config.BOREDOM_IDLE_RATE * config.CODING_FOCUS_MULTIPLIER
    assert b.boredom == pytest.approx(expected)


def test_afk_restores_energy(monkeypatch):
    monkeypatch.setattr(config, "IDLE_AFK_SECONDS", 120.0)
    b = _brain(energy=40.0, boredom=40.0, hunger=40.0, user_idle_seconds=130.0)
    before = b.energy
    b.update(1.0, "idle")
    assert b.energy > before  # AFK napping replenishes energy


def _decide(b, random_val, is_night=False, monkeypatch=None):
    """Run decide() with deterministic random + fixed clock."""
    monkeypatch.setattr(random, "random", lambda: random_val)
    monkeypatch.setattr(b, "is_late", lambda: is_night)
    return b.decide()


# ── decide() priority chain (deterministic via monkeypatched random) ──

def test_decide_nap_when_idle_too_long(monkeypatch):
    b = _brain(user_idle_seconds=config.IDLE_NAP_SECONDS + 1)
    assert _decide(b, 0.0, monkeypatch=monkeypatch) == "sit"


def test_decide_sleepy_at_night(monkeypatch):
    b = _brain(user_idle_seconds=0.0, energy=50.0)
    assert _decide(b, 0.5, is_night=True, monkeypatch=monkeypatch) == "sit"


def test_decide_sit_when_exhausted(monkeypatch):
    b = _brain(energy=10.0, user_idle_seconds=0.0)  # below sleep threshold
    assert _decide(b, 0.0, monkeypatch=monkeypatch) == "sit"


def test_decide_walk_when_very_bored(monkeypatch):
    b = _brain(boredom=config.BOREDOM_WALK_THRESHOLD + 10, user_idle_seconds=0.0)
    decision = _decide(b, 0.0, monkeypatch=monkeypatch)
    assert decision == "walk"
    assert b.walk_speed > 0.5  # boredom-scaled walk speed boost


def test_decide_sit_when_hungry(monkeypatch):
    b = _brain(hunger=config.HUNGER_SIT_THRESHOLD + 5,
               boredom=0.0, energy=90.0, user_idle_seconds=0.0)
    # random 0.2 < HUNGER_SIT_PROBABILITY -> hungry sit triggers.
    assert _decide(b, 0.2, monkeypatch=monkeypatch) == "sit"


def test_decide_jump_when_bored_and_energetic(monkeypatch):
    b = _brain(boredom=config.BOREDOM_JUMP_THRESHOLD + 5,
               energy=config.ENERGY_JUMP_THRESHOLD + 5,
               hunger=0.0, user_idle_seconds=0.0)
    assert _decide(b, 0.1, monkeypatch=monkeypatch) == "jump"


def test_decide_slow_wander(monkeypatch):
    b = _brain(energy=90.0,
               boredom=config.BOREDOM_WALK_TRIGGER + 5,
               hunger=0.0, user_idle_seconds=0.0)
    assert _decide(b, 0.1, monkeypatch=monkeypatch) == "walk"
    assert b.walk_speed < 0.5  # slower wandering speed


def test_decide_idle_when_content(monkeypatch):
    b = _brain(energy=90.0, boredom=0.0, hunger=0.0, user_idle_seconds=0.0)
    assert _decide(b, 0.9, monkeypatch=monkeypatch) == "idle"