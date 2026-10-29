"""Live H5N1 simulation served next to the world inspector.

One ``AgentModel`` per server, like the NetLogo interface: reset, step, run and
manual interventions on a selected farm. Requests share the model under a lock.
"""

import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import replace

import numpy as np
from shapely.geometry import LineString, Point, box

from simgeope.simulation import AgentModel, ModelParams, infection_risk
from simgeope.simulation.model import DEMO_POULTRY_POLES, install_synthetic_facilities
from simgeope.simulation.risk import GREEN, RED, YELLOW
from simgeope.world.exceptions import RouteNotFoundError

from .app import MAX_FEATURES, _collection, _route_coordinates

MAX_STEP_DAYS = 60
MAX_EVENTS = 500
MAX_BODY_BYTES = 4096
INTERVENTIONS = {
    "stamping_out": "apply_stamping_out",
    "movement_block": "apply_movement_block",
    "biosecurity_boost": "apply_biosecurity_boost",
}
_LEVEL_NAMES = {GREEN: "green", YELLOW: "yellow", RED: "red"}


class SimulationSession:
    """A resettable scenario on a loaded world, mirroring ``test.py``.

    Worlds without facilities get synthetic farms and hubs once, at creation,
    so every reset reuses the same geography.
    """

    def __init__(
        self,
        world,
        *,
        params: ModelParams | None = None,
        seed: int = 42,
        pop_total: int = 5_000,
        birds: int = 80,
        synthetic_farms: int = 120,
        synthetic_hubs: Mapping[str, int] | None = None,
        poles: Sequence[str] = DEMO_POULTRY_POLES,
    ):
        self.world = world
        self.params = params or ModelParams()
        self.pop_total = pop_total
        self.birds = birds
        self.synthetic = world.facilities is None
        if self.synthetic:
            install_synthetic_facilities(
                world,
                n=synthetic_farms,
                hubs=synthetic_hubs
                if synthetic_hubs is not None
                else {spec.hub_kind: 2 for spec in self.params.fleets},
                poles=poles,
                seed=seed,
            )
        self.lock = threading.Lock()
        self._route_cache: dict[tuple[int, int], list | None] = {}
        self.reset(seed)

    def reset(self, seed: int) -> None:
        # A fresh copy keeps parameter edits from leaking across resets.
        model = AgentModel(self.world, params=replace(self.params), seed=seed)
        model.add_farms_from_world(pop_total=self.pop_total)
        model.add_birds_uniform(self.birds)
        model.add_hubs_from_world()
        model.setup_fleets()
        model.seed_initial_outbreak()
        with self.lock:
            self.seed = seed
            self.model = model
            self._risk = None

    def step(self, days: int) -> None:
        with self.lock:
            for _ in range(days):
                self.model.step()
            self._risk = None

    def risk(self):
        """Today's risk snapshot, or None without an environmental raster."""
        if self.world.environment is None:
            return None
        if self._risk is None or self._risk.day != self.model.day_count:
            self._risk = infection_risk(self.model)
        return self._risk

    def route_coordinates(self, origin_node: int | None, destination_node: int | None):
        """Road path coordinates; None for unsnapped stops or no route."""
        if origin_node is None or destination_node is None:
            return None
        key = (origin_node, destination_node)
        if key not in self._route_cache:
            try:
                route = self.world.roads.route(origin_node, destination_node)
                self._route_cache[key] = _route_coordinates(self.world, route.edge_ids)
            except RouteNotFoundError:
                self._route_cache[key] = None
        return self._route_cache[key]


def add_simulation_routes(app, session: SimulationSession) -> None:
    from fastapi import HTTPException, Query, Request
    from starlette.concurrency import run_in_threadpool

    async def body(request: Request, allowed: set[str]) -> dict:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_BODY_BYTES:
                raise HTTPException(413, "Simulation request exceeds 4 KiB")
        try:
            payload = json.loads(raw) if raw else {}
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise HTTPException(422, "Expected a JSON object") from None
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise HTTPException(422, f"Expected only: {', '.join(sorted(allowed))}")
        return payload

    def integer(value, name, low, high):
        if type(value) is not int or not low <= value <= high:
            raise HTTPException(422, f"{name} must be an integer in [{low}, {high}]")
        return value

    def state():
        model, p = session.model, session.model.params
        return {
            "day": model.day_count,
            "seed": session.seed,
            "synthetic_facilities": session.synthetic,
            "healthy_farms": model.healthy_farms,
            "notified_farms": model.notified_farms,
            "hidden_farms": model.hidden_farms,
            "rt": model.current_rt,
            "cumulative_cost": model.cumulative_cost,
            "total_deaths": model.total_deaths,
            "total_culled": model.total_culled,
            "farms": len(model.farms),
            "hubs": len(model.hubs),
            "trucks": len(model.trucks),
            "birds": len(model.birds),
            "infected_birds": sum(1 for b in model.birds if b.bird_state == "I"),
            "wild_bird_infections": model.wild_bird_infections,
            "wild_bird_deaths": model.wild_bird_deaths,
            "birds_arrived": model.birds_arrived,
            "risk_available": session.world.environment is not None,
            "risk_thresholds": [p.risk_yellow_threshold, p.risk_red_threshold],
            "stamping_out_radius_km": p.stamping_out_radius_km,
            "interventions": sorted(INTERVENTIONS),
        }

    @app.get("/api/sim/state")
    def get_state():
        with session.lock:
            return state()

    @app.post("/api/sim/reset")
    async def reset(request: Request):
        payload = await body(request, {"seed"})
        seed = integer(payload.get("seed", session.seed), "seed", 0, 2**32 - 1)
        await run_in_threadpool(session.reset, seed)
        with session.lock:
            return state()

    @app.post("/api/sim/step")
    async def step(request: Request):
        payload = await body(request, {"days"})
        days = integer(payload.get("days", 1), "days", 1, MAX_STEP_DAYS)
        await run_in_threadpool(session.step, days)
        with session.lock:
            return state()

    @app.post("/api/sim/intervene")
    async def intervene(request: Request):
        payload = await body(request, {"farm_id", "action"})
        action, farm_id = payload.get("action"), payload.get("farm_id")
        if not isinstance(farm_id, str):
            raise HTTPException(422, "farm_id must be a farm ID string")
        if action not in INTERVENTIONS:
            raise HTTPException(
                422, f"action must be one of: {', '.join(sorted(INTERVENTIONS))}"
            )
        with session.lock:
            farm = session.model.farm_by_id(farm_id)
            if farm is None:
                raise HTTPException(404, "Unknown farm")
            if farm.culled:
                raise HTTPException(409, "Farm was already culled")
            getattr(farm, INTERVENTIONS[action])()
            return state()

    @app.get("/api/sim/farms")
    def farms():
        with session.lock:
            return _collection(
                (
                    Point(farm.location),
                    {
                        "id": farm.farm_id,
                        "status": farm.status,
                        "susceptible": farm.susceptible,
                        "exposed": farm.exposed,
                        "infected": farm.infected,
                        "recovered": farm.recovered,
                        "dead": farm.dead,
                        "biosecurity": farm.biosecurity,
                        "quarantined": farm.quarantined,
                        "notified": farm.notified,
                    },
                )
                for farm in session.model.farms
            )

    @app.get("/api/sim/hubs")
    def hubs():
        with session.lock:
            return _collection(
                (
                    Point(hub.location),
                    {"id": hub.hub_id, "kind": hub.kind, "load": hub.load},
                )
                for hub in session.model.hubs
            )

    @app.get("/api/sim/birds")
    def birds():
        with session.lock:
            return _collection(
                (Point(bird.location), {"id": bird.bird_id, "state": bird.bird_state})
                for bird in session.model.birds
            )

    @app.get("/api/sim/movements")
    def movements():
        """Today's truck trips along their road routes."""
        with session.lock:
            model = session.model
            stops = {farm.farm_id: farm for farm in model.farms} | {
                hub.hub_id: hub for hub in model.hubs
            }
            today = [m for m in model.movements if m.day == model.day_count]

            def records():
                for m in today:
                    origin, destination = stops[m.origin_id], stops[m.destination_id]
                    coordinates = session.route_coordinates(
                        origin.road_node, destination.road_node
                    )
                    if not coordinates or len(coordinates) < 2:
                        coordinates = [origin.location, destination.location]
                    yield (
                        LineString(coordinates),
                        {
                            "truck_id": m.truck_id,
                            "origin_id": m.origin_id,
                            "destination_id": m.destination_id,
                            "travel_time_s": m.travel_time_s,
                            "load_on_arrival": m.load_on_arrival,
                            "introduced_infection": m.introduced_infection,
                        },
                    )

            return _collection(records())

    @app.get("/api/sim/risk")
    def risk(limit: int = Query(MAX_FEATURES, ge=1, le=MAX_FEATURES)):
        """Cells with nonzero risk; zero-risk cells are omitted, not unknown."""
        with session.lock:
            snapshot = session.risk()
            if snapshot is None:
                return dict(_collection(()), available=False)
            p = session.model.params
            levels = snapshot.levels(p.risk_yellow_threshold, p.risk_red_threshold)
            grid, spatial = snapshot.grid, session.world.spatial
            rows, cols = np.nonzero(snapshot.total > 0)
            half = grid.resolution_m / 2

            def records():
                for row, col in zip(rows.tolist(), cols.tolist(), strict=True):
                    x, y = grid.cell_center(row, col)
                    yield (
                        spatial.geometry_to_wgs84(
                            box(x - half, y - half, x + half, y + half)
                        ),
                        {
                            "row": row,
                            "col": col,
                            "bird": float(snapshot.bird[row, col]),
                            "fomite": float(snapshot.fomite[row, col]),
                            "local": float(snapshot.local[row, col]),
                            "total": float(snapshot.total[row, col]),
                            "level": _LEVEL_NAMES[int(levels[row, col])],
                        },
                    )

            return dict(_collection(records(), limit=limit), available=True)

    @app.get("/api/sim/interventions")
    def interventions():
        """Stamping-out zones around culled farms, buffered in the metric CRS."""
        with session.lock:
            model = session.model
            radius_m = model.params.stamping_out_radius_km * 1000.0
            spatial = session.world.spatial
            return _collection(
                (
                    spatial.geometry_to_wgs84(
                        Point(spatial.project(farm.location)).buffer(radius_m)
                    ),
                    {"farm_id": farm.farm_id, "radius_km": radius_m / 1000.0},
                )
                for farm in model.farms
                if farm.culled
            )

    @app.get("/api/sim/history")
    def history():
        """One summary row per simulated day (farm statuses, birds, Rt, cost)."""
        with session.lock:
            return list(session.model.history)

    @app.get("/api/sim/events")
    def events(since: int = Query(0, ge=0)):
        with session.lock:
            log = session.model.events
            start = max(since, len(log) - MAX_EVENTS)
            return {
                "next": len(log),
                "skipped": start - since,
                "events": [
                    {"day": day, "type": kind, "details": details}
                    for day, kind, details in log[start:]
                ],
            }


__all__ = ["SimulationSession", "add_simulation_routes"]
