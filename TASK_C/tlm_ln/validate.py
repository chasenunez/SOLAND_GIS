"""Checks that run between processing steps.

None of these change the data. They exist because the original workflow produced
a result with no way to tell whether it was right, and the steps are slow enough
that discovering a problem by eye afterwards is expensive.

Everything returns a DataFrame so it can be written next to the output and read
later, rather than only appearing in a log that scrolls past.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

log = logging.getLogger(__name__)


def area_reconciliation(parts: dict[str, gpd.GeoDataFrame]) -> pd.DataFrame:
    """Summarise feature counts and areas across the layers of a run.

    Read the erase steps as arithmetic: the area removed from TLM_total should
    equal the area of the LN layer used to erase it, minus whatever part of that
    LN layer fell outside TLM_total to begin with. A large unexplained difference
    means a CRS problem, an invalid geometry that survived repair, or an eraser
    layer that was not what you thought it was.
    """
    rows = []
    for name, layer in parts.items():
        if layer is None:
            continue
        area_km2 = layer.geometry.area.sum() / 1e6 if len(layer) else 0.0
        rows.append({
            "layer": name,
            "features": len(layer),
            "area_km2": round(area_km2, 3),
            "geometry_types": ", ".join(sorted(layer.geom_type.unique())) if len(layer) else "",
        })

    report = pd.DataFrame(rows, columns=["layer", "features", "area_km2", "geometry_types"])
    for row in report.itertuples():
        log.info("%-28s %8d features  %12.3f km2", row.layer, row.features, row.area_km2)
    return report


def gap_report(
    tlm_erased: gpd.GeoDataFrame,
    ln_kept: gpd.GeoDataFrame,
    ln_dropped: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Quantify the area left uncovered by the selection step.

    When TLM_total is erased with the full LN layer but only part of that layer
    is merged back, the difference becomes a hole. This measures how much.

    Overlaying features (group 99) sit on top of other LN polygons by definition,
    so most of the dropped area is covered by something that stays. The part that
    is not covered is the real gap, and isolated trees and avenues are the likely
    source: an avenue along a road need not lie within any other parcel.

    The number this produces is what the `erase_with_selected_only` setting is
    for. If it is negligible, leave the setting alone and match the concept
    diagram. If it is not, the setting closes the gap.
    """
    if ln_dropped.empty:
        return pd.DataFrame([{
            "dropped_features": 0, "dropped_area_km2": 0.0,
            "gap_area_km2": 0.0, "gap_share_of_dropped_pct": 0.0,
        }])

    dropped_area = ln_dropped.geometry.area.sum()
    gap_area = _uncovered_area(ln_dropped, ln_kept, tlm_erased)

    report = pd.DataFrame([{
        "dropped_features": len(ln_dropped),
        "dropped_area_km2": round(dropped_area / 1e6, 3),
        "gap_area_km2": round(gap_area / 1e6, 3),
        "gap_share_of_dropped_pct": round(gap_area / dropped_area * 100, 2) if dropped_area else 0.0,
    }])

    log.info(
        "selection leaves %.3f km2 uncovered, %.2f%% of the %.3f km2 dropped",
        gap_area / 1e6,
        report["gap_share_of_dropped_pct"].iloc[0],
        dropped_area / 1e6,
    )
    if gap_area / 1e6 > 1.0:
        log.warning(
            "More than 1 km2 is left uncovered by the selection step. Consider setting "
            "erase_with_selected_only = true in the config, which erases TLM with only "
            "the LN features that are kept and so closes these gaps."
        )
    return report


def _uncovered_area(
    dropped: gpd.GeoDataFrame,
    ln_kept: gpd.GeoDataFrame,
    tlm_erased: gpd.GeoDataFrame,
) -> float:
    """Area of `dropped` that nothing in the output covers.

    Written to touch as little data as possible, because at national scale the
    obvious version is expensive. Two things keep it cheap.

    The dropped set is already small: group 99 holds four codes, all of them
    trees. So the work scales with the overlay features rather than with the
    whole country.

    And only the covering features that actually touch one of them can matter, so
    a spatial join narrows the second side before any geometry is differenced.
    Without that, `gpd.overlay` would build its index over every kept LN parcel
    and every surviving TLM polygon, which is most of Switzerland, to answer a
    question about a few thousand trees.
    """
    geometry_column = dropped.geometry.name
    frames = [layer for layer in (ln_kept, tlm_erased) if layer is not None and len(layer)]
    if not frames:
        return float(dropped.geometry.area.sum())

    # Reduce each layer to its geometry under a single common column name, so
    # they can be concatenated. `rename_geometry` refuses a no-op rename, hence
    # the check rather than an unconditional call.
    pieces = []
    for layer in frames:
        piece = layer[[layer.geometry.name]]
        if layer.geometry.name != geometry_column:
            piece = piece.rename_geometry(geometry_column)
        pieces.append(piece)

    covers = gpd.GeoDataFrame(
        pd.concat(pieces, ignore_index=True), geometry=geometry_column, crs=dropped.crs
    )

    # Narrow to the covering features that touch a dropped one at all.
    candidates = gpd.sjoin(
        covers, dropped[[geometry_column]], how="inner", predicate="intersects"
    )
    if candidates.empty:
        return float(dropped.geometry.area.sum())

    relevant = covers.loc[candidates.index.unique()]
    uncovered = gpd.overlay(
        dropped[[geometry_column]], relevant, how="difference", keep_geom_type=True
    )
    return float(uncovered.geometry.area.sum()) if len(uncovered) else 0.0


def classification_completeness(gdf: gpd.GeoDataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    """Report how much of the output carries no classification.

    A feature with no class code is invisible to any downstream selection, so it
    is effectively missing even though it is present in the file. Worth knowing
    as a number before the layer is handed to anyone.
    """
    rows = []
    total_area = gdf.geometry.area.sum() if len(gdf) else 0.0
    for column in columns:
        if column not in gdf.columns:
            rows.append({"column": column, "missing_features": len(gdf),
                         "missing_pct": 100.0, "missing_area_km2": round(total_area / 1e6, 3),
                         "note": "column absent"})
            continue
        missing = gdf[column].isna()
        rows.append({
            "column": column,
            "missing_features": int(missing.sum()),
            "missing_pct": round(missing.mean() * 100, 2) if len(gdf) else 0.0,
            "missing_area_km2": round(gdf.loc[missing].geometry.area.sum() / 1e6, 3) if missing.any() else 0.0,
            "note": "",
        })

    report = pd.DataFrame(rows)
    worst = report.loc[report["missing_pct"] > 0]
    if len(worst):
        log.warning("unclassified features remain: %s",
                    worst[["column", "missing_features", "missing_pct"]].to_dict("records"))
    return report


def assert_crs(layers: dict[str, gpd.GeoDataFrame], expected: str) -> None:
    """Fail if any layer is not in the expected projection.

    Cheap, and it catches the class of error that produces plausible-looking
    output displaced by hundreds of metres. Worth doing before any overlay, since
    an overlay between mismatched projections returns an empty result rather than
    an error.
    """
    wrong = {
        name: str(layer.crs)
        for name, layer in layers.items()
        if layer is not None and len(layer) and str(layer.crs) != expected
    }
    if wrong:
        raise ValueError(f"layers are not in {expected}: {wrong}")


def summarise_by_class(gdf: gpd.GeoDataFrame, class_column: str = "class_code") -> pd.DataFrame:
    """Area and feature count per class, for the documentation.

    The table most likely to be pasted into a report, and the quickest way to
    spot that a class has vanished or doubled between years.
    """
    if class_column not in gdf.columns or gdf.empty:
        return pd.DataFrame(columns=[class_column, "features", "area_km2", "area_pct"])

    working = gdf.copy()
    working["_area"] = working.geometry.area
    grouped = working.groupby(class_column, dropna=False).agg(
        features=("_area", "size"), area_m2=("_area", "sum")
    ).reset_index()

    total = grouped["area_m2"].sum()
    grouped["area_km2"] = (grouped["area_m2"] / 1e6).round(3)
    grouped["area_pct"] = (grouped["area_m2"] / total * 100).round(2) if total else 0.0
    return grouped.drop(columns="area_m2").sort_values("area_km2", ascending=False)
