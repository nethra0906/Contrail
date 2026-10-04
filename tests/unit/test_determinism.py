"""Unit tests for services/common/determinism.py - the module whose own
docstring calls determinism "the entire product claim" for the
counterfactual sandbox. No automated test protected it before this.
"""

from __future__ import annotations

from services.common.determinism import Rng, spec_hash


def test_same_seed_produces_identical_sequences():
    a = Rng(42)
    b = Rng(42)
    seq_a = [a.uniform(0, 1) for _ in range(20)]
    seq_b = [b.uniform(0, 1) for _ in range(20)]
    assert seq_a == seq_b


def test_different_seeds_produce_different_sequences():
    a = Rng(1)
    b = Rng(2)
    seq_a = [a.uniform(0, 1) for _ in range(20)]
    seq_b = [b.uniform(0, 1) for _ in range(20)]
    assert seq_a != seq_b


def test_gauss_choice_sample_shuffle_are_all_reproducible_per_seed():
    a = Rng(7)
    b = Rng(7)

    assert a.gauss(0, 1) == b.gauss(0, 1)
    population = list(range(50))
    assert a.choice(population) == b.choice(population)
    assert a.sample(population, 5) == b.sample(population, 5)

    list_a, list_b = list(population), list(population)
    a.shuffle(list_a)
    b.shuffle(list_b)
    assert list_a == list_b


def test_spawn_is_deterministic_per_parent_seed_and_label():
    parent1 = Rng(100)
    parent2 = Rng(100)
    child1 = parent1.spawn("runway-A")
    child2 = parent2.spawn("runway-A")
    assert child1.uniform(0, 1) == child2.uniform(0, 1)


def test_spawn_with_different_labels_gives_independent_streams():
    parent = Rng(100)
    child_a = parent.spawn("runway-A")
    child_b = parent.spawn("runway-B")
    assert child_a.uniform(0, 1) != child_b.uniform(0, 1)


def test_spawn_differs_from_parent_and_from_a_raw_same_seed_rng():
    parent = Rng(100)
    child = parent.spawn("runway-A")
    independent = Rng(100)
    # The spawned child must not just be a copy of the parent's own stream.
    assert child.uniform(0, 1) != independent.uniform(0, 1)


def test_spec_hash_is_stable_for_the_same_input():
    spec = '{"scenario": "close_runway", "seed": 1}'
    assert spec_hash(spec) == spec_hash(spec)


def test_spec_hash_differs_for_different_input():
    assert spec_hash('{"a": 1}') != spec_hash('{"a": 2}')


def test_spec_hash_is_a_short_hex_string():
    h = spec_hash('{"a": 1}')
    assert len(h) == 16
    int(h, 16)  # raises ValueError if not valid hex
