"""Phase 3/4 smoke example using the same facade as future world consumers."""

import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("world", nargs="?", default="data/world/pe_v1")
    arguments = parser.parse_args()
    from simgeope import PernambucoWorld

    world = PernambucoWorld.load(arguments.world)
    recife = (-34.88, -8.05)
    caruaru = (-35.97, -8.28)
    municipality = world.municipality_at(recife)
    print(
        "Municipality:",
        (municipality.code, municipality.name) if municipality else None,
    )
    print("Nearest road:", world.nearest_road(recife))
    route = world.route(recife, caruaru)
    print(
        "Recife -> Caruaru:",
        {
            "travel_time_s": route.travel_time_s,
            "distance_m": route.distance_m,
            "road_edges": len(route.edge_ids),
        },
    )
    if world.hydro is not None:
        waterway = world.hydro.nearest_waterway(recife)
        waterbody = world.hydro.nearest_waterbody(recife)
        print(
            "Hydrology:",
            {
                "distance_to_water_m": world.hydro.distance_to_water(recife),
                "nearest_waterway": (waterway.id, waterway.name) if waterway else None,
                "nearest_waterbody": (waterbody.id, waterbody.name)
                if waterbody
                else None,
                "route_crossings": sum(
                    len(world.hydro.intersections_with_road(edge))
                    for edge in route.edge_ids
                ),
                "basin": world.hydro.basin_at(recife),
            },
        )
    if world.environment is not None:
        print("Environment:", world.environment_at(recife))


if __name__ == "__main__":
    main()
