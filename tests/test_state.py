import pytest

from simgeope.world.roads import RoadRuntimeState


@pytest.mark.parametrize(
    "multiplier", [0, -1, float("nan"), float("inf"), -float("inf")]
)
def test_invalid_road_cost_rejected(multiplier):
    with pytest.raises(ValueError):
        RoadRuntimeState(cost_multiplier={1: multiplier})


def test_overlay_copies_input_and_preserves_immutable_state():
    closed = {1}
    costs = {2: 3.0}
    state = RoadRuntimeState(closed_edges=closed, cost_multiplier=costs)
    closed.add(2)
    costs[2] = 9.0
    assert state.closed_edges == frozenset({1})
    assert dict(state.cost_multiplier) == {2: 3.0}
    with pytest.raises(TypeError):
        state.cost_multiplier[2] = 4.0
