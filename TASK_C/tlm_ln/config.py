"""Configuration, read from a TOML file rather than hardcoded in a notebook.

Why this exists: the original notebooks carried Windows `Q:` paths inside a
frozen dataclass, so running them anywhere else meant editing code. Paths belong
in a config file, code belongs in modules, and the two should not be tangled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 and earlier
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

# CH1903+ / LV95. Everything in this workflow is in this projection, and the
# readers assert it rather than assume it.
DEFAULT_CRS = "EPSG:2056"

# Group code marking LN polygons that overlay other LN polygons. In the Feb 2026
# lookup these are Hochstamm-Feldobstbäume (921), Nussbäume (922),
# Edelkastanienbäume (923) and Einheimische Einzelbäume und Alleen (924), all
# labelled "Ueberlagernde Flaechen" / "Overlap_area".
#
# This replaces the earlier rule of dropping everything with class code 6. Class
# 6 is "Flaechen ausserhalb der LN", which also contains real, non-overlapping
# surfaces: ponds and ditches (904), ruderal areas (905), dry stone walls (906),
# unpaved paths (907), region-specific biodiversity areas (908) and home gardens
# (909). Dropping those removed genuine ground cover from the result.
OVERLAP_GROUP_CODE = 99

# Sentinels used in the LN lookup CSV. There are three, in the same file, which
# is why they are listed here rather than left to pandas defaults.
LOOKUP_NA_VALUES = ("<Null>", "NULL", "<zero>", "")


@dataclass(frozen=True)
class TlmConfig:
    """Inputs and outputs for preparing TLM_total (notebook 01)."""

    tlm_gpkg: Path
    tlm_lookup_table: Path
    output_gpkg: Path
    crs: str = DEFAULT_CRS
    nutzungsareal_layer: str = "tlm_areale_nutzungsareal"
    siedlungsname_layer: str = "tlm_namen_siedlungsname"
    bodenbedeckung_layer: str = "tlm_bb_bodenbedeckung"


@dataclass(frozen=True)
class YearConfig:
    """Inputs and outputs for one yearly LN/TLM run (notebook 02)."""

    year: int
    ln_inputs: tuple[Path, ...]
    lookup_table: Path
    tlm_total_gpkg: Path
    output_gpkg: Path
    crs: str = DEFAULT_CRS
    tlm_total_layer: str = "TLM_total"
    ln_join_field: str = "lnf_code"
    lookup_join_field: str = "ID"
    overlap_group_code: int = OVERLAP_GROUP_CODE

    # Erase TLM_total with only the LN features that are kept, rather than with
    # every LN feature. The concept diagram shows the latter, so False is the
    # default and matches existing results. Setting this True closes the gaps
    # that overlay features would otherwise leave behind. The pipeline reports
    # the area difference either way, so the choice can be made on a number.
    erase_with_selected_only: bool = False

    # Polygons smaller than this are dropped after an overlay. Erase operations
    # on national-scale data produce slivers along shared edges that are
    # artefacts of floating point, not features of the landscape.
    min_polygon_area_m2: float = 1.0

    # The pairwise overlap layer is for documentation and statistics. It is the
    # slowest step by a wide margin, so it is opt-in.
    create_overlap_layer: bool = False

    # Measure how much area the selection step leaves uncovered. On by default,
    # because it is the number that decides `erase_with_selected_only`, and an
    # unmeasured gap is the problem this whole correction was about. It costs one
    # spatial join and one difference over the overlay features alone, so it is
    # far cheaper than the erase itself; turn it off only for a quick run.
    compute_gap_report: bool = True

    lookup_fields: tuple[str, ...] = (
        "Class", "Class_Code", "Group_Code", "Group_de", "Group_en",
        "Class_en", "BFF_QI", "Pest_Group", "Pest_Code",
        "ueberlagernd", "Gueltig_Von", "Gueltig_Bis",
    )
    extra: dict = field(default_factory=dict)


def _require_reader() -> None:
    if tomllib is None:
        raise SystemExit(
            "No TOML reader available. Use Python 3.11 or later, "
            "or install the backport with: pip install tomli"
        )


def _resolve(base: Path, value: str) -> Path:
    """Resolve a config path relative to the config file unless it is absolute.

    Lets the same config work from a notebook, a script, or a scheduled job
    without anyone worrying about the current working directory.
    """
    path = Path(value)
    return path if path.is_absolute() else (base / path)


def load(path: Path | str) -> tuple[TlmConfig, dict]:
    """Read the TLM section of a config file.

    Returns the config plus the raw dictionary, so a caller can reach settings
    that have not been promoted to dataclass fields.
    """
    _require_reader()
    path = Path(path)
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    base = path.parent
    tlm = raw["tlm"]
    return (
        TlmConfig(
            tlm_gpkg=_resolve(base, tlm["tlm_gpkg"]),
            tlm_lookup_table=_resolve(base, tlm["tlm_lookup_table"]),
            output_gpkg=_resolve(base, tlm["output_gpkg"]),
            crs=raw.get("crs", DEFAULT_CRS),
            nutzungsareal_layer=tlm.get("nutzungsareal_layer", "tlm_areale_nutzungsareal"),
            siedlungsname_layer=tlm.get("siedlungsname_layer", "tlm_namen_siedlungsname"),
            bodenbedeckung_layer=tlm.get("bodenbedeckung_layer", "tlm_bb_bodenbedeckung"),
        ),
        raw,
    )


def load_year(path: Path | str, year: int) -> YearConfig:
    """Read the yearly section of a config file for one year.

    The year is a parameter rather than a config value so that running several
    years is a loop, not seven edited copies of the same file.
    """
    _require_reader()
    path = Path(path)
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    base = path.parent
    section = raw["year"]
    per_year = raw.get("years", {}).get(str(year), {})

    inputs = per_year.get("ln_inputs") or section.get("ln_inputs")
    if not inputs:
        raise SystemExit(f"No ln_inputs configured for {year} in {path}")

    template = section["output_gpkg"]
    return YearConfig(
        year=year,
        ln_inputs=tuple(_resolve(base, item) for item in inputs),
        lookup_table=_resolve(base, per_year.get("lookup_table", section["lookup_table"])),
        tlm_total_gpkg=_resolve(base, section["tlm_total_gpkg"]),
        output_gpkg=_resolve(base, template.format(year=year)),
        crs=raw.get("crs", DEFAULT_CRS),
        tlm_total_layer=section.get("tlm_total_layer", "TLM_total"),
        overlap_group_code=int(section.get("overlap_group_code", OVERLAP_GROUP_CODE)),
        erase_with_selected_only=bool(section.get("erase_with_selected_only", False)),
        min_polygon_area_m2=float(section.get("min_polygon_area_m2", 1.0)),
        create_overlap_layer=bool(section.get("create_overlap_layer", False)),
        compute_gap_report=bool(section.get("compute_gap_report", True)),
    )
