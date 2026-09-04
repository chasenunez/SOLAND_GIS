"""The two workflows from the concept diagram, as functions.

`prepare_tlm` is notebook 01: build TLM_total from three swissTLM3D layers.
`run_year` is notebook 02: combine TLM_total with one year of cantonal LN data.

Each step writes its intermediate layer, as the notebooks did, so a run can be
inspected or resumed. Each returns a dictionary of layers and reports, so a
notebook can plot them without the pipeline needing to know about matplotlib.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

from . import classify, geometry, io, validate
from .config import LOOKUP_NA_VALUES, TlmConfig, YearConfig

log = logging.getLogger(__name__)


def prepare_tlm(cfg: TlmConfig, write: bool = True) -> dict[str, object]:
    """Build TLM_total from Bodenbedeckung, Nutzungsareal and Siedlungsname.

    Follows the diagram: classify each source, merge land cover with orchards and
    vineyards, erase settlements by that merge, then merge the erased settlements
    back in.

    Worth knowing why the erase step is there. TLM Bodenbedeckung does not cover
    the whole country: it maps forest, rock, water, glaciers, wetlands and loose
    rock, leaving built-up and farmed land unmapped. The settlement polygons fill
    those holes, and are erased first so they do not overlap the land cover they
    sit beside. If a future TLM release extends Bodenbedeckung to cover
    settlements, this step would empty the settlement layer, which is why the
    result is checked for emptiness rather than assumed.
    """
    log.info("preparing TLM_total from %s", cfg.tlm_gpkg.name)

    nutz = io.read_vector(cfg.tlm_gpkg, cfg.nutzungsareal_layer, cfg.crs, "TLM Nutzungsareal")
    siedl = io.read_vector(cfg.tlm_gpkg, cfg.siedlungsname_layer, cfg.crs, "TLM Siedlungsname")
    bb = io.read_vector(cfg.tlm_gpkg, cfg.bodenbedeckung_layer, cfg.crs, "TLM Bodenbedeckung")

    io.require_columns(nutz, ["objektart"], "TLM Nutzungsareal")
    io.require_columns(siedl, ["objektart"], "TLM Siedlungsname")
    io.require_columns(bb, ["objektart"], "TLM Bodenbedeckung")

    lookup = io.read_lookup_csv(cfg.tlm_lookup_table, LOOKUP_NA_VALUES)

    tlm_reben = classify.classify_nutzungsareal(nutz)
    tlm_siedl = classify.classify_siedlung(siedl)
    tlm_bb = classify.classify_bodenbedeckung(bb, lookup)

    obst_reben = geometry.merge_layers([tlm_reben, tlm_bb], cfg.crs, "TLM_ObstReben")
    siedl_erase = geometry.erase(tlm_siedl, obst_reben, label="erase settlements")

    if siedl_erase.empty:
        raise ValueError(
            "Erasing settlements by land cover left nothing. This workflow assumes "
            "TLM Bodenbedeckung leaves gaps over built-up land that the settlement "
            "polygons fill. Check whether the Bodenbedeckung layer now covers "
            "settlements too, which would make this step redundant."
        )

    tlm_total = geometry.merge_layers([siedl_erase, obst_reben], cfg.crs, "TLM_total")
    tlm_total = classify.drop_join_artefacts(tlm_total)

    layers = {
        "TLM_Reben": tlm_reben,
        "TLM_Siedl": tlm_siedl,
        "TLM_BB": tlm_bb,
        "TLM_ObstReben": obst_reben,
        "Siedl_erase": siedl_erase,
        "TLM_total": tlm_total,
    }

    validate.assert_crs(layers, cfg.crs)
    areas = validate.area_reconciliation(layers)
    completeness = validate.classification_completeness(tlm_total, ("class", "class_code", "group_code"))
    by_class = validate.summarise_by_class(tlm_total)

    if write:
        for name, layer in layers.items():
            io.write_layer(layer, cfg.output_gpkg, name)
        io.write_manifest(
            cfg.output_gpkg.with_suffix(".manifest.json"),
            {"tlm_gpkg": cfg.tlm_gpkg, "tlm_lookup_table": cfg.tlm_lookup_table},
            {"crs": cfg.crs, "layers": ", ".join(layers)},
        )

    return {"layers": layers, "areas": areas, "completeness": completeness, "by_class": by_class}


def run_year(cfg: YearConfig, write: bool = True) -> dict[str, object]:
    """Combine TLM_total with one year of LN data.

    Follows the diagram: merge the LN version inputs, join the lookup, remove
    identical geometries, erase TLM_total with LN, select the non-overlaying LN
    features, and merge the two.

    Two behaviours differ from the original code and both are deliberate. The
    selection is on group code 99 rather than class code 6, per the corrected
    concept diagram. And the layers are harmonised onto one classification schema
    before the merge, so the output has a single set of columns.
    """
    log.info("running year %d", cfg.year)

    ln_parts = [
        io.read_vector(path, crs=cfg.crs, label=f"LN input {index + 1}")
        for index, path in enumerate(cfg.ln_inputs)
    ]
    ln_merged = geometry.merge_layers(ln_parts, cfg.crs, f"Nutzungsflaechen_{cfg.year}")

    lookup = io.read_lookup_csv(cfg.lookup_table, LOOKUP_NA_VALUES)
    overlap_flag_report = classify.check_overlap_flag_consistency(lookup, cfg.overlap_group_code)

    ln_joined = classify.join_ln_lookup(
        ln_merged, lookup, cfg.ln_join_field, cfg.lookup_join_field, cfg.lookup_fields
    )
    validity_report = classify.check_code_validity(ln_joined, cfg.year, cfg.ln_join_field)

    # Priority on the join field makes the survivor of a duplicate geometry a
    # stated rule rather than an accident of file order.
    ln_clean = geometry.remove_identical_geometries(ln_joined, priority_column=cfg.ln_join_field)

    overlaps = geometry.find_overlaps(ln_clean, cfg.ln_join_field) if cfg.create_overlap_layer else None

    tlm_total = io.read_vector(cfg.tlm_total_gpkg, cfg.tlm_total_layer, cfg.crs, "TLM_total")

    ln_kept, ln_dropped = geometry.select_by_group(ln_clean, cfg.overlap_group_code)

    eraser = ln_kept if cfg.erase_with_selected_only else ln_clean
    log.info(
        "erasing TLM_total with %s LN features",
        "the selected" if cfg.erase_with_selected_only else "all",
    )
    tlm_erased = geometry.erase(
        tlm_total, eraser, min_area_m2=cfg.min_polygon_area_m2, label=f"erase TLM {cfg.year}"
    )

    if cfg.compute_gap_report:
        gaps = validate.gap_report(tlm_erased, ln_kept, ln_dropped)
    else:
        log.info("gap report skipped because compute_gap_report is False")
        gaps = pd.DataFrame(columns=[
            "dropped_features", "dropped_area_km2", "gap_area_km2", "gap_share_of_dropped_pct",
        ])

    ln_final = classify.harmonise(ln_kept, source=f"LN_{cfg.year}")
    tlm_final = classify.ensure_target_fields(tlm_erased)
    if "source" not in tlm_final.columns or tlm_final["source"].isna().all():
        tlm_final["source"] = "TLM_total"

    final = geometry.merge_layers([ln_final, tlm_final], cfg.crs, f"TLM_LN_{cfg.year}")
    final = classify.drop_join_artefacts(final)

    layers = {
        f"Nutzungsflaechen_{cfg.year}": ln_merged,
        f"LN_{cfg.year}": ln_clean,
        f"LN_{cfg.year}_sel": ln_kept,
        f"LN_{cfg.year}_overlay_dropped": ln_dropped,
        f"TLM_erased_{cfg.year}": tlm_erased,
        f"TLM_LN_{cfg.year}": final,
    }
    if overlaps is not None:
        layers[f"LN_{cfg.year}_overlaps"] = overlaps

    validate.assert_crs(layers, cfg.crs)
    areas = validate.area_reconciliation(layers)
    completeness = validate.classification_completeness(final, ("class", "class_code", "group_code"))
    by_class = validate.summarise_by_class(final)

    reports = {
        "areas": areas,
        "completeness": completeness,
        "by_class": by_class,
        "gaps": gaps,
        "code_validity": validity_report,
        "overlap_flag_mismatch": overlap_flag_report,
    }

    if write:
        for name, layer in layers.items():
            io.write_layer(layer, cfg.output_gpkg, name)
        _write_reports(cfg, reports)
        io.write_manifest(
            cfg.output_gpkg.with_suffix(".manifest.json"),
            {
                **{f"ln_input_{i + 1}": path for i, path in enumerate(cfg.ln_inputs)},
                "lookup_table": cfg.lookup_table,
                "tlm_total": cfg.tlm_total_gpkg,
            },
            {
                "year": cfg.year,
                "crs": cfg.crs,
                "overlap_group_code": cfg.overlap_group_code,
                "erase_with_selected_only": cfg.erase_with_selected_only,
                "min_polygon_area_m2": cfg.min_polygon_area_m2,
            },
        )

    return {"layers": layers, **reports}


def _write_reports(cfg: YearConfig, reports: dict[str, pd.DataFrame]) -> None:
    """Write the validation reports beside the output, as CSV.

    Next to the data rather than only in the log, because the log scrolls past
    and the question "was anything odd about the 2023 run?" gets asked later.
    """
    directory = cfg.output_gpkg.parent / f"reports_{cfg.year}"
    directory.mkdir(parents=True, exist_ok=True)
    for name, table in reports.items():
        if isinstance(table, pd.DataFrame):
            table.to_csv(directory / f"{name}.csv", index=False, encoding="utf-8")
    log.info("wrote validation reports to %s", directory)
