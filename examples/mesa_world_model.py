"""Query geography from one Mesa agent at a supplied facility.

Run with a locally built world and its own facility IDs, for example:
``uv run --extra simulation python examples/mesa_world_model.py WORLD
--facility ORIGIN --destination DESTINATION``.
"""

import argparse

from mesa import Agent

from simgeope import PernambucoWorld
from simgeope.mesa_adapter import WorldModel
from simgeope.world.exceptions import (
    NoRoadNearbyError,
    PointOutsideWorldError,
    RouteNotFoundError,
    WorldDataMissingError,
)


class LocationAgent(Agent):
    """Read a supplied location; optionally query a route without moving."""

    def __init__(self, model, location, *, destination=None):
        super().__init__(model)
        self.location = location
        self.destination = destination
        self.municipality = None
        self.environment = None
        self.environment_error = None
        self.route = None
        self.route_error = None

    def step(self):
        self.municipality = self.model.world.municipality_at(self.location)
        self.environment = None
        self.environment_error = None
        try:
            self.environment = self.model.environment_at(self.location)
        except (WorldDataMissingError, PointOutsideWorldError) as exc:
            self.environment_error = str(exc)
        self.route = None
        self.route_error = None
        if self.destination is not None:
            try:
                self.route = self.model.route(self.location, self.destination)
            except (
                WorldDataMissingError,
                NoRoadNearbyError,
                RouteNotFoundError,
                KeyError,
            ) as exc:
                self.route_error = str(exc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("world", help="Path to an already-built world")
    parser.add_argument("--facility", required=True, help="Existing facility ID")
    parser.add_argument("--destination", help="Optional destination facility ID")
    parser.add_argument("--seed", type=int, default=42)
    arguments = parser.parse_args()
    world = PernambucoWorld.load(arguments.world)
    model = WorldModel(world, seed=arguments.seed)
    agent = LocationAgent(model, arguments.facility, destination=arguments.destination)
    model.step()
    model.agents.do("step")
    print("Mesa steps:", model.steps)
    print("Municipality:", agent.municipality)
    print("Environment:", agent.environment or agent.environment_error)
    if agent.destination is not None:
        print("Route:", agent.route or agent.route_error)


if __name__ == "__main__":
    main()
