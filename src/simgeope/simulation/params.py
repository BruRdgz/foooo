"""Tunable parameters mirroring the NetLogo interface sliders."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import get_type_hints


@dataclass(frozen=True, slots=True)
class FleetSpec:
    """Trucks based at every hub facility of one kind, serving nearby farms.

    Examples: ``feed_mill`` (ração: fábrica → várias granjas → fábrica),
    ``slaughterhouse`` (aves vivas: abatedouro → granja → abatedouro), or any
    other hub kind present in the facility table, such as ``market``.
    ``client_kinds`` limits the farms it serves by facility kind (e.g. egg
    collection only at laying farms); None serves every farm.
    """

    hub_kind: str
    trucks_per_hub: int
    max_stops_per_day: int
    client_kinds: tuple[str, ...] | None = None


@dataclass(slots=True)
class ModelParams:
    # --- calendário -------------------------------------------------------
    # Day 0 is the 1st of this month (see AgentModel.current_date); seasonal
    # processes read the date, not the day count.
    start_month: int = 1

    # --- SEIR intra-granja ------------------------------------------------
    beta: float = 3.2
    incubation_days: float = 4.0
    infectious_period_days: float = 3.0
    mortality_rate: float = 90.0  # %
    base_biosecurity: float = 50.0  # 0-100
    initial_infected: int = 50

    # --- vigilância / reação ---------------------------------------------
    detection_time_max: int = 7
    reaction_time_max: int = 3
    generation_interval_days: int = 5
    # Passive surveillance (SRN): besides the drawn detection day, a farm is
    # notified as a suspected case once its deaths over the last window reach
    # this share of the housed flock, whichever comes first.
    mortality_notification: int = 1  # 0/1
    mortality_alert_pct: float = 10.0
    mortality_alert_window_days: int = 3

    # --- aves silvestres (SIR com chegada e partida migratória) ----------
    # Illustrative defaults for a demonstrative scenario, not field estimates.
    bird_infection_duration: int = 5
    bird_mortality_pct: float = 30.0  # infectadas que morrem; o resto se recupera
    infection_prob_bird_to_farm: float = 15.0  # %
    seed_size_birds: int = 10
    initial_bird_infection_pct: float = 10.0  # %
    bird_step_m: float = 2000.0  # raio típico de forrageio em torno do poleiro
    bird_water_pull_prob: float = 0.60  # residentes com poleiro na água
    farm_water_proximity_km: float = 3.0
    bird_farm_proximity_km: float = 3.0
    # Per infected roost-mate per day, for roosts within bird_contact_km.
    bird_to_bird_prob: float = 8.0  # %
    bird_contact_km: float = 1.0
    # Spillback: wild birds near an infected farm (within bird_farm_proximity_km).
    farm_to_bird_prob: float = 5.0  # %
    # Migratory flow: flocks land together at water sites (Poisson events,
    # 1 + Poisson(flock_size_mean - 1) birds) and each bird stays a geometric
    # time, so the population settles near flocks x size x stay. An infected
    # flock carries one infected index bird. The daily rate is the peak-month
    # rate, scaled by the month's MIGRATORY_ARRIVAL_PROFILE entry (model.py).
    migratory_flocks_per_day: float = 0.3
    # Share of flocks landing at the documented Pernambuco sites
    # (reference/migracao_sitios_pe.csv); the rest land at size-weighted water.
    migratory_site_fraction: float = 0.5
    flock_size_mean: float = 8.0
    migratory_infected_pct: float = 15.0  # % dos bandos
    bird_mean_stay_days: float = 30.0

    # --- transmissão local entre granjas vizinhas ------------------------
    # Daily chance from a fully shedding neighbour at distance zero; it falls
    # linearly to zero at local_spread_radius_km and scales with biosecurity.
    local_spread_prob: float = 10.0  # %
    local_spread_radius_km: float = 3.0
    seed_size_local: int = 10

    # --- fim de surto -----------------------------------------------------
    # Quarantine of a non-culled farm is lifted this long after infection ends.
    quarantine_release_days: int = 21

    # --- fômites (caminhões) ---------------------------------------------
    # Facility kinds that become farms; every other kind may be a fleet hub.
    # "farm_layer"/"farm_broiler" are postura/corte (see FarmAgent.production);
    # "farm" and "producer" are farms of unknown production type.
    farm_kinds: tuple[str, ...] = ("farm", "producer", "farm_layer", "farm_broiler")
    # Illustrative defaults, to be calibrated against real fleet data. Feed
    # reaches every farm; live birds go to slaughter from broiler farms and,
    # for worlds without production types, from farms of unknown type. Product
    # fleets (slaughterhouse: live birds; egg_grading: eggs) also record a lot
    # per farm collection (see agents.Lot). The egg fleet's trays and boxes
    # returning through the entreposto are its fomite pathway (hub exchange).
    fleets: tuple[FleetSpec, ...] = (
        FleetSpec("feed_mill", trucks_per_hub=3, max_stops_per_day=4),
        FleetSpec(
            "slaughterhouse",
            trucks_per_hub=2,
            max_stops_per_day=1,
            client_kinds=("farm_broiler", "farm", "producer"),
        ),
        # Assumption: no open data on egg-collection frequency or fleet size;
        # one truck collects at a few laying farms per day.
        FleetSpec(
            "egg_grading",
            trucks_per_hub=2,
            max_stops_per_day=3,
            client_kinds=("farm_layer",),
        ),
    )
    truck_shift_hours: float = 10.0
    truck_service_minutes: float = 45.0  # carga/descarga por parada
    # Max chance per visit for a fully loaded truck at a farm without biosecurity.
    infection_prob_truck_to_farm: float = 20.0  # %
    seed_size_fomite: int = 15
    # A full load decays to negligible after this many days (NetLogo slider).
    fomite_survival_days: int = 5
    # Farm prevalence (I/N) at which a visiting truck leaves fully loaded.
    fomite_full_load_prevalence: float = 0.01
    # Fraction of load exchanged between a truck and its base hub's environment.
    hub_transfer_fraction: float = 0.5
    truck_disinfection_prob: float = 0.5  # lavagem ao voltar à base

    # --- produção e mercados (lotes e rastreamento) -----------------------
    # Eggs per live hen per day (IBGE POG, PE, 1st quarter 2025; derivation
    # in the ParamSpec source and docs/FONTES_DADOS.md).
    eggs_per_hen_day: float = 0.8
    # Live birds per slaughter-truck visit; the flock is not reduced by it.
    broiler_lot_birds: int = 5000
    # Lots collected this many days before the trigger are recalled (two
    # 14-day incubation periods, Plano de Contingência §7.1).
    trace_window_days: int = 28

    # --- camada de risco (probabilidade diária de introdução) -------------
    # Initial cut-offs at default parameters: a fully loaded truck visit to a
    # base-biosecurity farm (10%) is yellow; infected birds nearby (15%) red.
    risk_yellow_threshold: float = 0.05
    risk_red_threshold: float = 0.15

    # --- intervenções / economia -----------------------------------------
    stamping_out_radius_km: float = 5.0
    cull_cost_per_bird: float = 8.0
    quarantine_cost_per_day: float = 500.0
    biosecurity_boost_amount: float = 20.0
    biosecurity_investment_cost: float = 5000.0

    # --- protocolo oficial (MAPA: POP de trânsito nov/2023 e Plano de -----
    # Contingência IAAP/DNC jun/2023). Off by default; sources and the
    # remaining assumptions are in PARAM_SPECS and docs/PROTOCOLO_OFICIAL.md.
    protocol_enabled: int = 0  # 0/1
    confirmation_days: int = 4
    auto_depopulation: int = 1  # 0/1
    # Radii are measured from the focus (3 + 7 = 10, 10 + 15 = 25 km).
    perifocus_radius_km: float = 3.0
    surveillance_radius_km: float = 10.0
    protection_radius_km: float = 25.0
    standstill_days: int = 7
    zone_disinfection_prob: float = 0.95
    zone_release_days: int = 28
    # Intervals between active-surveillance inspections in each ring; the
    # names predate the periodic visits.
    perifocus_detection_days: int = 3
    surveillance_detection_days: int = 7
    # After a cull: fallow, then a partial restocking under periodic
    # inspection, then full capacity.
    fallow_days: int = 30
    restock_pct: float = 20.0
    restock_monitoring_days: int = 60
    restock_inspection_days: int = 7


_FIELD_TYPES = get_type_hints(ModelParams)


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """How the interface edits one scalar ``ModelParams`` field.

    Bounds follow the NetLogo slider where one exists; otherwise they are
    plausible ranges that include the default and keep every divisor nonzero.
    The migratory population settles near flocks/day x flock size x stay, so
    those three are capped at 1 x 20 x 60 (about 1,200 birds): every bird is
    checked against every farm daily and kept in each replayable day.
    ``assumption`` marks values not stated by their ``source`` (a short PT-BR
    citation, or "suposição: ..." explaining the guess).
    """

    name: str
    label: str
    group: str
    min: float
    max: float
    step: float
    unit: str = ""
    assumption: bool = False
    source: str | None = None

    @property
    def type(self) -> str:
        """The field's declared type, ``"int"`` or ``"float"``."""
        return _FIELD_TYPES[self.name].__name__


_CALENDAR = "Calendário"
_SEIR = "SEIR intra-granja"
_SURVEILLANCE = "Vigilância e reação"
_BIRDS = "Aves silvestres"
_LOCAL = "Transmissão local"
_FOMITES = "Fômites (caminhões)"
_MARKETS = "Produção e mercados"
_RISK = "Camada de risco"
_ECONOMY = "Intervenções e economia"
_PROTOCOL = "Protocolo oficial (MAPA 2023)"
# Official sources, cited by the short names below in ParamSpec.source.
_POP = "MAPA, POP Postos Fixos e Equipes Volantes — IAAP e DNC, v1.0, nov/2023"
_PLAN = "Plano de Contingência IAAP/DNC (MAPA, Parte Específica, v1.0, jun/2023)"
_FICHA = "Ficha Técnica IA (MAPA/DSA, jul/2023)"
_INVESTIGATIONS = "planilha MAPA de investigações com coleta de amostras, 2022–2025"

# In the interface's group order.
PARAM_SPECS: tuple[ParamSpec, ...] = (
    ParamSpec("start_month", "Mês de início da simulação", _CALENDAR, 1, 12, 1),
    ParamSpec("beta", "Taxa de transmissão (β)", _SEIR, 0.5, 5.0, 0.1),
    ParamSpec("incubation_days", "Período de incubação", _SEIR, 1, 14, 1, "dias"),
    ParamSpec("infectious_period_days", "Período infeccioso", _SEIR, 1, 10, 1, "dias"),
    ParamSpec("mortality_rate", "Mortalidade", _SEIR, 50, 100, 5, "%"),
    ParamSpec("base_biosecurity", "Biosseguridade inicial", _SEIR, 0, 100, 5, "%"),
    ParamSpec("initial_infected", "Aves expostas na introdução", _SEIR, 1, 500, 10),
    ParamSpec(
        "detection_time_max",
        "Tempo máximo até detecção",
        _SURVEILLANCE,
        1,
        30,
        1,
        "dias",
    ),
    ParamSpec(
        "reaction_time_max", "Tempo máximo de reação", _SURVEILLANCE, 0, 14, 1, "dias"
    ),
    ParamSpec(
        "generation_interval_days",
        "Intervalo de geração (Rt)",
        _SURVEILLANCE,
        1,
        10,
        1,
        "dias",
    ),
    ParamSpec(
        "mortality_notification",
        "Notificar por mortalidade (caso suspeito de SRN)",
        _SURVEILLANCE,
        0,
        1,
        1,
        source=f"{_FICHA}, p. 3: caso suspeito de SRN, notificação imediata ao SVO",
    ),
    ParamSpec(
        "mortality_alert_pct",
        "Mortalidade que caracteriza a suspeita",
        _SURVEILLANCE,
        1,
        100,
        1,
        "%",
        source=f"{_FICHA}, p. 3: 'mortalidade maior ou igual a 10% em até 72 "
        "horas' em estabelecimento comercial",
    ),
    ParamSpec(
        "mortality_alert_window_days",
        "Janela da mortalidade",
        _SURVEILLANCE,
        1,
        14,
        1,
        "dias",
        source=f"{_FICHA}, p. 3: 'em até 72 horas' (3 dias)",
    ),
    ParamSpec(
        "quarantine_release_days",
        "Quarentena após fim da infecção",
        _SURVEILLANCE,
        0,
        90,
        1,
        "dias",
    ),
    ParamSpec(
        "bird_infection_duration",
        "Duração da infecção nas aves",
        _BIRDS,
        1,
        14,
        1,
        "dias",
    ),
    ParamSpec(
        "bird_mortality_pct", "Mortalidade das aves infectadas", _BIRDS, 0, 100, 5, "%"
    ),
    ParamSpec(
        "infection_prob_bird_to_farm", "Infecção ave → granja", _BIRDS, 0, 100, 5, "%"
    ),
    ParamSpec(
        "seed_size_birds", "Aves expostas por introdução silvestre", _BIRDS, 1, 100, 5
    ),
    ParamSpec(
        "initial_bird_infection_pct",
        "Aves residentes infectadas no início",
        _BIRDS,
        0,
        50,
        5,
        "%",
    ),
    ParamSpec("bird_step_m", "Raio de forrageio", _BIRDS, 0, 10_000, 100, "m"),
    ParamSpec("bird_water_pull_prob", "Fração com poleiro na água", _BIRDS, 0, 1, 0.05),
    ParamSpec(
        "farm_water_proximity_km", "Granja perto da água até", _BIRDS, 0, 20, 0.5, "km"
    ),
    ParamSpec("bird_farm_proximity_km", "Alcance ave–granja", _BIRDS, 0, 20, 0.5, "km"),
    ParamSpec(
        "bird_to_bird_prob", "Infecção ave → ave por dia", _BIRDS, 0, 100, 1, "%"
    ),
    ParamSpec(
        "bird_contact_km",
        "Distância de contato entre poleiros",
        _BIRDS,
        0,
        10,
        0.5,
        "km",
    ),
    ParamSpec(
        "farm_to_bird_prob", "Infecção granja → ave por dia", _BIRDS, 0, 100, 1, "%"
    ),
    ParamSpec(
        "migratory_flocks_per_day",
        "Bandos migratórios por dia no pico (set–nov)",
        _BIRDS,
        0,
        1,
        0.1,
        assumption=True,
        source="suposição: taxa ilustrativa no mês de pico; o perfil mensal que "
        "a reduz nos outros meses segue Azevedo Jr. 1992 (Coroa do Avião, "
        "limícolas árticas de agosto a abril) e CEMAVE (chegada em setembro)",
    ),
    ParamSpec(
        "migratory_site_fraction",
        "Fração dos bandos nos sítios de pouso conhecidos",
        _BIRDS,
        0,
        1,
        0.05,
        assumption=True,
        source="suposição: metade dos bandos pousa nos sítios documentados em PE "
        "(CEMAVE; Azevedo Jr. 1992); o resto representa sítios não inventariados",
    ),
    ParamSpec("flock_size_mean", "Tamanho médio do bando", _BIRDS, 1, 20, 1),
    ParamSpec(
        "migratory_infected_pct", "Bandos com ave infectada", _BIRDS, 0, 100, 5, "%"
    ),
    ParamSpec(
        "bird_mean_stay_days", "Permanência média das aves", _BIRDS, 1, 60, 1, "dias"
    ),
    ParamSpec(
        "local_spread_prob",
        "Transmissão vizinha à distância zero",
        _LOCAL,
        0,
        100,
        1,
        "%",
    ),
    ParamSpec(
        "local_spread_radius_km",
        "Raio da transmissão vizinha",
        _LOCAL,
        0.5,
        20,
        0.5,
        "km",
    ),
    ParamSpec(
        "seed_size_local", "Aves expostas por transmissão vizinha", _LOCAL, 1, 100, 1
    ),
    ParamSpec("truck_shift_hours", "Turno do caminhão (h)", _FOMITES, 1, 24, 0.5),
    ParamSpec("truck_service_minutes", "Tempo por parada (min)", _FOMITES, 0, 240, 5),
    ParamSpec(
        "infection_prob_truck_to_farm",
        "Infecção caminhão → granja",
        _FOMITES,
        0,
        100,
        5,
        "%",
    ),
    ParamSpec("seed_size_fomite", "Aves expostas por caminhão", _FOMITES, 1, 100, 5),
    ParamSpec(
        "fomite_survival_days", "Sobrevivência do fômite", _FOMITES, 1, 14, 1, "dias"
    ),
    ParamSpec(
        "fomite_full_load_prevalence",
        "Prevalência para carga máxima",
        _FOMITES,
        0.001,
        1,
        0.001,
    ),
    ParamSpec(
        "hub_transfer_fraction", "Fração trocada com a base", _FOMITES, 0, 1, 0.05
    ),
    ParamSpec(
        "truck_disinfection_prob", "Chance de lavagem na base", _FOMITES, 0, 1, 0.05
    ),
    ParamSpec(
        "eggs_per_hen_day",
        "Produção de ovos por poedeira",
        _MARKETS,
        0,
        1,
        0.01,
        "ovos/dia",
        source="IBGE, Pesquisa da Produção de Ovos de Galinha (tabela 7524), "
        "PE, 1º tri 2025: 79.752 mil dúzias × 12 ÷ (13.352.483 poedeiras × 90 "
        "dias) = 0,796 ovo/ave/dia",
    ),
    ParamSpec(
        "broiler_lot_birds",
        "Aves vivas por carga de abate",
        _MARKETS,
        100,
        30_000,
        100,
        "aves",
        assumption=True,
        source="suposição: uma carga de caminhão de frango vivo; não há dado "
        "aberto sobre tamanho de lote, e o modelo não simula ciclos de "
        "produção nem retira as aves do plantel",
    ),
    ParamSpec(
        "trace_window_days",
        "Janela de rastreamento e recolhimento",
        _MARKETS,
        0,
        90,
        1,
        "dias",
        source="Plano de Contingência MAPA IAAP/DNC, jun/2023, §7.1: destruição "
        "de carne, ovos e subprodutos de dois períodos de incubação (Código "
        "Terrestre da OMSA); Ficha Técnica IA: incubação de até 14 dias",
    ),
    ParamSpec("risk_yellow_threshold", "Limiar amarelo", _RISK, 0, 1, 0.01),
    ParamSpec("risk_red_threshold", "Limiar vermelho", _RISK, 0, 1, 0.01),
    ParamSpec(
        "stamping_out_radius_km",
        "Raio de sacrifício sanitário",
        _ECONOMY,
        1,
        15,
        1,
        "km",
    ),
    ParamSpec(
        "cull_cost_per_bird", "Custo por ave sacrificada", _ECONOMY, 0, 100, 1, "R$"
    ),
    ParamSpec(
        "quarantine_cost_per_day",
        "Custo diário de quarentena",
        _ECONOMY,
        0,
        5000,
        100,
        "R$",
    ),
    ParamSpec(
        "biosecurity_boost_amount", "Ganho de biosseguridade", _ECONOMY, 5, 50, 5, "%"
    ),
    ParamSpec(
        "biosecurity_investment_cost",
        "Investimento em biosseguridade",
        _ECONOMY,
        0,
        20_000,
        500,
        "R$",
    ),
    ParamSpec(
        "protocol_enabled",
        "Aplicar o protocolo oficial",
        _PROTOCOL,
        0,
        1,
        1,
        source=f"{_POP} (trânsito); {_PLAN} (foco, áreas, vigilância e "
        "repovoamento)",
    ),
    ParamSpec(
        "confirmation_days",
        "Confirmação laboratorial após a notificação",
        _PROTOCOL,
        0,
        14,
        1,
        "dias",
        source=f"{_INVESTIGATIONS}: mediana de 4 dias (quartis 3–6) entre o "
        "atendimento inicial e o diagnóstico conclusivo em 172 focos de IAAP "
        "(2 com datas inválidas excluídos; 165 dos 174 em aves silvestres); mede "
        "atendimento → diagnóstico, não notificação → confirmação",
    ),
    ParamSpec(
        "auto_depopulation",
        "Despovoamento do foco na confirmação",
        _PROTOCOL,
        0,
        1,
        1,
        source=f"{_PLAN}, §7.1, p. 14: 'Depopulação imediata de todas as aves "
        "existentes na unidade epidemiológica'",
    ),
    ParamSpec(
        "perifocus_radius_km",
        "Raio do perifoco",
        _PROTOCOL,
        0.5,
        10,
        0.5,
        "km",
        source=f"{_PLAN}, §7, p. 13: área perifocal de 3 km ao redor do foco; "
        "POP MAPA 2023, p. 3",
    ),
    ParamSpec(
        "surveillance_radius_km",
        "Raio externo da área de vigilância",
        _PROTOCOL,
        1,
        50,
        0.5,
        "km",
        source=f"{_PLAN}, §7, p. 13: 7 km a partir da área perifocal, "
        "'perfazendo um total de 10 km'",
    ),
    ParamSpec(
        "protection_radius_km",
        "Raio externo da área de proteção",
        _PROTOCOL,
        1.5,
        100,
        0.5,
        "km",
        source=f"{_PLAN}, §7, p. 13: 15 km a partir dos limites da área de "
        "vigilância, 'perfazendo um total de 25 km'",
    ),
    ParamSpec(
        "standstill_days",
        "Interrupção inicial do trânsito",
        _PROTOCOL,
        0,
        30,
        1,
        "dias",
        assumption=True,
        source="suposição: o POP manda interromper o trânsito 'em um momento "
        "inicial' (p. 2) sem dizer por quanto tempo; o Plano (§7.2–7.3) só "
        "condiciona as saídas à autorização do SVO",
    ),
    ParamSpec(
        "zone_disinfection_prob",
        "Eficácia da desinfecção do veículo nas zonas",
        _PROTOCOL,
        0,
        1,
        0.05,
        assumption=True,
        source="suposição: o POP exige limpeza e desinfecção a cada carregamento "
        "e entre áreas (p. 2 item 3; p. 8 §7) sem informar a eficácia",
    ),
    ParamSpec(
        "zone_release_days",
        "Liberação da área após o despovoamento",
        _PROTOCOL,
        0,
        90,
        1,
        "dias",
        assumption=True,
        source="suposição: o Plano mantém as medidas 'até a fase de conclusão do "
        "foco' (§7.2–7.3, p. 14–15) e encerra o foco sem mais evidência do "
        "agente (§14, p. 38), sem prazo; 28 dias = dois períodos de incubação "
        "de até 14 dias (Ficha Técnica IA), como na OMSA",
    ),
    ParamSpec(
        "perifocus_detection_days",
        "Intervalo das visitas de vigilância no perifoco",
        _PROTOCOL,
        1,
        30,
        1,
        "dias",
        source=f"{_PLAN}, §7.2, p. 14: investigação em todas as propriedades com "
        "aves 'a cada 3 dias' até a conclusão do foco",
    ),
    ParamSpec(
        "surveillance_detection_days",
        "Intervalo das visitas de vigilância na área de vigilância",
        _PROTOCOL,
        1,
        30,
        1,
        "dias",
        source=f"{_PLAN}, §7.3, p. 15: investigação em todas as propriedades com "
        "aves 'a cada 7 dias' até a conclusão do foco",
    ),
    ParamSpec(
        "fallow_days",
        "Vazio sanitário após o abate",
        _PROTOCOL,
        0,
        180,
        1,
        "dias",
        source=f"{_PLAN}, §10, p. 29: repovoamento só 'no mínimo, 30 dias depois "
        "dos procedimentos de desinfecção' (contados do abate no modelo)",
    ),
    ParamSpec(
        "restock_pct",
        "Repovoamento inicial",
        _PROTOCOL,
        1,
        100,
        1,
        "% da capacidade",
        source=f"{_PLAN}, §11, p. 33: repovoamento com, 'no máximo, 20% da "
        "capacidade de alojamento'",
    ),
    ParamSpec(
        "restock_monitoring_days",
        "Monitoramento antes do repovoamento total",
        _PROTOCOL,
        0,
        180,
        1,
        "dias",
        source=f"{_PLAN}, §11, p. 33: aves 'controladas durante 60 dias' e "
        "depois 'repovoamento total'",
    ),
    ParamSpec(
        "restock_inspection_days",
        "Intervalo das inspeções no repovoamento",
        _PROTOCOL,
        1,
        30,
        1,
        "dias",
        source=f"{_PLAN}, §11, p. 33: 'inspeções semanais do SVO'",
    ),
)


def override_params(
    params: ModelParams, overrides: Mapping[str, object]
) -> ModelParams:
    """A copy of `params` with validated `overrides` ({name: number}) applied.

    Raises ValueError for unknown names, a value of the wrong type (an int
    field given a float, or any bool), non-finite or out-of-bounds values, a
    yellow risk threshold above the red one, or emergency-area radii that are
    not strictly increasing (each ring must be nonempty). `params` is never
    modified.
    """
    specs = {spec.name: spec for spec in PARAM_SPECS}
    changes: dict[str, float] = {}
    for name, value in overrides.items():
        spec = specs.get(name)
        if spec is None:
            raise ValueError(f"Unknown parameter: {name}")
        if spec.type == "int":
            if type(value) is not int:
                raise ValueError(f"{name} must be an integer")
        elif type(value) is not int and not (
            type(value) is float and math.isfinite(value)
        ):
            raise ValueError(f"{name} must be a finite number")
        if not spec.min <= value <= spec.max:
            raise ValueError(f"{name} must be in [{spec.min}, {spec.max}]")
        # JSON clients send 3.0 as 3; float fields stay floats.
        changes[name] = value if spec.type == "int" else float(value)
    result = replace(params, **changes)
    if result.risk_yellow_threshold > result.risk_red_threshold:
        raise ValueError("risk_yellow_threshold must not exceed risk_red_threshold")
    if not (
        result.perifocus_radius_km
        < result.surveillance_radius_km
        < result.protection_radius_km
    ):
        raise ValueError(
            "perifocus_radius_km < surveillance_radius_km < protection_radius_km "
            "is required"
        )
    return result


@dataclass(frozen=True, slots=True)
class ParamPreset:
    """A named partial override of ``ModelParams`` offered by the interface."""

    id: str
    label: str
    description: str
    values: Mapping[str, float]


# Illustrative scenarios, not calibrated. Chosen from 120-day runs of seeds 1-10
# with the earlier demo population (120 farms of 5,000 birds around the demo
# poles, 2 hubs per fleet) on a grid-road test world and once on the
# Pernambuco world: "surto_sustentado" kept >= 5 infected farms at day 120 in
# 8/10 and 7/10 seeds, and in "cadeia_logistica" trucks caused most farm
# introductions. Rerun with the mortality suspicion rule (Ficha Técnica IA) on
# a 3 km road grid over the real municipalities: "surto_sustentado" kept >= 5
# infected farms at day 120 in 5/10 seeds (6/10 with the rule off); the rule
# made 2.3 of its 34.2 notifications per run, since the drawn detection day
# usually comes first. "protocolo_mapa" ended every outbreak before day 120
# (6.1 foci per run, the last infection on day 33.8 on average).
# With the official-data population (docs/FONTES_DADOS.md: 267 farms of about
# 215-256 thousand birds) on the Pernambuco world, "surto_sustentado" gave
# 4/10, and in "cadeia_logistica" trucks caused 1 of 18 introductions after the
# initial ones: the presets need recalibrating for that population.
_SUSTAINED_OUTBREAK = {
    "local_spread_prob": 25.0,
    "local_spread_radius_km": 6.0,
    "base_biosecurity": 40.0,
    "detection_time_max": 21,
    "infection_prob_truck_to_farm": 40.0,
}
PARAM_PRESETS: tuple[ParamPreset, ...] = (
    ParamPreset("padrao", "Padrão", "Valores padrão do modelo.", {}),
    ParamPreset(
        "surto_sustentado",
        "Surto sustentado",
        "Valores ilustrativos, não calibrados: transmissão vizinha mais forte e "
        "de maior alcance, biosseguridade menor e detecção espontânea mais "
        "lenta mantêm o surto ativo por meses em cerca de metade das sementes. "
        "A notificação por mortalidade de 10% em 72 h continua valendo, mas "
        "raramente vem antes.",
        _SUSTAINED_OUTBREAK,
    ),
    # The sustained outbreak with the protocol on, so its effect is visible.
    ParamPreset(
        "protocolo_mapa",
        "Protocolo oficial (MAPA)",
        "O surto sustentado com o protocolo oficial do MAPA (POP de trânsito, "
        "nov/2023, e Plano de Contingência IAAP/DNC, jun/2023): confirmação em 4 "
        "dias (mediana das investigações do MAPA), despovoamento imediato do "
        "foco, perifoco de 3 km, vigilância até 10 km e proteção até 25 km, "
        "visitas a cada 3 e 7 dias, vazio sanitário de 30 dias e repovoamento "
        "com 20% monitorado por 60 dias. A duração da interrupção inicial do "
        "trânsito, a eficácia da desinfecção e o prazo de liberação das áreas "
        "são suposições sinalizadas, a confirmar com a ADAGRO.",
        _SUSTAINED_OUTBREAK
        | {
            "protocol_enabled": 1,
            "confirmation_days": 4,
            "auto_depopulation": 1,
            "perifocus_radius_km": 3.0,
            "surveillance_radius_km": 10.0,
            "protection_radius_km": 25.0,
            "standstill_days": 7,
            "zone_disinfection_prob": 0.95,
            "zone_release_days": 28,
            "perifocus_detection_days": 3,
            "surveillance_detection_days": 7,
            "fallow_days": 30,
            "restock_pct": 20.0,
            "restock_monitoring_days": 60,
            "restock_inspection_days": 7,
        },
    ),
    ParamPreset(
        "cadeia_logistica",
        "Cadeia logística",
        "Valores ilustrativos, não calibrados: caminhões mais contaminantes, "
        "pouca lavagem e fômites mais duráveis fazem da frota a principal via "
        "de introdução nas granjas.",
        {
            "infection_prob_truck_to_farm": 60.0,
            "truck_disinfection_prob": 0.2,
            "fomite_survival_days": 7,
            "hub_transfer_fraction": 0.7,
            "local_spread_prob": 5.0,
            "infection_prob_bird_to_farm": 10.0,
            "detection_time_max": 14,
        },
    ),
)
