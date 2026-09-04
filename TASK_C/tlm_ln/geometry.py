"""Geometry operations: erase, merge, de-duplicate, find overlaps.

The originals worked but had two problems at national scale. The overlap counter
looped over every polygon in Python, which on several hundred thousand LN
features is hours rather than minutes. And duplicate removal kept whichever row
happened to come first, which depends on the order the input files were
concatenated, so the same inputs in a different order gave a different answer.

Both are fixed here. The erase and merge behaviour is unchanged except for
sliver removal, which is documented where it happens.
"""

from __future__ import annotations

import logging
import warnings

import geopandas as gpd
import pandas as pd

from .io import clean_geometries

log = logging.getLogger(__name__)

POLYGON_TYPES = ("Polygon", "MultiPolygon")


def merge_layers(layers: list[gpd.GeoDataFrame], crs: str, label: str = "merged") -> gpd.GeoDataFrame:
    """Concatenate layers into one, keeping the union of their columns."""
    frames = [layer for layer in layers if len(layer)]
    if not frames:
        raise ValueError(f"{label}: nothing to merge, all inputs are empty")

    # pandas warns when concatenating frames where a column is all-NA in one of
    # them, because a future version will decide dtypes differently. That is
    # exactly what happens here by design: `ensure_target_fields` gives both
    # sides the same schema, and the TLM side has no pest_code while the LN side
    # has no objektart. The values are unaffected either way, so the warning is
    # suppressed narrowly rather than left to clutter every notebook run.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*DataFrame concatenation with empty or all-NA entries.*",
            category=FutureWarning,
        )
        merged = pd.concat(frames, ignore_index=True, sort=False)
    merged = gpd.GeoDataFrame(merged, geometry="geometry", crs=crs)
    log.info("%s: %d features from %d layers", label, len(merged), len(frames))
    return clean_geometries(merged, label)


def remove_identical_geometries(
    gdf: gpd.GeoDataFrame,
    priority_column: str | None = None,
    grid_size: float | None = None,
) -> gpd.GeoDataFrame:
    """Remove rows whose geometry is exactly the same as an earlier row's.

    Two changes from the original.

    Deterministic ordering. The original kept the first occurrence in file order,
    so merging the v2 and v3 LN inputs in a different order produced a different
    surviving row. Here the frame is sorted by `priority_column` first, which
    makes the choice explicit and repeatable. Without a priority column, rows are
    sorted by their existing index so at least the same input gives the same
    output.

    Optional coordinate snapping. Geometries that are identical in the field can
    differ in their last decimal place after passing through two file formats.
    `grid_size` snaps coordinates before comparison. It defaults to off, because
    snapping is a real change to the data and should be a deliberate choice.

    A note on what this does not solve: the concept diagram footnotes that some
    fields are cropped several times a year, which is why identical geometries
    appear with different codes. Keeping one of them discards that information
    rather than representing it. If multi-cropping matters to a downstream user,
    it needs a data model that can hold more than one use per polygon, not a
    de-duplication rule.
    """
    if gdf.empty:
        return gdf.copy()

    working = gdf.copy()
    if priority_column and priority_column in working.columns:
        working = working.sort_values(priority_column, kind="stable")
        log.info("de-duplicating with priority on %s", priority_column)
    else:
        if priority_column:
            log.warning("priority column %r not found; falling back to input order", priority_column)
        working = working.sort_index(kind="stable")

    geometry = working.geometry.normalize()
    if grid_size is not None:
        from shapely import set_precision

        geometry = gpd.GeoSeries(set_precision(geometry.values, grid_size), crs=working.crs)
        log.info("snapping coordinates to a %s m grid before comparison", grid_size)

    duplicated = geometry.to_wkb(hex=True).duplicated(keep="first")
    if duplicated.any():
        log.info("removed %d exact duplicate geometries of %d", int(duplicated.sum()), len(working))

    return working.loc[~duplicated].sort_index(kind="stable").copy()


def find_overlaps(gdf: gpd.GeoDataFrame, id_column: str | None = None) -> gpd.GeoDataFrame:
    """Return the pairwise intersections between features in one layer.

    Replaces the original nested Python loop with a spatial self-join, which
    pushes the candidate search into the index and the intersection into
    vectorised shapely. On national-scale LN data this changes the cost from
    hundreds of thousands of Python-level iterations to one indexed join.

    Only polygonal intersections are returned. Two polygons that merely touch
    produce a line or a point of zero area, which is adjacency rather than
    overlap and would otherwise inflate the count.
    """
    if gdf.empty:
        return gpd.GeoDataFrame(
            columns=["left_id", "right_id", "overlap_area_m2", "geometry"],
            geometry="geometry", crs=gdf.crs,
        )

    working = gdf.reset_index(drop=True).copy()
    working["_row"] = working.index
    if id_column and id_column in working.columns:
        working["_id"] = working[id_column]
    else:
        working["_id"] = working["_row"]

    pairs = gpd.sjoin(
        working[["_row", "_id", "geometry"]],
        working[["_row", "_id", "geometry"]],
        how="inner", predicate="intersects",
    )

    # Keep each unordered pair once, and drop self-matches.
    pairs = pairs.loc[pairs["_row_left"] < pairs["_row_right"]]
    if pairs.empty:
        return gpd.GeoDataFrame(
            columns=["left_id", "right_id", "overlap_area_m2", "geometry"],
            geometry="geometry", crs=gdf.crs,
        )

    left = working.geometry.iloc[pairs["_row_left"].to_numpy()].reset_index(drop=True)
    right = working.geometry.iloc[pairs["_row_right"].to_numpy()].reset_index(drop=True)
    intersections = left.intersection(right, align=False)

    result = gpd.GeoDataFrame(
        {
            "left_id": pairs["_id_left"].to_numpy(),
            "right_id": pairs["_id_right"].to_numpy(),
            "geometry": intersections.values,
        },
        geometry="geometry", crs=gdf.crs,
    )

    result = result.loc[result.geometry.notna() & ~result.geometry.is_empty]
    result = result.loc[result.geom_type.isin(POLYGON_TYPES)]
    result["overlap_area_m2"] = result.geometry.area
    result = result.loc[result["overlap_area_m2"] > 0].reset_index(drop=True)

    log.info("found %d overlapping pairs covering %.2f km2",
             len(result), result["overlap_area_m2"].sum() / 1e6)
    return result


def erase(
    target: gpd.GeoDataFrame,
    eraser: gpd.GeoDataFrame,
    min_area_m2: float = 1.0,
    label: str = "erase",
) -> gpd.GeoDataFrame:
    """Remove from `target` everything covered by `eraser`.

    Equivalent to the ArcGIS Erase tool and to the original `gpd.overlay(...,
    how="difference")`, with one addition: polygons below `min_area_m2` are
    dropped afterwards. Difference operations along shared boundaries leave
    slivers a few square centimetres across that are artefacts of floating point
    arithmetic. Left in, they inflate feature counts and clutter any map drawn
    from the result.
    """
    target = clean_geometries(target, f"{label}:target")
    eraser = clean_geometries(eraser, f"{label}:eraser")

    if eraser.empty:
        log.warning("%s: eraser layer is empty, returning the target unchanged", label)
        return target

    area_before = target.geometry.area.sum()
    result = gpd.overlay(
        target, eraser[[eraser.geometry.name]], how="difference", keep_geom_type=True
    )
    result = clean_geometries(result, f"{label}:result")

    if min_area_m2 > 0 and len(result):
        small = result.geometry.area < min_area_m2
        if small.any():
            log.info("%s: dropped %d sliver polygons under %.2f m2",
                     label, int(small.sum()), min_area_m2)
            result = result.loc[~small].copy()

    area_after = result.geometry.area.sum()
    log.info("%s: %.2f km2 in, %.2f km2 out, %.2f km2 removed",
             label, area_before / 1e6, area_after / 1e6, (area_before - area_after) / 1e6)
    return result


def select_by_group(
    ln: gpd.GeoDataFrame, overlap_group_code: int, group_column: str = "Group_Code"
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Split LN into the features to keep and the overlaying features to drop.

    The rule is group code, not class code. Class code 6 is "Flaechen ausserhalb
    der LN" and contains real ground cover: ponds, ditches, dry stone walls,
    unpaved paths, home gardens. Group code 99 is "Ueberlagernde Flaechen" and
    contains only features that sit on top of other LN polygons: high-stem fruit
    trees, walnuts, chestnuts, isolated trees and avenues.

    Both halves are returned. The dropped half is needed to report how much area
    it covers and to write it out for inspection, rather than having it vanish.
    """
    column = group_column
    if column not in ln.columns:
        alternatives = [c for c in ln.columns if c.lower() == column.lower()]
        if not alternatives:
            raise KeyError(
                f"LN data has no {column!r} column after the lookup join. "
                f"Columns: {sorted(ln.columns)[:25]}"
            )
        column = alternatives[0]

    codes = pd.to_numeric(ln[column], errors="coerce")
    is_overlay = codes == overlap_group_code

    kept = ln.loc[~is_overlay].copy()
    dropped = ln.loc[is_overlay].copy()

    log.info(
        "selection on %s: keeping %d features, dropping %d overlaying features (%.2f km2)",
        column, len(kept), len(dropped),
        dropped.geometry.area.sum() / 1e6 if len(dropped) else 0.0,
    )

    unclassified = codes.isna().sum()
    if unclassified:
        log.warning(
            "%d LN features have no %s and are being kept by default. "
            "Check whether they should be classified first.",
            int(unclassified), column,
        )

    return kept, dropped
