"""Seeded V0.1 workload and plan-size evaluation, independent of V5 routing eval."""

from __future__ import annotations

from dataclasses import replace
import random
from typing import Any, Iterator

from ..routing.model import RouteError
from ..routing.rearrange import crossbar_create_net_with_rearrange
from ..routing.steiner import crossbar_delete_net, crossbar_init_state
from .plan import compile_plan, net_tags, state_with_tags
from .profile import BoardProfile


def v01_transitions(
    profile: BoardProfile, *, seed: int = 7, trials: int = 60,
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Replay creates (bounded rearrange enabled) and deletes with persistent tags."""
    if type(seed) is not int or type(trials) is not int or trials < 0:
        raise ValueError("seed must be an integer; trials must be a non-negative integer")
    rng = random.Random(seed)
    state = crossbar_init_state(profile.topology)
    for step in range(trials):
        reserved = {node for net in state["nets"].values() for node in net["nodes"]}
        available = sorted(set(profile.topology["node_attachments"]) - reserved)
        if state["nets"] and (len(available) < 2 or rng.random() < 0.35):
            new = crossbar_delete_net(state, rng.choice(sorted(state["nets"])))
        else:
            members = sorted(rng.sample(available, rng.randint(2, min(4, len(available)))))
            try:
                new, _ = crossbar_create_net_with_rearrange(
                    state, f"net-{step:04d}", members, max_moves=64, max_states=32,
                )
            except RouteError as exc:
                if exc.code != "NO_FREE_PATH":
                    raise
                new = state
        previous = net_tags(state)
        survivors = {name: tag for name, tag in previous.items() if name in new["nets"]}
        tags = net_tags({"nets": new["nets"], "meta": {"tags": survivors}})
        new = state_with_tags(new, tags)
        yield state, new
        state = new


def run_plan_stats(*, seed: int = 7, trials: int = 60, profile: BoardProfile | None = None) -> dict[str, Any]:
    profile = BoardProfile.load() if profile is None else profile
    # Measure above-limit steps as well; splitting belongs to M3 service.
    measurement_profile = replace(profile, limits=replace(profile.limits, max_ops=256))
    sizes = []
    for old, new in v01_transitions(measurement_profile, seed=seed, trials=trials):
        plan = compile_plan(old, new, measurement_profile)
        sizes.append(len(plan.breaks) + len(plan.makes))
    return {
        "board": "v01", "verified": False, "seed": seed, "steps": trials,
        "ops_per_step": {"min": min(sizes, default=0), "mean": sum(sizes) / len(sizes) if sizes else 0.0,
                         "max": max(sizes, default=0)},
        "max_ops": profile.limits.max_ops,
        "steps_exceeding_max_ops": sum(size > profile.limits.max_ops for size in sizes),
        "sizes": sizes,
    }
