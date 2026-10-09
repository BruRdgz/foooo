"""Compact directed routing with explicit parallel-edge selection."""

from dataclasses import dataclass
from math import isfinite

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from ..exceptions import RouteNotFoundError
from .state import RoadRuntimeState


@dataclass(frozen=True, slots=True)
class Route:
    node_ids: tuple[int, ...]
    edge_ids: tuple[int, ...]
    travel_time_s: float
    distance_m: float


class Router:
    def __init__(self, node_ids, edges):
        self.node_ids = tuple(int(node) for node in node_ids)
        self.node_index = {node: index for index, node in enumerate(self.node_ids)}
        self.edges = tuple(edges)
        self.edge_ids = frozenset(edge.id for edge in self.edges)
        self._base_graph, self._base_choices = self._graph(None)

    def _graph(self, state):
        if state is not None:
            unknown = (
                state.closed_edges | state.cost_multiplier.keys()
            ) - self.edge_ids
            if unknown:
                raise ValueError(f"Unknown road edge IDs: {sorted(unknown)}")
        choices = {}
        for edge in self.edges:
            if state is not None and edge.id in state.closed_edges:
                continue
            cost = edge.base_travel_time_s
            if state is not None:
                cost *= state.cost_multiplier.get(edge.id, 1.0)
            if not isfinite(cost) or cost <= 0:
                raise ValueError(
                    f"Invalid effective travel cost for road edge {edge.id}"
                )
            key = (self.node_index[edge.u], self.node_index[edge.v])
            previous = choices.get(key)
            if previous is None or (cost, edge.id) < (previous[0], previous[1].id):
                choices[key] = (cost, edge)
        pairs = sorted(choices)
        graph = csr_matrix(
            (
                [choices[key][0] for key in pairs],
                ([key[0] for key in pairs], [key[1] for key in pairs]),
            ),
            shape=(len(self.node_ids), len(self.node_ids)),
        )
        return graph, choices

    def route(
        self, origin: int, destination: int, *, state: RoadRuntimeState | None = None
    ):
        if origin not in self.node_index or destination not in self.node_index:
            raise RouteNotFoundError(f"Unknown road node: {origin} or {destination}")
        if origin == destination:
            # Validate overlays even when no movement is needed.
            if state is not None:
                self._graph(state)
            return Route((origin,), (), 0.0, 0.0)
        graph, choices = (
            (self._base_graph, self._base_choices)
            if state is None
            else self._graph(state)
        )
        start, end = self.node_index[origin], self.node_index[destination]
        costs, predecessors = dijkstra(
            graph, directed=True, indices=start, return_predecessors=True
        )
        if not np.isfinite(costs[end]):
            raise RouteNotFoundError(
                f"No directed road route from {origin} to {destination}"
            )
        indices = [end]
        while indices[-1] != start:
            indices.append(int(predecessors[indices[-1]]))
        indices.reverse()
        selected = [choices[(u, v)][1] for u, v in zip(indices, indices[1:])]
        return Route(
            tuple(self.node_ids[index] for index in indices),
            tuple(edge.id for edge in selected),
            float(costs[end]),
            float(sum(edge.length_m for edge in selected)),
        )

    def travel_time(self, origin, destination, *, state=None):
        return self.route(origin, destination, state=state).travel_time_s

    def distance(self, origin, destination, *, state=None):
        return self.route(origin, destination, state=state).distance_m
