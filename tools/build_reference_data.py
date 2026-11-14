"""Build the per-municipality poultry reference table from official raw files.

Writes ``src/simgeope/simulation/reference/producao_municipios_pe.csv`` (one
row per Pernambuco municipality, aggregate counts only) from:

* IBGE PPM 2024, table 3939, as returned by the IBGE aggregates API v3:
  https://servicodados.ibge.gov.br/api/v3/agregados/3939/periodos/2024/variaveis/105?localidades=N6[N3[26]]&classificacao=79[32796,32793]
* IBGE SIDRA table 74 (egg production, thousand dozen), CSV export with
  every municipality and year, used for the 2023 hen-egg column;
* the ADAGRO list of registered poultry farms (February 2022) as a PDF, read
  with ``pdftotext -layout`` (poppler-utils). The list names the owners: only
  the number of registry entries per municipality is kept.

See docs/FONTES_DADOS.md for what each column means and its limitations.
Run from the repository root:

    python tools/build_reference_data.py PPM.json tabela74.csv ADAGRO.pdf
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from pathlib import Path

OUTPUT = Path("src/simgeope/simulation/reference/producao_municipios_pe.csv")
PE_STATE_CODE = "26"
PPM_YEAR = "2024"
EGG_YEAR = "2023"
# IBGE table 3939, classification 79 (tipo de rebanho).
PPM_CHICKENS_TOTAL = "32796"  # Galináceos - total
PPM_HENS = "32793"  # Galináceos - galinhas
EGG_PRODUCT = "Ovos de galinha"
# Every ADAGRO entry is one line starting with this label; its 4-digit
# registry number closes the entry, possibly on a later line.
ADAGRO_ENTRY = re.compile(r"^GRANJA AVÍCOLA {2,}(\S.*)$")
ADAGRO_REGISTRY = re.compile(r"\b\d{4}\s*$")
# ADAGRO spellings of municipalities that IBGE names differently.
ADAGRO_ALIASES = {"SAO CAETANO": "São Caitano"}
COLUMNS = (
    "codigo_ibge",
    "municipio",
    "galinaceos_2024",
    "galinhas_2024",
    "corte_proxy_2024",
    "ovos_galinha_mil_duzias_2023",
    "granjas_adagro_2022",
)


def normalized(name: str) -> str:
    """Upper case without accents or repeated spaces, as in the ADAGRO list."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.upper().split())


def ibge_count(value: str) -> int | None:
    """An IBGE cell as an integer: "-" is a true zero; "...", "X" are unknown."""
    if value.isdigit():
        return int(value)
    if value == "-":
        return 0
    return None


def read_ppm(path: Path) -> tuple[dict[str, str], dict[str, dict[str, int | None]]]:
    """Municipality names and, per category, the 2024 head count by code."""
    names: dict[str, str] = {}
    counts: dict[str, dict[str, int | None]] = {}
    (variable,) = json.loads(path.read_text(encoding="utf-8"))
    for result in variable["resultados"]:
        (classification,) = result["classificacoes"]
        (category,) = classification["categoria"]
        series = counts.setdefault(category, {})
        for row in result["series"]:
            place = row["localidade"]
            names[place["id"]] = place["nome"].removesuffix(" - PE")
            series[place["id"]] = ibge_count(row["serie"][PPM_YEAR])
    missing = {PPM_CHICKENS_TOTAL, PPM_HENS} - set(counts)
    if missing:
        raise ValueError(f"PPM file lacks categories {sorted(missing)}")
    return names, counts


def read_eggs(path: Path) -> dict[str, int | None]:
    """2023 hen-egg production (thousand dozen) per PE municipality code."""
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    years = next(r for r in rows if EGG_YEAR in r)
    products = rows[rows.index(years) + 1]
    start = years.index(EGG_YEAR)
    column = start + products[start : start + 3].index(EGG_PRODUCT)
    return {
        row[1]: ibge_count(row[column])
        for row in rows
        if row[:1] == ["MU"] and row[1].startswith(PE_STATE_CODE)
    }


def read_adagro(path: Path, names: dict[str, str]) -> tuple[Counter, list[str]]:
    """Registry entries per municipality code, and fields that match none.

    pdftotext sometimes runs the owner's name into the municipality column
    (e.g. "CHA DE ALEGRIA <NAME> ..."), so the municipality is the longest
    known name that prefixes the column text. Prints how many entries needed
    that prefix match.
    """
    text = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    by_name = {normalized(name): code for code, name in names.items()}
    for alias, official in ADAGRO_ALIASES.items():
        by_name[alias] = by_name[normalized(official)]
    longest_first = sorted(by_name, key=len, reverse=True)
    counts: Counter = Counter()
    unmatched: list[str] = []
    prefixed = 0
    field: str | None = None
    for line in text.splitlines():
        entry = ADAGRO_ENTRY.match(line)
        if entry:
            if field is not None:
                raise ValueError("ADAGRO entry without a registry number")
            field = normalized(re.split(r"\s{2,}", entry.group(1))[0])
        if field is not None and ADAGRO_REGISTRY.search(line):
            if field in by_name:
                counts[by_name[field]] += 1
            else:
                match = next((n for n in longest_first if field.startswith(n)), None)
                if match is None:
                    unmatched.append(field)
                else:
                    counts[by_name[match]] += 1
                    prefixed += 1
            field = None
    if field is not None:
        raise ValueError("Last ADAGRO entry has no registry number")
    print(f"ADAGRO: {sum(counts.values()) + len(unmatched)} registros", end="")
    print(f" ({prefixed} casados por prefixo do nome do município)")
    return counts, unmatched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ppm_json", type=Path, help="IBGE API v3 JSON, table 3939")
    parser.add_argument("tabela74_csv", type=Path, help="SIDRA table 74 CSV")
    parser.add_argument("adagro_pdf", type=Path, help="ADAGRO registered farms PDF")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)

    names, ppm = read_ppm(args.ppm_json)
    eggs = read_eggs(args.tabela74_csv)
    farms, unmatched = read_adagro(args.adagro_pdf, names)

    rows = []
    for code in sorted(names):
        total = ppm[PPM_CHICKENS_TOTAL].get(code)
        hens = ppm[PPM_HENS].get(code)
        if total is None or hens is None:
            raise ValueError(f"PPM {PPM_YEAR} has no head count for {code}")
        rows.append(
            {
                "codigo_ibge": code,
                "municipio": names[code],
                "galinaceos_2024": total,
                "galinhas_2024": hens,
                "corte_proxy_2024": total - hens,
                # Empty when IBGE does not publish it ("..." or "X").
                "ovos_galinha_mil_duzias_2023": eggs.get(code),
                "granjas_adagro_2022": farms[code],
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    print(f"{len(rows)} municípios gravados em {args.output}")
    for column in COLUMNS[2:]:
        values = [row[column] for row in rows]
        known = [v for v in values if v is not None]
        print(f"  {column}: total {sum(known)}, sem valor {len(values) - len(known)}")
    if unmatched:
        print(f"ADAGRO sem município reconhecido ({len(unmatched)}):", file=sys.stderr)
        for field in sorted(set(unmatched)):
            print(f"  {field}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
