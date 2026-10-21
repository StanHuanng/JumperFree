"""Pure software routing model for JumperFree stage 1."""

from .model import (
    RouteError,
    create_net,
    delete_net,
    init_state,
    list_nets,
    query_state,
)
from .steiner import (
    crossbar_create_net,
    crossbar_delete_net,
    crossbar_init_state,
    crossbar_query,
)
from .rearrange import crossbar_create_net_with_rearrange
from .eval import (
    check_constraints,
    exhaustive_reference,
    run_evaluation,
    true_exhaustive_feasible,
)
from .scenarios import (
    Scenario,
    load_scenario,
    scenario_to_route_requests,
    scenario_to_topology,
)
from .v5_scale import v5_scale_topology
from .scale_eval import assert_scale_state, make_scale_workload, run_scale_evaluation

__all__ = [
    "RouteError",
    "create_net",
    "delete_net",
    "init_state",
    "list_nets",
    "query_state",
    "crossbar_create_net",
    "crossbar_delete_net",
    "crossbar_init_state",
    "crossbar_query",
    "crossbar_create_net_with_rearrange",
    "check_constraints",
    "exhaustive_reference",
    "true_exhaustive_feasible",
    "run_evaluation",
    "Scenario",
    "load_scenario",
    "scenario_to_route_requests",
    "scenario_to_topology",
    "v5_scale_topology",
    "assert_scale_state",
    "make_scale_workload",
    "run_scale_evaluation",
]
