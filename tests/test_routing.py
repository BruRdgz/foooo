import pytest

from simgeope.world.exceptions import RouteNotFoundError
from simgeope.world.roads import RoadEdge, RoadRuntimeState
from simgeope.world.roads.routing import Router


@pytest.fixture
def router():
    # Two parallel one-way choices, a detour, and a disconnected island.
    edges = [
        RoadEdge(0, 1, 2, 100, 10),
        RoadEdge(1, 1, 2, 120, 12),
        RoadEdge(2, 2, 3, 100, 10),
        RoadEdge(3, 1, 4, 180, 15),
        RoadEdge(4, 4, 3, 180, 15),
        RoadEdge(5, 8, 9, 50, 5),
    ]
    return Router((1, 2, 3, 4, 8, 9), edges)


def test_parallel_edges_keep_identity_under_closure(router):
    baseline = router.route(1, 3)
    assert baseline.edge_ids == (0, 2)
    assert baseline.travel_time_s == 20
    assert baseline.distance_m == 200
    alternative = router.route(1, 3, state=RoadRuntimeState(closed_edges={0}))
    assert alternative.edge_ids == (1, 2)
    assert alternative.travel_time_s == 22
    assert alternative.distance_m == 220
    detour = router.route(1, 3, state=RoadRuntimeState(closed_edges={0, 1}))
    assert detour.edge_ids == (3, 4)
    assert detour.travel_time_s == 30
    assert router.route(1, 3) == baseline


def test_cost_multiplier_selects_parallel_edge_and_changes_cost(router):
    route = router.route(1, 3, state=RoadRuntimeState(cost_multiplier={0: 2, 2: 1.5}))
    assert route.edge_ids == (1, 2)
    assert route.travel_time_s == 27
    assert route.distance_m == 220
    assert router.travel_time(1, 3) == 20
    assert router.distance(1, 3) == 200


@pytest.mark.parametrize("origin,destination", [(3, 1), (1, 8), (9, 8), (99, 1)])
def test_oneway_disconnected_and_unknown_nodes_raise(router, origin, destination):
    with pytest.raises(RouteNotFoundError):
        router.route(origin, destination)


def test_closures_can_remove_all_routes(router):
    with pytest.raises(RouteNotFoundError):
        router.route(1, 3, state=RoadRuntimeState(closed_edges={2, 4}))


def test_same_node_is_zero_cost_but_still_validates_overlay(router):
    route = router.route(1, 1)
    assert route.node_ids == (1,)
    assert route.edge_ids == ()
    assert route.travel_time_s == route.distance_m == 0
    with pytest.raises(ValueError, match="Unknown road edge"):
        router.route(1, 1, state=RoadRuntimeState(closed_edges={999}))


def test_overlay_unknown_edges_and_immutable_snapshot(router):
    closed, multipliers = {0}, {1: 2}
    state = RoadRuntimeState(closed_edges=closed, cost_multiplier=multipliers)
    closed.clear()
    multipliers[1] = 100
    assert state.closed_edges == frozenset({0})
    assert state.cost_multiplier[1] == 2
    with pytest.raises(TypeError):
        state.cost_multiplier[1] = 3
    with pytest.raises(ValueError, match="Unknown road edge"):
        router.route(1, 3, state=RoadRuntimeState(cost_multiplier={999: 2}))


@pytest.mark.parametrize("multiplier", [0, -1, float("nan"), float("inf")])
def test_invalid_overlay_costs(multiplier):
    with pytest.raises(ValueError, match="strictly positive"):
        RoadRuntimeState(cost_multiplier={0: multiplier})


def test_equal_cost_parallel_edges_are_deterministic():
    router = Router((1, 2), [RoadEdge(8, 1, 2, 10, 1), RoadEdge(2, 1, 2, 10, 1)])
    assert router.route(1, 2).edge_ids == (2,)


def test_effective_cost_overflow_is_explicit(router):
    with pytest.raises(ValueError, match="effective travel cost"):
        router.route(1, 3, state=RoadRuntimeState(cost_multiplier={0: 1e308}))
