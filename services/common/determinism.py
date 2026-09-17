"""Deterministic randomness for the simulation engine.

The entire product claim for the counterfactual sandbox is that
(snapshot_id, scenario_spec_hash, model_versions, seed) reproduces byte-identical
output. That's only true if there is exactly one source of randomness in the
simulation path, threaded explicitly rather than pulled from module-level
`random` or `numpy.random` global state. Every DES component takes a `Rng` and
uses only that.
"""

from __future__ import annotations

import hashlib
import random


class Rng:
    """Thin wrapper around random.Random so simulation code never touches
    the `random` module directly. Import-time grep for `import random` outside
    this file (excluding tests) is a code-review red flag.
    """

    def __init__(self, seed: int):
        self._seed = seed
        self._rand = random.Random(seed)

    def uniform(self, a: float, b: float) -> float:
        return self._rand.uniform(a, b)

    def gauss(self, mu: float, sigma: float) -> float:
        return self._rand.gauss(mu, sigma)

    def choice(self, seq):
        return self._rand.choice(seq)

    def sample(self, population, k: int):
        return self._rand.sample(population, k)

    def shuffle(self, seq: list) -> None:
        self._rand.shuffle(seq)

    def spawn(self, label: str) -> Rng:
        """Deterministically derive a child RNG for a sub-component (e.g. one
        per runway) so parallel-looking logic still has fully reproducible,
        independent streams - derived from the parent seed and a stable label,
        never from wall-clock or object identity.
        """
        h = hashlib.sha256(f"{self._seed}:{label}".encode()).hexdigest()
        return Rng(int(h[:16], 16))


def spec_hash(spec_json: str) -> str:
    """Stable hash of a canonicalized ScenarioSpec JSON string, used as the
    reproducibility key alongside snapshot_id, model_versions, and seed.
    """
    return hashlib.sha256(spec_json.encode("utf-8")).hexdigest()[:16]
