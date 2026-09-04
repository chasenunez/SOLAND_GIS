"""Reading and writing, with the checks that turn silent wrong answers into
loud failures.

The original notebooks read data and carried on. Most of the additions here are
assertions: that a CRS is what it claims, that a required column exists, that a
layer is not unexpectedly empty. None of them change a correct run. All of them
shorten the distance between a mistake and noticing it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import pandas as pd
from shapely import make_valid

log = logging.getLogger(__name__)


def clean_geometries(gdf: gpd.GeoDataFrame, label: str = "layer") -> gpd.GeoDataFrame:
    """Drop empty geometries and repair invalid ones.

    Repairs are logged with a count. An input that needs thousands of repairs is
    telling you something about the source data, and that signal is lost if the
    fix is silent.
    """
    before = len(gdf)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    if before != len(gdf):
        log.info("%s: dropped %d empty or missing geometries", label, before - len(gdf))

    invalid = ~gdf.geometry.is_valid
    if invalid.any():
        log.info("%s: repairing %d invalid geometries", label, int(invalid.sum()))
        gdf.loc[invalid, gdf.geometry.name] = gdf.loc[invalid, gdf.geometry.name].apply(make_valid)

    return gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()


def require_columns(df: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    """Fail early and by name when an expected column is missing.

    Field names in swissTLM3D and in the cantonal data have changed before and
    will change again. A clear error naming the layer and the column is worth far
    more than a KeyError forty minutes into a run.
    """
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise KeyError(
            f"{label} is missing required columns: {missing}. "
            f"Columns present: {sorted(df.columns)[:25]}"
        )


def read_vector(
    path: Path,
    layer: str | None = None,
    crs: str | None = None,
    label: str | None = None,
) -> gpd.GeoDataFrame:
    """Read a vector layer, reprojecting only when the CRS actually differs.

    A missing CRS is treated as an error unless a target is supplied, because
    assigning a projection to data that might be in a different one produces
    output that looks right and is silently displaced.
    """
    label = label or f"{path.name}:{layer}" if layer else path.name
    if not path.exists():
        raise FileNotFoundError(f"{label}: no such file: {path}")

    gdf = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
    if gdf.empty:
        raise ValueError(f"{label} is empty. Check the layer name and the source file.")

    if gdf.crs is None:
        if crs is None:
            raise ValueError(f"{label} has no CRS and no target CRS was given")
        log.warning("%s has no CRS; assigning %s without reprojection", label, crs)
        gdf = gdf.set_crs(crs)
    elif crs is not None and gdf.crs.to_string() != crs:
        log.info("%s: reprojecting from %s to %s", label, gdf.crs.to_string(), crs)
        gdf = gdf.to_crs(crs)

    log.info("%s: read %d features", label, len(gdf))
    return clean_geometries(gdf, label)


def read_lookup_csv(path: Path, na_values: Iterable[str]) -> pd.DataFrame:
    """Read a lookup CSV, handling the encoding and the missing-value sentinels.

    Two things the original code did not do. The LN lookup is cp1252, not UTF-8,
    and it uses three different sentinels for absent values: '<Null>', 'NULL' and
    '<zero>'. Left undeclared they arrive as literal strings, which is how the
    validity columns came to be ignored.

    String columns are also stripped. The Feb 2026 lookup carries 'Reben ' with a
    trailing space in Group_de, which silently splits any groupby against 'Reben'.
    """
    if not path.exists():
        raise FileNotFoundError(f"lookup table not found: {path}")

    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        table = pd.read_excel(path, na_values=list(na_values))
    elif suffix == ".csv":
        table = _read_csv_flexibly(path, na_values)
    else:
        raise ValueError(f"unsupported lookup table format: {path}")

    for column in table.select_dtypes(include="object").columns:
        table[column] = table[column].str.strip()

    log.info("lookup %s: %d rows, %d columns", path.name, len(table), len(table.columns))
    return table


def _read_csv_flexibly(path: Path, na_values: Iterable[str]) -> pd.DataFrame:
    """Try the separator and encoding combinations these tables actually use.

    The two lookups in this workflow differ: TLM_LUT.csv is comma separated and
    readable as UTF-8, LN_LBZ_Lookup is semicolon separated and cp1252. Rather
    than hardcode which is which, try both and keep the one that yields more than
    a single column.
    """
    last_error: Exception | None = None
    for separator in (";", ","):
        for encoding in ("cp1252", "utf-8"):
            try:
                table = pd.read_csv(
                    path, sep=separator, encoding=encoding, na_values=list(na_values)
                )
            except (UnicodeDecodeError, pd.errors.ParserError) as error:
                last_error = error
                continue
            if len(table.columns) > 1:
                return table
    raise ValueError(f"could not parse {path} as CSV: {last_error}")


def write_layer(gdf: gpd.GeoDataFrame, gpkg: Path, layer: str) -> None:
    """Write one layer to a GeoPackage, replacing any existing layer of that name."""
    gpkg.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(gpkg, layer=layer, driver="GPKG", mode="w")
    log.info("wrote %d features to %s:%s", len(gdf), gpkg.name, layer)


def file_checksum(path: Path, chunk: int = 1 << 20) -> str:
    """sha256 of a file, read in chunks so size does not dictate memory use."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(path: Path, inputs: dict[str, Path], settings: dict) -> Path:
    """Record what went into a run, so the output can be traced back to it.

    This is the piece that makes a run reproducible rather than merely repeatable.
    Without it, a GeoPackage six months old cannot be tied to the versions of the
    TLM and the lookup that produced it. Checksums are computed for inputs under
    a gigabyte; larger files record size and modification time instead, since
    hashing them would dominate the run.
    """
    entries = {}
    for name, source in inputs.items():
        source = Path(source)
        if not source.exists():
            entries[name] = {"path": str(source), "status": "missing"}
            continue
        stat = source.stat()
        entry = {
            "path": str(source),
            "bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        }
        if stat.st_size < (1 << 30):
            entry["sha256"] = file_checksum(source)
        else:
            entry["sha256"] = "not computed: file larger than 1 GB"
        entries[name] = entry

    manifest = {
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": _package_versions(),
        "settings": {key: str(value) for key, value in settings.items()},
        "inputs": entries,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    log.info("wrote run manifest to %s", path)
    return path


def _package_versions() -> dict[str, str]:
    """Versions of the libraries whose behaviour could change a result."""
    import shapely

    try:
        import pyogrio

        pyogrio_version = pyogrio.__version__
    except ImportError:  # pragma: no cover - optional engine
        pyogrio_version = "not installed"

    return {
        "geopandas": gpd.__version__,
        "pandas": pd.__version__,
        "shapely": shapely.__version__,
        "pyogrio": pyogrio_version,
    }
