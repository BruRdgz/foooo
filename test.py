"""Smoke test for the H5N1 ABM against the built world.

If the world was built without facilities.parquet, this test injects a
synthetic FacilityIndex at runtime (Option B) so the simulation can run.
"""

from simgeope.simulation.model import AgentModel, install_synthetic_facilities
from simgeope.simulation.params import ModelParams
from simgeope.world.model import PernambucoWorld


def main() -> None:
    world = PernambucoWorld.load("data/world/pe_v1")

    print("has facilities:", world.facilities is not None)
    print("has roads:     ", world.roads is not None)
    print("has hydro:     ", world.hydro is not None)
    print("has admin:     ", world.administrative is not None)

    # ---- Option B quick fix ------------------------------------------------
    if world.facilities is None:
        print("→ world has no facilities.parquet; installing 50 synthetic farms")
        install_synthetic_facilities(world, n=50, seed=42)
        print(
            f"→ installed {len(world.facilities.facilities)} farms "
            f"(snapped to roads: {world.roads is not None})"
        )
    # ------------------------------------------------------------------------

    model = AgentModel(
        world,
        params=ModelParams(beta=3.2, base_biosecurity=60, truck_uses_roads=True),
        seed=42,
        log_path="h5n1_log.csv",
    )

    model.add_farms_from_world(pop_total=5_000)
    model.add_birds_uniform(80)  # inside PE state polygon
    model.add_trucks_random(5)
    model.seed_initial_outbreak()
    model.run(180)

    print(
        f"Dia {model.day_count}",
        f"| saudáveis={model.healthy_farms}",
        f"| notificadas={model.notified_farms}",
        f"| ocultas={model.hidden_farms}",
        f"| Rt={model.current_rt:.2f}",
        f"| custo=R$ {model.cumulative_cost:,.0f}",
        f"| mortas={model.total_deaths:.0f}",
        f"| sacrificadas={model.total_culled:.0f}",
    )


if __name__ == "__main__":
    main()
