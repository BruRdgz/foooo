"""Create a small synthetic, stationary Mesa population in a built world.

Run with ``uv run --extra simulation python examples/agent_population.py WORLD``.
The coordinates are illustrative initial positions, not sampled data.
"""

import argparse

from simgeope import PernambucoWorld
from simgeope.simulation import AgentModel
from simgeope.world.facilities import Facility


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("world", help="Path to an already-built world with roads")
    arguments = parser.parse_args()

    model = AgentModel(PernambucoWorld.load(arguments.world), seed=42)
    recife = model.add_farm(
        Facility(id="farm-recife", kind="farm", lon=-34.88, lat=-8.05),
        pop_total=100,
    )
    caruaru = model.add_farm(
        Facility(id="farm-caruaru", kind="farm", lon=-35.97, lat=-8.28),
        pop_total=80,
    )
    bird = model.add_bird("bird-1", (-35.0, -8.1))
    truck = model.add_truck("truck-1", recife)
    model.step()

    print(f"Mesa steps: {model.steps}")
    print(f"Agents: {len(model.agents_by_id)} (2 farms, 1 bird, 1 truck)")
    for farm in (recife, caruaru):
        print(
            f"Farm {farm.farm_id}: location={farm.location}, population={farm.pop_total}"
        )
    print(f"Bird {bird.bird_id}: location={bird.location}")
    print(
        f"Truck {truck.truck_id}: at_farm={truck.at_farm.farm_id}, location={truck.location}"
    )


if __name__ == "__main__":
    main()
