"""Turning source attributes into the shared classification.

Two classification systems meet here. The TLM side (notebook 01) produced
lowercase `class`, `class_code`, `group_code`, `pest_code`. The LN side
(notebook 02) carried `Class`, `Class_Code`, `Group_Code`, `Pest_Code` from the
lookup. The original code concatenated the two, so the final layer held two
parallel half-empty column families and no single field a user could classify on.

Everything below writes the lowercase names, and `harmonise` renames the LN
columns onto them before the merge. One schema, one set of names, one column to
symbolise on.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

log = logging.getLogger(__name__)

# The shared schema. Anything joining the final layer must fill these.
TARGET_FIELDS = (
    "class", "class_code", "group_code", "group_de", "group_en",
    "class_en", "pest_group", "pest_code", "bff_qi", "ueberlagernd", "source",
)

# Maps the LN lookup's capitalised names onto the shared lowercase schema.
#
# Every classification column the LN side contributes needs an entry here. An
# earlier version covered only the four main ones, so Pest_Group and BFF_QI came
# through capitalised and sat next to lowercase pest_code in the output: a
# smaller version of the two-schema problem this renaming exists to prevent.
# ueberlagernd is already lowercase in the lookup and needs no rename.
LN_RENAMES = {
    "Class": "class",
    "Class_Code": "class_code",
    "Group_Code": "group_code",
    "Group_de": "group_de",
    "Group_en": "group_en",
    "Class_en": "class_en",
    "Pest_Group": "pest_group",
    "Pest_Code": "pest_code",
    "BFF_QI": "bff_qi",
}

# Columns that exist only to get the join done, and that mean nothing once it
# has. Dropped from the published layer.
#
# The validity window is the clearest case: Gueltig_Von and Gueltig_Bis describe
# when a *code* was in use, not anything about the polygon carrying it, and
# `check_code_validity` has already read them by this point. OBJEKTARTD is the
# lookup's copy of the TLM's own objektart, which stays.
JOIN_ARTEFACTS = ("Gueltig_Von", "Gueltig_Bis", "OBJEKTARTD", "ID")

# TLM Nutzungsareal classification. These values were checked against the LN
# lookup and agree with it exactly: Reben is class 3 / group 30 / pest 300, and
# Obstanlagen is class 3 / group 31 / pest 310. They are written here rather than
# read from the lookup because the TLM has no lnf_code to join on.
TLM_NUTZUNGSAREAL_CLASSES = {
    "Reben": {"class": "Dauerkulturen", "class_code": 3, "group_code": 30,
              "group_de": "Reben", "group_en": "Vines", "pest_code": 300},
    "Obstanlage": {"class": "Dauerkulturen", "class_code": 3, "group_code": 31,
                   "group_de": "Obst", "group_en": "Fruits", "pest_code": 310},
}

# TLM Siedlungsname classification, matching row 16 of TLM_LUT.csv.
TLM_SIEDLUNG_CLASS = {
    "class": "Siedlung", "class_code": 75, "group_code": 75,
    "group_de": "Siedlung", "group_en": "Urban", "pest_code": pd.NA,
}

# Forest classes are collapsed to one group code, as in the original workflow.
FOREST_GROUP_CODE = 50


def ensure_target_fields(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add any missing shared-schema columns, so concatenation cannot misalign."""
    gdf = gdf.copy()
    for column in TARGET_FIELDS:
        if column not in gdf.columns:
            gdf[column] = pd.NA
    return gdf


def classify_nutzungsareal(nutz: gpd.GeoDataFrame, source: str = "TLM_Nutzungsareal") -> gpd.GeoDataFrame:
    """Select orchards and vineyards from TLM Nutzungsareal and classify them."""
    selected = nutz.loc[nutz["objektart"].isin(TLM_NUTZUNGSAREAL_CLASSES)].copy()
    if selected.empty:
        raise ValueError(
            "No Reben or Obstanlage found in TLM Nutzungsareal. "
            f"objektart values present: {sorted(nutz['objektart'].dropna().unique())[:20]}"
        )

    selected = ensure_target_fields(selected)
    for objektart, values in TLM_NUTZUNGSAREAL_CLASSES.items():
        mask = selected["objektart"] == objektart
        for column, value in values.items():
            selected.loc[mask, column] = value
    selected["source"] = source

    log.info("Nutzungsareal: %s", dict(selected["objektart"].value_counts()))
    return selected


def classify_siedlung(siedl: gpd.GeoDataFrame, source: str = "TLM_Siedlungsname") -> gpd.GeoDataFrame:
    """Select settlement polygons (objektart == 'Ort') and classify them."""
    selected = siedl.loc[siedl["objektart"] == "Ort"].copy()
    if selected.empty:
        raise ValueError(
            "No features with objektart == 'Ort' in TLM Siedlungsname. "
            f"Values present: {sorted(siedl['objektart'].dropna().unique())[:20]}"
        )

    selected = ensure_target_fields(selected)
    for column, value in TLM_SIEDLUNG_CLASS.items():
        selected[column] = value
    selected["source"] = source

    log.info("Siedlung: selected %d of %d features", len(selected), len(siedl))
    return selected


def classify_bodenbedeckung(
    bb: gpd.GeoDataFrame, lookup: pd.DataFrame, source: str = "TLM_Bodenbedeckung"
) -> gpd.GeoDataFrame:
    """Join TLM Bodenbedeckung to the TLM lookup and derive the group code.

    Two corrections against the original. The lookup is de-duplicated on the join
    key (OBJEKTARTD) rather than on OBJECTID: de-duplicating on a column you do
    not join on leaves duplicates on the one you do, and the merge then either
    raises or multiplies rows. And unmatched objektart values are reported, so a
    new swissTLM3D class arrives as a warning rather than as silently
    unclassified polygons.
    """
    required = ["OBJEKTARTD", "class", "class_code"]
    missing = [column for column in required if column not in lookup.columns]
    if missing:
        raise KeyError(f"TLM lookup is missing columns: {missing}")

    duplicated = lookup["OBJEKTARTD"].duplicated().sum()
    if duplicated:
        log.warning(
            "TLM lookup has %d duplicate OBJEKTARTD values; keeping the first of each",
            int(duplicated),
        )
    keep = [column for column in ("OBJEKTARTD", "class", "class_code", "class_en") if column in lookup.columns]
    small = lookup[keep].drop_duplicates(subset="OBJEKTARTD").copy()

    joined = bb.merge(
        small, how="left", left_on="objektart", right_on="OBJEKTARTD", validate="many_to_one"
    )
    joined = gpd.GeoDataFrame(joined, geometry=bb.geometry.name, crs=bb.crs)

    unmatched = joined.loc[joined["class"].isna(), "objektart"]
    if len(unmatched):
        counts = unmatched.value_counts().to_dict()
        log.warning(
            "%d Bodenbedeckung features did not match the lookup. "
            "Unmatched objektart values: %s. Add them to TLM_LUT.csv, otherwise "
            "they carry no classification into the final layer.",
            len(unmatched), counts,
        )

    joined = ensure_target_fields(joined)
    joined["group_code"] = joined["class_code"]
    joined.loc[joined["class"] == "Wald", "group_code"] = FOREST_GROUP_CODE
    joined["group_de"] = joined["class"]
    if "class_en" in joined.columns:
        joined["group_en"] = joined["class_en"]
    joined["pest_code"] = pd.NA
    joined["source"] = source

    return joined


def join_ln_lookup(
    ln: gpd.GeoDataFrame,
    lookup: pd.DataFrame,
    join_field: str,
    lookup_key: str,
    fields: tuple[str, ...],
) -> gpd.GeoDataFrame:
    """Join classification fields onto LN features by lnf_code.

    The lookup key is asserted unique rather than quietly de-duplicated. In the
    Feb 2026 table all 174 ids are unique, so de-duplication is a no-op today.
    But the table carries validity windows, which exist precisely so a code can
    appear more than once, and silently keeping whichever row came first would
    resolve that case wrongly and without comment.
    """
    if join_field not in ln.columns:
        raise KeyError(
            f"LN data has no join field {join_field!r}. Columns: {sorted(ln.columns)[:25]}"
        )
    if lookup_key not in lookup.columns:
        raise KeyError(f"lookup has no key column {lookup_key!r}")

    duplicated = lookup[lookup_key].duplicated()
    if duplicated.any():
        offenders = sorted(lookup.loc[duplicated, lookup_key].unique())
        raise ValueError(
            f"Lookup key {lookup_key!r} is not unique: {offenders}. "
            "This usually means codes have been given validity windows. Filter the "
            "lookup by the run year before joining rather than dropping duplicates."
        )

    available = [field for field in fields if field in lookup.columns]
    absent = [field for field in fields if field not in lookup.columns]
    if absent:
        log.warning("lookup is missing optional fields, continuing without them: %s", absent)

    small = lookup[[lookup_key, *available]].copy()
    small[lookup_key] = pd.to_numeric(small[lookup_key], errors="coerce").astype("Int64")

    ln = ln.copy()
    ln[join_field] = pd.to_numeric(ln[join_field], errors="coerce").astype("Int64")

    joined = ln.merge(
        small, how="left", left_on=join_field, right_on=lookup_key, validate="many_to_one"
    )
    if lookup_key != join_field:
        joined = joined.drop(columns=[lookup_key], errors="ignore")

    unmatched = joined["Class_Code"].isna() if "Class_Code" in joined.columns else joined[join_field].isna()
    if unmatched.any():
        codes = sorted(joined.loc[unmatched, join_field].dropna().unique().tolist())
        log.warning(
            "%d LN features carry codes absent from the lookup: %s. "
            "They will have no classification in the output.",
            int(unmatched.sum()), codes[:30],
        )

    return gpd.GeoDataFrame(joined, geometry=ln.geometry.name, crs=ln.crs)


def check_code_validity(ln: gpd.GeoDataFrame, year: int, join_field: str) -> pd.DataFrame:
    """Warn where a feature's code was not valid in the year being processed.

    The lookup records Gueltig_Von and Gueltig_Bis for around thirty codes: Reis
    (509) runs to 2022, Kichererbsen (540) starts in 2023, Ackerschonstreifen
    appears as 555 to 2022 and again as 950 from 2023. The join does not consult
    them, so a feature carrying a retired or not-yet-introduced code is still
    given a class, silently.

    This warns rather than fails. Whether cantonal entry systems restrict farmers
    to currently valid codes is not established, so an out-of-window code may be
    legitimate. What it should never be is invisible.
    """
    columns = [column for column in ("Gueltig_Von", "Gueltig_Bis") if column in ln.columns]
    if not columns:
        log.info("validity columns not present in the joined data; skipping the year check")
        return pd.DataFrame(columns=["code", "issue", "valid_from", "valid_to", "features"])

    frame = ln[[join_field, *columns]].copy()
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    valid_from = frame.get("Gueltig_Von")
    valid_to = frame.get("Gueltig_Bis")

    too_early = valid_from.notna() & (valid_from > year) if valid_from is not None else pd.Series(False, index=frame.index)
    too_late = valid_to.notna() & (valid_to < year) if valid_to is not None else pd.Series(False, index=frame.index)

    problems = []
    for mask, issue in ((too_early, "not yet valid"), (too_late, "no longer valid")):
        if not mask.any():
            continue
        for code, group in frame.loc[mask].groupby(join_field):
            problems.append({
                "code": code,
                "issue": issue,
                "valid_from": group["Gueltig_Von"].iloc[0] if "Gueltig_Von" in group else None,
                "valid_to": group["Gueltig_Bis"].iloc[0] if "Gueltig_Bis" in group else None,
                "features": len(group),
            })

    report = pd.DataFrame(problems, columns=["code", "issue", "valid_from", "valid_to", "features"])
    if len(report):
        log.warning(
            "%d land use codes are outside their validity window for %d, covering %d features. "
            "This may mean the cantonal delivery is stale, the year is mislabelled, or the "
            "lookup predates the year being processed.",
            len(report), year, int(report["features"].sum()),
        )
        for row in report.itertuples():
            log.warning(
                "  code %s %s in %d (valid %s to %s), %d features",
                row.code, row.issue, year, row.valid_from, row.valid_to, row.features,
            )
    else:
        log.info("all land use codes are within their validity window for %d", year)

    return report


def check_overlap_flag_consistency(lookup: pd.DataFrame, overlap_group_code: int) -> pd.DataFrame:
    """Report codes flagged as overlaying that the group code does not exclude.

    In the Feb 2026 lookup, group code 99 ("Ueberlagernde Flaechen") holds four
    codes: 921, 922, 923 and 924. But six codes carry ueberlagernd = 1. The other
    two are 927 "Andere Baeume" and 928 "Andere Elemente", both regionsspezifische
    Biodiversitaetsfoerderflaechen, and both sit in group 60 rather than 99.

    Under a group-99 rule those two are kept, even though the data model says they
    overlay something else. Trees on a meadow counted as their own surface means
    that area is counted twice. This may be an oversight in the lookup or it may
    be deliberate, which is why this reports rather than decides.
    """
    if "ueberlagernd" not in lookup.columns or "Group_Code" not in lookup.columns:
        return pd.DataFrame(columns=["ID", "Nutzung_DE", "Group_Code", "ueberlagernd"])

    flagged = pd.to_numeric(lookup["ueberlagernd"], errors="coerce") == 1
    excluded = pd.to_numeric(lookup["Group_Code"], errors="coerce") == overlap_group_code
    mismatch = flagged & ~excluded

    columns = [c for c in ("ID", "Nutzung_DE", "Group_Code", "ueberlagernd") if c in lookup.columns]
    report = lookup.loc[mismatch, columns].copy()

    if len(report):
        log.warning(
            "%d codes are flagged ueberlagernd=1 but are not in group %d, so they are kept "
            "in the output and their area may be double counted: %s",
            len(report), overlap_group_code,
            report.get("ID", pd.Series(dtype=object)).tolist(),
        )
    return report


def harmonise(gdf: gpd.GeoDataFrame, source: str) -> gpd.GeoDataFrame:
    """Rename LN lookup columns onto the shared lowercase schema.

    Called on the LN side before the final merge, so the output carries one set of
    classification columns instead of two half-filled ones.
    """
    renames = {old: new for old, new in LN_RENAMES.items() if old in gdf.columns}
    harmonised = gdf.rename(columns=renames).copy()
    harmonised = ensure_target_fields(harmonised)
    harmonised["source"] = source

    # Drop leftovers from the original capitalised schema, so nobody has to guess
    # which of two similarly named columns is authoritative.
    leftovers = [old for old in LN_RENAMES if old in harmonised.columns]
    if leftovers:
        harmonised = harmonised.drop(columns=leftovers)

    return harmonised


def drop_join_artefacts(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Remove the columns that only existed to make the lookup join work.

    Run once, on the final layer, rather than earlier: `check_code_validity`
    needs the validity columns and the selection needs the group code, so the
    tidying has to come after both.

    Kept deliberately narrow. It would be easy to reduce the output to the target
    schema alone, but `lnf_code` and `objektart` are the two fields that let
    someone trace a polygon back to its source classification, and throwing those
    away to make the table look neat would be a poor trade.
    """
    present = [column for column in JOIN_ARTEFACTS if column in gdf.columns]
    if not present:
        return gdf

    log.info("dropping join artefacts from the published layer: %s", present)
    return gdf.drop(columns=present)
