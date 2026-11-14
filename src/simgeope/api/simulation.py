"""Live H5N1 simulation served next to the world inspector.

One ``AgentModel`` per server, like the NetLogo interface: reset, step, run and
manual interventions on a selected farm. Requests share the model under a lock.
The session also keeps a compact per-day snapshot so past days can be replayed.
"""

import json
import threading
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import NamedTuple

import numpy as np
from shapely.geometry import LineString, Point, box

from simgeope.simulation import AgentModel, ModelParams, infection_risk
from simgeope.simulation.agents import HUB_PRODUCTS, ZONE_RINGS, Transmission
from simgeope.simulation.model import (
    DEMO_POULTRY_POLES,
    Event,
    install_synthetic_facilities,
)
from simgeope.simulation.params import PARAM_PRESETS, PARAM_SPECS, override_params
from simgeope.simulation.risk import GREEN, RED, YELLOW
from simgeope.world.exceptions import RouteNotFoundError

from .app import MAX_FEATURES, _collection, _route_coordinates

MAX_STEP_DAYS = 60
MAX_EVENTS = 500
MAX_BODY_BYTES = 4096
# A full parameter override is under 2 KiB; the margin allows for new parameters.
MAX_RESET_BODY_BYTES = 16 * 1024
# Two simulated years of replayable days.
MAX_SNAPSHOT_DAYS = 730
# Most recent truck trips listed in a farm's detail.
MAX_FARM_VISITS = 60
INTERVENTIONS = {
    "stamping_out": "apply_stamping_out",
    "movement_block": "apply_movement_block",
    "biosecurity_boost": "apply_biosecurity_boost",
}
_LEVEL_NAMES = {GREEN: "green", YELLOW: "yellow", RED: "red"}
# Farm statuses with birds incubating or infectious (counted as infected days).
INFECTED_STATUSES = ("hidden", "notified")


class FarmDay(NamedTuple):
    """A farm's state at the end of one day (after any intervention)."""

    status: str
    susceptible: float
    exposed: float
    infected: float
    recovered: float
    dead: float
    new_exposed: float
    biosecurity: float
    quarantined: bool
    notified: bool
    culled: bool
    under_biosec_boost: bool
    first_infection_day: int
    detection_day: int
    zone: str | None
    focus: bool


@dataclass(frozen=True, slots=True)
class DaySnapshot:
    """Per-day state; farm and hub rows follow ``model.farms``/``model.hubs``.

    Static attributes (names, positions, populations, fleets) stay on the
    model, whose farm and hub lists do not change after a reset. Birds come
    and go, so each day keeps its own ids, (lon, lat) rows and one S/I/R
    letter per bird: a few dozen bytes per bird instead of a tuple of objects.
    """

    farms: tuple[FarmDay, ...]
    hub_loads: tuple[float, ...]
    bird_ids: tuple[str, ...]
    bird_locations: np.ndarray
    bird_states: str

    @classmethod
    def of(cls, model: AgentModel) -> "DaySnapshot":
        return cls(
            farms=tuple(
                FarmDay(
                    farm.status,
                    farm.susceptible,
                    farm.exposed,
                    farm.infected,
                    farm.recovered,
                    farm.dead,
                    farm.new_exposed_today,
                    farm.biosecurity,
                    farm.quarantined,
                    farm.notified,
                    farm.culled,
                    farm.under_biosec_boost,
                    farm.first_infection_day,
                    farm.detection_day,
                    farm.zone,
                    farm.focus,
                )
                for farm in model.farms
            ),
            hub_loads=tuple(hub.load for hub in model.hubs),
            bird_ids=tuple(bird.bird_id for bird in model.birds),
            bird_locations=np.array(
                [bird.location for bird in model.birds], dtype=float
            ).reshape(-1, 2),
            bird_states="".join(bird.bird_state for bird in model.birds),
        )


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
        self._municipality_names = {
            m.code: m.name for m in world.administrative.municipalities
        }
        self.lock = threading.Lock()
        self._route_cache: dict[tuple[int, int], list | None] = {}
        self.reset(seed)

    def reset(self, seed: int, params: ModelParams | None = None) -> None:
        """Start a new scenario; `params`, if given, become the session's own.

        Nothing is replaced unless the new model is built successfully.
        """
        params = self.params if params is None else params
        # A fresh copy keeps parameter edits from leaking across resets.
        model = AgentModel(self.world, params=replace(params), seed=seed)
        model.add_farms_from_world(pop_total=self.pop_total)
        model.add_birds_uniform(self.birds)
        model.add_hubs_from_world()
        model.setup_fleets()
        model.seed_initial_outbreak()
        snapshots = {model.day_count: DaySnapshot.of(model)}
        with self.lock:
            self.seed = seed
            self.params = params
            self.model = model
            # Retained days in ascending order, guarded by the lock.
            self.snapshots: dict[int, DaySnapshot] = snapshots
            self._risk = None

    def step(self, days: int) -> None:
        with self.lock:
            for _ in range(days):
                self.model.step()
                self.record_snapshot()
            self._risk = None

    def record_snapshot(self) -> None:
        """Store (or refresh) today's snapshot; the caller holds the lock."""
        self.snapshots[self.model.day_count] = DaySnapshot.of(self.model)
        while len(self.snapshots) > MAX_SNAPSHOT_DAYS:
            del self.snapshots[next(iter(self.snapshots))]

    def municipality(self, facility) -> str | None:
        return self._municipality_names.get(facility.municipality_code)

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

    async def body(
        request: Request, allowed: set[str], limit: int = MAX_BODY_BYTES
    ) -> dict:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > limit:
                raise HTTPException(
                    413, f"Simulation request exceeds {limit // 1024} KiB"
                )
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

    def day_snapshot(day: int | None) -> tuple[int, DaySnapshot]:
        """`day` (default today) and its snapshot; 404 if it is not retained."""
        day = session.model.day_count if day is None else day
        snapshot = session.snapshots.get(day)
        if snapshot is None:
            raise HTTPException(404, f"Day {day} is not retained")
        return day, snapshot

    def check_window(from_day: int | None, to_day: int | None) -> None:
        if from_day is not None and to_day is not None and from_day > to_day:
            raise HTTPException(422, "from_day must not exceed to_day")

    def event_record(event: Event) -> dict:
        return {
            "day": event.day,
            "type": event.kind,
            "details": event.details,
            "farm_id": event.farm_id,
        }

    def protocol_state(model) -> dict:
        today = model.day_count
        p = model.params
        active = [focus for focus in model.foci if focus.active_on(today)]
        # Culled farms are inside the rings but no longer under restriction.
        zones = Counter(farm.zone for farm in model.farms if not farm.culled)
        return {
            "enabled": bool(p.protocol_enabled),
            "active_foci": len(active),
            "standstill_foci": sum(
                focus.standstill_on(today, p.standstill_days) for focus in active
            ),
            "farms_in_zones": {ring: zones[ring] for ring in ZONE_RINGS},
        }

    def state():
        model, p = session.model, session.model.params
        return {
            "day": model.day_count,
            "date": model.current_date.isoformat(),
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
            "snapshot_days": [next(iter(session.snapshots)), model.day_count],
            "max_step_days": MAX_STEP_DAYS,
            "active_quarantines": model.active_quarantines,
            "protocol": protocol_state(model),
            "lots": model.lot_summary(),
        }

    @app.get("/api/sim/state")
    def get_state():
        with session.lock:
            return state()

    @app.get("/api/sim/params")
    def get_params():
        """Current and default model parameters with their editing schema."""
        with session.lock:
            current = session.params
        defaults = ModelParams()
        return {
            "values": {spec.name: getattr(current, spec.name) for spec in PARAM_SPECS},
            "defaults": {
                spec.name: getattr(defaults, spec.name) for spec in PARAM_SPECS
            },
            "schema": [asdict(spec) | {"type": spec.type} for spec in PARAM_SPECS],
            "presets": [
                {
                    "id": preset.id,
                    "label": preset.label,
                    "description": preset.description,
                    "values": dict(preset.values),
                }
                for preset in PARAM_PRESETS
            ],
        }

    @app.post("/api/sim/reset")
    async def reset(request: Request):
        """New scenario; `params` overrides persist for later resets."""
        payload = await body(request, {"seed", "params"}, MAX_RESET_BODY_BYTES)
        seed = integer(payload.get("seed", session.seed), "seed", 0, 2**32 - 1)
        overrides = payload.get("params", {})
        if not isinstance(overrides, dict):
            raise HTTPException(422, "params must be an object of parameter values")
        with session.lock:
            current = session.params
        try:
            params = override_params(current, overrides)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        await run_in_threadpool(session.reset, seed, params)
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
            # Stamping-out also quarantines neighbours: re-snapshot every farm.
            session.record_snapshot()
            # Today's risk depends on farm state, so the cached day is stale.
            session._risk = None
            return state()

    def farm_properties(farm, row: FarmDay) -> dict:
        facility = farm.facility
        return {
            "id": farm.farm_id,
            "name": facility.name,
            "municipality": session.municipality(facility),
            "kind": facility.kind,
            "pop_total": farm.pop_total,
            **row._asdict(),
        }

    @app.get("/api/sim/farms")
    def farms(day: int | None = None):
        with session.lock:
            _, snapshot = day_snapshot(day)
            return _collection(
                (Point(farm.location), farm_properties(farm, row))
                for farm, row in zip(session.model.farms, snapshot.farms, strict=True)
            )

    @app.get("/api/sim/farms/{farm_id}")
    def farm_detail(farm_id: str):
        """Today's farm state with its retained daily history and recent log."""
        with session.lock:
            model = session.model
            farm = model.farm_by_id(farm_id)
            if farm is None:
                raise HTTPException(404, "Unknown farm")
            row = model.farms.index(farm)
            # Straight-line distance in the metric CRS, not road travel time.
            nearest = min(
                model.hubs,
                key=lambda hub: (
                    model.distance_m(farm.location, hub.location),
                    hub.hub_id,
                ),
                default=None,
            )
            history = []
            for day, snapshot in session.snapshots.items():
                past = snapshot.farms[row]
                history.append(
                    {
                        "day": day,
                        "status": past.status,
                        "susceptible": past.susceptible,
                        "exposed": past.exposed,
                        "infected": past.infected,
                        "recovered": past.recovered,
                        "dead": past.dead,
                        "biosecurity": past.biosecurity,
                        "quarantined": past.quarantined,
                    }
                )
            events = [e for e in model.events if e.farm_id == farm_id][-MAX_EVENTS:]
            visits = [
                m for m in model.movements if farm_id in (m.origin_id, m.destination_id)
            ][-MAX_FARM_VISITS:]
            return {
                **farm_properties(farm, session.snapshots[model.day_count].farms[row]),
                "lon": farm.lon,
                "lat": farm.lat,
                "nearest_hub": None
                if nearest is None
                else {"id": nearest.hub_id, "kind": nearest.kind},
                "history": history,
                "events": [event_record(e) for e in events],
                "visits": [
                    {
                        "day": m.day,
                        "truck_id": m.truck_id,
                        "origin_id": m.origin_id,
                        "destination_id": m.destination_id,
                        "load_on_arrival": m.load_on_arrival,
                        "introduced_infection": m.introduced_infection,
                    }
                    for m in visits
                ],
            }

    @app.get("/api/sim/hubs")
    def hubs(day: int | None = None):
        with session.lock:
            model = session.model
            _, snapshot = day_snapshot(day)
            trucks = Counter(truck.base.hub_id for truck in model.trucks)
            return _collection(
                (
                    Point(hub.location),
                    {
                        "id": hub.hub_id,
                        "kind": hub.kind,
                        "load": load,
                        "name": hub.facility.name,
                        "trucks": trucks[hub.hub_id],
                    },
                )
                for hub, load in zip(model.hubs, snapshot.hub_loads, strict=True)
            )

    @app.get("/api/sim/birds")
    def birds(day: int | None = None):
        with session.lock:
            _, snapshot = day_snapshot(day)
            return _collection(
                (Point(location), {"id": bird_id, "state": state})
                for bird_id, location, state in zip(
                    snapshot.bird_ids,
                    snapshot.bird_locations.tolist(),
                    snapshot.bird_states,
                    strict=True,
                )
            )

    @app.get("/api/sim/movements")
    def movements(day: int | None = None):
        """The day's truck trips (default today) along their road routes."""
        with session.lock:
            model = session.model
            day, _ = day_snapshot(day)
            stops = {farm.farm_id: farm for farm in model.farms} | {
                hub.hub_id: hub for hub in model.hubs
            }
            trips = [m for m in model.movements if m.day == day]

            def records():
                for m in trips:
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
    def interventions(day: int | None = None):
        """Stamping-out zones around culled farms, buffered in the metric CRS."""
        with session.lock:
            model = session.model
            _, snapshot = day_snapshot(day)
            radius_m = model.params.stamping_out_radius_km * 1000.0
            spatial = session.world.spatial
            return _collection(
                (
                    spatial.geometry_to_wgs84(
                        Point(spatial.project(farm.location)).buffer(radius_m)
                    ),
                    {"farm_id": farm.farm_id, "radius_km": radius_m / 1000.0},
                )
                for farm, row in zip(model.farms, snapshot.farms, strict=True)
                if row.culled
            )

    @app.get("/api/sim/zones")
    def zones(day: int | None = None):
        """Emergency areas of the foci active on `day` (default today).

        One polygon per (focus, ring), drawn as rings around the focus farm in
        the metric CRS: the perifoco disk, then the vigilância and proteção
        annuli. `radius_km` is the ring's outer radius.
        """
        with session.lock:
            model = session.model
            day, _ = day_snapshot(day)
            p = model.params
            if not p.protocol_enabled:
                return _collection(())
            spatial = session.world.spatial
            radii_km = (
                p.perifocus_radius_km,
                p.surveillance_radius_km,
                p.protection_radius_km,
            )

            def records():
                for focus in model.foci:
                    if not focus.active_on(day):
                        continue
                    centre = Point(
                        spatial.project(model.farm_by_id(focus.farm_id).location)
                    )
                    standstill = focus.standstill_on(day, p.standstill_days)
                    inner = None
                    for ring, radius_km in zip(ZONE_RINGS, radii_km, strict=True):
                        disk = centre.buffer(radius_km * 1000.0)
                        shape = disk if inner is None else disk.difference(inner)
                        inner = disk
                        yield (
                            spatial.geometry_to_wgs84(shape),
                            {
                                "focus_id": focus.farm_id,
                                "ring": ring,
                                "radius_km": radius_km,
                                "confirmed_day": focus.confirmed_day,
                                "standstill": standstill,
                                "release_day": focus.release_day,
                            },
                        )

            return _collection(records())

    def bird_location(day: int, bird_id: str | None):
        """Where the bird was at the end of `day`, if that day is retained."""
        snapshot = session.snapshots.get(day)
        if snapshot is None or bird_id not in snapshot.bird_ids:
            return None
        return tuple(snapshot.bird_locations[snapshot.bird_ids.index(bird_id)].tolist())

    def transmission_path(transmission: Transmission, hubs_by_id) -> list:
        """Straight segments source farm -> relaying hub -> target farm."""
        model = session.model
        target = model.farm_by_id(transmission.farm_id).location
        if transmission.pathway == "bird":
            seen = bird_location(transmission.day, transmission.bird_id)
            return [seen or target, target]
        path = []
        if transmission.source_farm_id is not None:
            path.append(model.farm_by_id(transmission.source_farm_id).location)
        if transmission.hub_id is not None:
            path.append(hubs_by_id[transmission.hub_id].location)
        path.append(target)
        return path

    @app.get("/api/sim/transmissions")
    def transmissions(from_day: int | None = None, to_day: int | None = None):
        """Introductions in the window with their path, and hubs ranked by them.

        Fomite introductions count for a hub only when its environment relayed
        the load; a load carried straight between farms has no hub.
        """
        check_window(from_day, to_day)
        with session.lock:
            model = session.model
            low = 0 if from_day is None else from_day
            high = model.day_count if to_day is None else to_day
            hubs_by_id = {hub.hub_id: hub for hub in model.hubs}
            window = [t for t in model.transmissions if low <= t.day <= high]
            relayed: dict[str, list[Transmission]] = {}
            for t in window:
                if t.pathway == "fomite" and t.hub_id is not None:
                    relayed.setdefault(t.hub_id, []).append(t)
            hubs = [
                {
                    "id": hub.hub_id,
                    "name": hub.facility.name,
                    "kind": hub.kind,
                    "lon": hub.lon,
                    "lat": hub.lat,
                    "introductions": len(relayed.get(hub.hub_id, ())),
                    "source_farms": len(
                        {
                            t.source_farm_id
                            for t in relayed.get(hub.hub_id, ())
                            if t.source_farm_id is not None
                        }
                    ),
                }
                for hub in model.hubs
            ]
            hubs.sort(key=lambda hub: (-hub["introductions"], hub["id"]))
            return {
                "transmissions": [
                    asdict(t) | {"path": transmission_path(t, hubs_by_id)}
                    for t in window
                ],
                "hubs": hubs,
            }

    @app.get("/api/sim/lots")
    def lots(from_day: int | None = None, to_day: int | None = None):
        """Product lots collected in the window, and product hubs ranked by them.

        `exposure_days` is recall day minus collection day for recalled lots
        and null otherwise. Hub tallies count the window's lots by their
        destination hub, most exposed lots first.
        """
        check_window(from_day, to_day)
        with session.lock:
            model = session.model
            low = 0 if from_day is None else from_day
            high = model.day_count if to_day is None else to_day
            window = [lot for lot in model.lots if low <= lot.day <= high]
            by_hub: dict[str, Counter[str]] = {}
            for lot in window:
                tally = by_hub.setdefault(lot.hub_id, Counter())
                tally["lots"] += 1
                tally["quantity"] += lot.quantity
                if lot.exposed:
                    tally["exposed_lots"] += 1
                    tally["exposed_quantity"] += lot.quantity
                if lot.trace is not None:
                    tally[f"{lot.trace}_lots"] += 1
            hubs = []
            for hub in model.hubs:
                if hub.kind not in HUB_PRODUCTS:
                    continue
                tally = by_hub.get(hub.hub_id, Counter())
                hubs.append(
                    {
                        "id": hub.hub_id,
                        "name": hub.facility.name,
                        "kind": hub.kind,
                        "product": HUB_PRODUCTS[hub.kind],
                        "lon": hub.lon,
                        "lat": hub.lat,
                        "lots": tally["lots"],
                        "quantity": float(tally["quantity"]),
                        "exposed_lots": tally["exposed_lots"],
                        "exposed_quantity": float(tally["exposed_quantity"]),
                        "recalled_lots": tally["recalled_lots"],
                        "outside_window_lots": tally["outside_window_lots"],
                    }
                )
            hubs.sort(key=lambda hub: (-hub["exposed_lots"], hub["id"]))
            return {
                "lots": [
                    asdict(lot)
                    | {
                        "exposed": lot.exposed,
                        "exposure_days": None
                        if lot.trace != "recalled"
                        else lot.trace_day - lot.day,
                    }
                    for lot in window
                ],
                "hubs": hubs,
            }

    @app.get("/api/sim/heat")
    def heat(from_day: int | None = None, to_day: int | None = None):
        """Per-farm introductions, infected days and new exposures in a window.

        The window is clamped to the retained days, for every field, and
        reported as `window` (null when no retained day falls in it).
        """
        check_window(from_day, to_day)
        with session.lock:
            model = session.model
            first = next(iter(session.snapshots))
            low = first if from_day is None else max(from_day, first)
            high = model.day_count if to_day is None else min(to_day, model.day_count)
            if low > high:
                return dict(_collection(()), window=None)
            introductions = Counter(
                t.farm_id for t in model.transmissions if low <= t.day <= high
            )
            infected_days = [0] * len(model.farms)
            exposed = [0.0] * len(model.farms)
            for day, snapshot in session.snapshots.items():
                if low <= day <= high:
                    for row, past in enumerate(snapshot.farms):
                        if past.status in INFECTED_STATUSES:
                            infected_days[row] += 1
                        exposed[row] += past.new_exposed
            records = [
                (
                    Point(farm.location),
                    {
                        "id": farm.farm_id,
                        "introductions": introductions[farm.farm_id],
                        "infected_days": infected_days[row],
                        "exposed": exposed[row],
                    },
                )
                for row, farm in enumerate(model.farms)
                if introductions[farm.farm_id] or infected_days[row] or exposed[row]
            ]
            return dict(_collection(records), window=[low, high])

    @app.get("/api/sim/flocks")
    def flocks(from_day: int | None = None, to_day: int | None = None):
        """Migratory flock arrivals in the window (default: today only)."""
        check_window(from_day, to_day)
        with session.lock:
            model = session.model
            high = model.day_count if to_day is None else to_day
            low = high if from_day is None else from_day
            return _collection(
                (
                    Point(flock.lon, flock.lat),
                    {"day": flock.day, "size": flock.size, "infected": flock.infected},
                )
                for flock in model.flock_arrivals
                if low <= flock.day <= high
            )

    @app.get("/api/sim/history")
    def history():
        """One summary row per day from day 0 (farms, birds, Rt, cost, flows)."""
        with session.lock:
            return list(session.model.history)

    @app.get("/api/sim/events")
    def events(
        since: int = Query(0, ge=0),
        farm_id: str | None = None,
        kind: str | None = Query(None, alias="type"),
    ):
        """Log entries from index `since`, at most the last MAX_EVENTS of them.

        `next` and `skipped` index the full log; the optional `farm_id` and
        `type` filters only drop entries from the returned window.
        """
        with session.lock:
            log = session.model.events
            start = max(since, len(log) - MAX_EVENTS)
            return {
                "next": len(log),
                "skipped": start - since,
                "events": [
                    event_record(event)
                    for event in log[start:]
                    if (farm_id is None or event.farm_id == farm_id)
                    and (kind is None or event.kind == kind)
                ],
            }


__all__ = ["SimulationSession", "add_simulation_routes"]
