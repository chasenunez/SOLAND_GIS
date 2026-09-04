"""Tests for the TLM/LN workflow. Run with: python test_tlm_ln.py

Everything is built from small squares and rectangles whose areas can be worked
out on paper. That is the point: a test that asserts the code agrees with itself
proves nothing, whereas a 100 m square has an area of 10,000 m2 whatever the
implementation does.

No network and no access to the real data, so these verify logic rather than
results. The first real run is still yours.
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon, box

from tlm_ln import classify, config, geometry, io, validate

CRS = "EPSG:2056"

logging.disable(logging.WARNING)  # keep expected warnings out of the test output


def square(x: float, y: float, size: float = 100.0) -> Polygon:
    """A size x size square with its lower-left corner at (x, y)."""
    return box(x, y, x + size, y + size)


def frame(geometries, crs: str = CRS, **columns) -> gpd.GeoDataFrame:
    """Build a GeoDataFrame from geometries plus any attribute columns."""
    data = {name: values for name, values in columns.items()}
    return gpd.GeoDataFrame(data, geometry=list(geometries), crs=crs)


class TestErase(unittest.TestCase):
    def test_removes_exactly_the_overlapping_part(self) -> None:
        # A 100x100 target, a 50-wide eraser across its left half.
        target = frame([square(0, 0)])
        eraser = frame([box(0, 0, 50, 100)])
        result = geometry.erase(target, eraser, min_area_m2=0)
        self.assertAlmostEqual(result.geometry.area.sum(), 5_000.0, places=6)

    def test_untouched_when_the_eraser_is_elsewhere(self) -> None:
        target = frame([square(0, 0)])
        eraser = frame([square(1_000, 1_000)])
        result = geometry.erase(target, eraser, min_area_m2=0)
        self.assertAlmostEqual(result.geometry.area.sum(), 10_000.0, places=6)

    def test_full_coverage_leaves_nothing(self) -> None:
        target = frame([square(0, 0)])
        eraser = frame([box(-10, -10, 110, 110)])
        result = geometry.erase(target, eraser, min_area_m2=0)
        self.assertEqual(len(result), 0)

    def test_slivers_below_the_threshold_are_dropped(self) -> None:
        # Eraser leaves a strip 0.005 m wide, so 0.5 m2: a floating-point
        # artefact of the kind a difference along a shared edge produces.
        target = frame([square(0, 0)])
        eraser = frame([box(0, 0, 99.995, 100)])
        kept = geometry.erase(target, eraser, min_area_m2=0)
        dropped = geometry.erase(target, eraser, min_area_m2=1.0)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 0)

    def test_an_empty_eraser_returns_the_target_rather_than_nothing(self) -> None:
        # Returning an empty result here would silently delete a whole layer.
        target = frame([square(0, 0)])
        eraser = frame([], crs=CRS)
        result = geometry.erase(target, eraser, min_area_m2=0)
        self.assertEqual(len(result), 1)


class TestSelectByGroup(unittest.TestCase):
    def setUp(self) -> None:
        # Group 99 is the overlay group. Group 60 is "Sonstiges", which contains
        # real ground cover and must survive: ponds, walls, paths, home gardens.
        self.ln = frame(
            [square(0, 0), square(200, 0), square(400, 0), square(600, 0)],
            Group_Code=[10, 60, 99, 30],
            lnf_code=[501, 904, 921, 701],
        )

    def test_keeps_everything_except_the_overlay_group(self) -> None:
        kept, dropped = geometry.select_by_group(self.ln, 99)
        self.assertEqual(sorted(kept["lnf_code"]), [501, 701, 904])
        self.assertEqual(list(dropped["lnf_code"]), [921])

    def test_class_6_ground_cover_survives(self) -> None:
        # The regression this whole change exists for. Under the old class-6 rule
        # code 904 (ponds and ditches) was dropped and never restored.
        kept, _ = geometry.select_by_group(self.ln, 99)
        self.assertIn(904, list(kept["lnf_code"]))

    def test_missing_group_column_is_a_clear_error(self) -> None:
        with self.assertRaises(KeyError) as caught:
            geometry.select_by_group(frame([square(0, 0)], other=[1]), 99)
        self.assertIn("Group_Code", str(caught.exception))

    def test_finds_the_column_whatever_its_capitalisation(self) -> None:
        # The original code checked for Class_Code or Class_code in two places
        # and disagreed with itself. Accept either rather than fail on a capital.
        ln = frame([square(0, 0), square(200, 0)], group_code=[10, 99])
        kept, dropped = geometry.select_by_group(ln, 99)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 1)


class TestRemoveIdenticalGeometries(unittest.TestCase):
    def test_removes_exact_duplicates(self) -> None:
        gdf = frame([square(0, 0), square(0, 0), square(200, 0)], lnf_code=[501, 502, 503])
        self.assertEqual(len(geometry.remove_identical_geometries(gdf)), 2)

    def test_result_does_not_depend_on_input_order(self) -> None:
        # The original kept the first row in file order, so concatenating the LN
        # v2 and v3 inputs the other way round gave a different survivor.
        one = frame([square(0, 0), square(0, 0)], lnf_code=[601, 501])
        other = frame([square(0, 0), square(0, 0)], lnf_code=[501, 601])
        kept_one = geometry.remove_identical_geometries(one, priority_column="lnf_code")
        kept_other = geometry.remove_identical_geometries(other, priority_column="lnf_code")
        self.assertEqual(list(kept_one["lnf_code"]), list(kept_other["lnf_code"]))
        self.assertEqual(list(kept_one["lnf_code"]), [501])

    def test_ignores_vertex_order(self) -> None:
        # The same square wound the other way is the same square.
        clockwise = Polygon([(0, 0), (0, 100), (100, 100), (100, 0)])
        anticlockwise = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
        gdf = frame([clockwise, anticlockwise], lnf_code=[501, 502])
        self.assertEqual(len(geometry.remove_identical_geometries(gdf)), 1)

    def test_near_identical_geometries_survive_unless_snapping_is_asked_for(self) -> None:
        # A millimetre of difference is kept by default, because snapping changes
        # the data and should be a deliberate choice rather than a hidden one.
        gdf = frame([square(0, 0), box(0, 0, 100.001, 100)], lnf_code=[501, 502])
        self.assertEqual(len(geometry.remove_identical_geometries(gdf)), 2)
        snapped = geometry.remove_identical_geometries(gdf, priority_column="lnf_code", grid_size=0.01)
        self.assertEqual(len(snapped), 1)

    def test_empty_input_is_handled(self) -> None:
        self.assertEqual(len(geometry.remove_identical_geometries(frame([], crs=CRS))), 0)


class TestFindOverlaps(unittest.TestCase):
    def test_reports_the_intersection_area(self) -> None:
        # Two 100x100 squares offset by 50 in x overlap over 50x100 = 5,000 m2.
        gdf = frame([square(0, 0), square(50, 0)], lnf_code=[501, 502])
        overlaps = geometry.find_overlaps(gdf, "lnf_code")
        self.assertEqual(len(overlaps), 1)
        self.assertAlmostEqual(overlaps["overlap_area_m2"].iloc[0], 5_000.0, places=6)

    def test_each_pair_appears_once(self) -> None:
        gdf = frame([square(0, 0), square(50, 0), square(25, 0)], lnf_code=[1, 2, 3])
        overlaps = geometry.find_overlaps(gdf, "lnf_code")
        pairs = {tuple(sorted((row.left_id, row.right_id))) for row in overlaps.itertuples()}
        self.assertEqual(len(pairs), len(overlaps))

    def test_touching_polygons_are_not_overlaps(self) -> None:
        # Sharing an edge is adjacency. Counting it would inflate the total.
        gdf = frame([square(0, 0), square(100, 0)], lnf_code=[501, 502])
        self.assertEqual(len(geometry.find_overlaps(gdf, "lnf_code")), 0)

    def test_disjoint_polygons_produce_nothing(self) -> None:
        gdf = frame([square(0, 0), square(1_000, 0)], lnf_code=[501, 502])
        self.assertEqual(len(geometry.find_overlaps(gdf, "lnf_code")), 0)


class TestCodeValidity(unittest.TestCase):
    def make(self, codes, valid_from, valid_to) -> gpd.GeoDataFrame:
        return frame(
            [square(i * 200, 0) for i in range(len(codes))],
            lnf_code=codes, Gueltig_Von=valid_from, Gueltig_Bis=valid_to,
        )

    def test_retired_code_is_reported(self) -> None:
        # Reis (509) is valid to 2022. Seeing it in a 2025 run means something
        # upstream is stale, and the join would otherwise assign a class silently.
        ln = self.make([509], [None], [2022])
        report = classify.check_code_validity(ln, 2025, "lnf_code")
        self.assertEqual(len(report), 1)
        self.assertEqual(report["issue"].iloc[0], "no longer valid")

    def test_not_yet_introduced_code_is_reported(self) -> None:
        # Kichererbsen (540) starts in 2023, so it cannot appear in 2019 data.
        ln = self.make([540], [2023], [None])
        report = classify.check_code_validity(ln, 2019, "lnf_code")
        self.assertEqual(report["issue"].iloc[0], "not yet valid")

    def test_codes_inside_their_window_are_not_reported(self) -> None:
        ln = self.make([509, 540], [None, 2023], [2022, None])
        self.assertEqual(len(classify.check_code_validity(ln, 2022, "lnf_code")), 1)

    def test_codes_with_no_window_are_always_fine(self) -> None:
        ln = self.make([501], [None], [None])
        self.assertEqual(len(classify.check_code_validity(ln, 2025, "lnf_code")), 0)

    def test_missing_validity_columns_do_not_break_the_run(self) -> None:
        ln = frame([square(0, 0)], lnf_code=[501])
        self.assertEqual(len(classify.check_code_validity(ln, 2025, "lnf_code")), 0)


class TestOverlapFlagConsistency(unittest.TestCase):
    def test_finds_codes_flagged_as_overlaying_but_not_excluded(self) -> None:
        # The real case: 927 and 928 carry ueberlagernd = 1 but sit in group 60,
        # so a group-99 rule keeps them and their area is counted twice.
        lookup = pd.DataFrame({
            "ID": [921, 922, 927, 928, 904],
            "Nutzung_DE": ["Hochstamm", "Nussbaeume", "Andere Baeume", "Andere Elemente", "Teiche"],
            "Group_Code": [99, 99, 60, 60, 60],
            "ueberlagernd": [1, 1, 1, 1, 0],
        })
        report = classify.check_overlap_flag_consistency(lookup, 99)
        self.assertEqual(sorted(report["ID"]), [927, 928])

    def test_a_consistent_lookup_reports_nothing(self) -> None:
        lookup = pd.DataFrame({
            "ID": [921, 904], "Group_Code": [99, 60], "ueberlagernd": [1, 0],
        })
        self.assertEqual(len(classify.check_overlap_flag_consistency(lookup, 99)), 0)


class TestSchemaHarmonisation(unittest.TestCase):
    def test_ln_columns_are_renamed_onto_the_shared_schema(self) -> None:
        ln = frame([square(0, 0)], Class=["Ackerland"], Class_Code=[1], Group_Code=[10])
        harmonised = classify.harmonise(ln, source="LN_2025")
        self.assertEqual(harmonised["class"].iloc[0], "Ackerland")
        self.assertEqual(harmonised["class_code"].iloc[0], 1)
        self.assertEqual(harmonised["source"].iloc[0], "LN_2025")

    def test_the_old_capitalised_columns_are_removed(self) -> None:
        # Leaving both means the output has two similarly named columns and no
        # way for a user to tell which is authoritative.
        ln = frame([square(0, 0)], Class=["Ackerland"], Class_Code=[1])
        harmonised = classify.harmonise(ln, source="LN_2025")
        self.assertNotIn("Class", harmonised.columns)
        self.assertNotIn("Class_Code", harmonised.columns)

    def test_every_ln_classification_column_is_lowercased(self) -> None:
        # An earlier version renamed only the four main columns, so Pest_Group
        # and BFF_QI reached the output capitalised, sitting beside lowercase
        # pest_code. That is the same two-schema problem in miniature.
        ln = frame(
            [square(0, 0)],
            Class=["Ackerland"], Class_Code=[1], Group_Code=[10], Group_de=["Acker"],
            Group_en=["Arable_land"], Class_en=["arable"], Pest_Group=["Getreide"],
            Pest_Code=[101], BFF_QI=[0], ueberlagernd=[0],
        )
        harmonised = classify.harmonise(ln, source="LN_2025")
        capitalised = [c for c in harmonised.columns if c != "geometry" and c != c.lower()]
        self.assertEqual(capitalised, [], f"still capitalised: {capitalised}")
        self.assertEqual(harmonised["pest_group"].iloc[0], "Getreide")
        self.assertEqual(harmonised["bff_qi"].iloc[0], 0)

    def test_join_artefacts_are_dropped_but_traceability_is_kept(self) -> None:
        # Validity windows describe a code, not a polygon, and mean nothing once
        # the join has happened. lnf_code and objektart are what let someone
        # trace a polygon back to its source, so they stay.
        gdf = frame(
            [square(0, 0)],
            lnf_code=[501], objektart=["Wald"], class_code=[1],
            Gueltig_Von=[None], Gueltig_Bis=[2022], OBJEKTARTD=["Wald"], ID=[501],
        )
        cleaned = classify.drop_join_artefacts(gdf)
        for column in ("Gueltig_Von", "Gueltig_Bis", "OBJEKTARTD", "ID"):
            self.assertNotIn(column, cleaned.columns)
        for column in ("lnf_code", "objektart", "class_code"):
            self.assertIn(column, cleaned.columns)

    def test_dropping_artefacts_is_safe_when_none_are_present(self) -> None:
        gdf = frame([square(0, 0)], class_code=[1])
        self.assertEqual(list(classify.drop_join_artefacts(gdf).columns), list(gdf.columns))

    def test_merged_output_has_one_populated_class_column(self) -> None:
        # The end-to-end version of the same point. The original concatenated a
        # lowercase TLM schema with a capitalised LN schema and produced two
        # half-empty families.
        ln = classify.harmonise(
            frame([square(0, 0)], Class=["Ackerland"], Class_Code=[1]), "LN_2025"
        )
        tlm = classify.ensure_target_fields(frame([square(200, 0)]))
        tlm.loc[:, ["class", "class_code", "source"]] = ["Wald", 5, "TLM_total"]

        merged = geometry.merge_layers([ln, tlm], CRS, "final")
        self.assertFalse(merged["class_code"].isna().any())
        self.assertEqual(sorted(merged["class"]), ["Ackerland", "Wald"])


class TestClassifyNutzungsareal(unittest.TestCase):
    def test_assigns_the_codes_that_match_the_ln_lookup(self) -> None:
        # Checked against LN_LBZ_Lookup_Feb2026: Reben is 3/30/300 and
        # Obstanlagen is 3/31/310. The TLM has no lnf_code to join on, so these
        # are written in code; this test is what keeps them honest.
        nutz = frame([square(0, 0), square(200, 0)], objektart=["Reben", "Obstanlage"])
        result = classify.classify_nutzungsareal(nutz)
        reben = result.loc[result["objektart"] == "Reben"].iloc[0]
        obst = result.loc[result["objektart"] == "Obstanlage"].iloc[0]
        self.assertEqual((reben["class_code"], reben["group_code"], reben["pest_code"]), (3, 30, 300))
        self.assertEqual((obst["class_code"], obst["group_code"], obst["pest_code"]), (3, 31, 310))

    def test_ignores_other_objektart_values(self) -> None:
        nutz = frame([square(0, 0), square(200, 0)], objektart=["Reben", "Truppenuebungsplatz"])
        self.assertEqual(len(classify.classify_nutzungsareal(nutz)), 1)

    def test_finding_nothing_is_an_error_with_the_values_listed(self) -> None:
        # A renamed objektart value would otherwise produce an empty layer that
        # propagates silently to the end of the workflow.
        nutz = frame([square(0, 0)], objektart=["Rebbau"])
        with self.assertRaises(ValueError) as caught:
            classify.classify_nutzungsareal(nutz)
        self.assertIn("Rebbau", str(caught.exception))


class TestClassifyBodenbedeckung(unittest.TestCase):
    def setUp(self) -> None:
        self.lookup = pd.DataFrame({
            "OBJEKTARTD": ["Wald", "Fels", "Fliessgewaesser"],
            "class": ["Wald", "Fels", "Gewaesser"],
            "class_code": [5, 74, 71],
            "class_en": ["Forest", "Rock", "Water"],
        })

    def test_forest_classes_collapse_to_one_group(self) -> None:
        bb = frame([square(0, 0), square(200, 0)], objektart=["Wald", "Fels"])
        result = classify.classify_bodenbedeckung(bb, self.lookup)
        forest = result.loc[result["objektart"] == "Wald"].iloc[0]
        rock = result.loc[result["objektart"] == "Fels"].iloc[0]
        self.assertEqual(forest["group_code"], 50)
        self.assertEqual(rock["group_code"], 74)

    def test_unmatched_classes_are_kept_but_left_unclassified(self) -> None:
        # A new swissTLM3D class should not vanish, and should not be guessed at.
        # It should arrive with an empty class and a warning in the log.
        bb = frame([square(0, 0), square(200, 0)], objektart=["Wald", "Neuartige Flaeche"])
        result = classify.classify_bodenbedeckung(bb, self.lookup)
        self.assertEqual(len(result), 2)
        self.assertTrue(pd.isna(result.loc[result["objektart"] == "Neuartige Flaeche", "class"].iloc[0]))

    def test_duplicate_lookup_keys_do_not_multiply_rows(self) -> None:
        # De-duplicating on OBJECTID while joining on OBJEKTARTD, as the original
        # did, leaves duplicates on the join key and the merge multiplies rows.
        duplicated = pd.concat([self.lookup, self.lookup.iloc[[0]]], ignore_index=True)
        bb = frame([square(0, 0)], objektart=["Wald"])
        self.assertEqual(len(classify.classify_bodenbedeckung(bb, duplicated)), 1)


class TestJoinLnLookup(unittest.TestCase):
    def setUp(self) -> None:
        self.lookup = pd.DataFrame({
            "ID": [501, 904, 921],
            "Class": ["Ackerland", "Sonstige", "Sonstige"],
            "Class_Code": [1, 6, 6],
            "Group_Code": [10, 60, 99],
        })
        self.fields = ("Class", "Class_Code", "Group_Code")

    def test_joins_on_lnf_code(self) -> None:
        ln = frame([square(0, 0), square(200, 0)], lnf_code=[501, 921])
        joined = classify.join_ln_lookup(ln, self.lookup, "lnf_code", "ID", self.fields)
        self.assertEqual(list(joined["Group_Code"]), [10, 99])

    def test_a_non_unique_lookup_key_raises_rather_than_picking_one(self) -> None:
        # Validity windows exist so a code can appear twice. Quietly keeping the
        # first row resolves that case wrongly and without comment.
        duplicated = pd.concat([self.lookup, self.lookup.iloc[[0]]], ignore_index=True)
        ln = frame([square(0, 0)], lnf_code=[501])
        with self.assertRaises(ValueError) as caught:
            classify.join_ln_lookup(ln, duplicated, "lnf_code", "ID", self.fields)
        self.assertIn("not unique", str(caught.exception))

    def test_codes_absent_from_the_lookup_survive_unclassified(self) -> None:
        ln = frame([square(0, 0), square(200, 0)], lnf_code=[501, 99999])
        joined = classify.join_ln_lookup(ln, self.lookup, "lnf_code", "ID", self.fields)
        self.assertEqual(len(joined), 2)
        self.assertTrue(pd.isna(joined.loc[joined["lnf_code"] == 99999, "Class_Code"].iloc[0]))

    def test_a_missing_join_field_names_itself(self) -> None:
        with self.assertRaises(KeyError) as caught:
            classify.join_ln_lookup(frame([square(0, 0)]), self.lookup, "lnf_code", "ID", self.fields)
        self.assertIn("lnf_code", str(caught.exception))


class TestGapReport(unittest.TestCase):
    def test_measures_the_hole_left_by_the_selection(self) -> None:
        # An overlay polygon sitting entirely outside anything kept becomes a
        # hole: erased from TLM, then not merged back. This is the case that
        # motivates the erase_with_selected_only setting.
        ln_kept = frame([square(0, 0)], Group_Code=[10])
        ln_dropped = frame([square(1_000, 0)], Group_Code=[99])
        tlm_erased = frame([square(2_000, 0)])
        report = validate.gap_report(tlm_erased, ln_kept, ln_dropped)
        self.assertAlmostEqual(report["gap_area_km2"].iloc[0], 10_000 / 1e6, places=6)
        self.assertAlmostEqual(report["gap_share_of_dropped_pct"].iloc[0], 100.0, places=2)

    def test_an_overlay_covered_by_kept_features_leaves_no_hole(self) -> None:
        # The normal case. Fruit trees on a meadow: the meadow stays, so nothing
        # is lost when the trees are dropped.
        ln_kept = frame([square(0, 0)], Group_Code=[10])
        ln_dropped = frame([box(20, 20, 40, 40)], Group_Code=[99])
        tlm_erased = frame([square(2_000, 0)])
        report = validate.gap_report(tlm_erased, ln_kept, ln_dropped)
        self.assertAlmostEqual(report["gap_area_km2"].iloc[0], 0.0, places=6)

    def test_nothing_dropped_means_no_gap(self) -> None:
        report = validate.gap_report(frame([square(0, 0)]), frame([square(200, 0)]), frame([], crs=CRS))
        self.assertEqual(report["gap_area_km2"].iloc[0], 0.0)


class TestValidationReports(unittest.TestCase):
    def test_area_reconciliation_totals_are_right(self) -> None:
        report = validate.area_reconciliation({
            "a": frame([square(0, 0)]),
            "b": frame([square(0, 0), square(200, 0)]),
        })
        self.assertAlmostEqual(report.loc[report["layer"] == "a", "area_km2"].iloc[0], 0.01, places=6)
        self.assertAlmostEqual(report.loc[report["layer"] == "b", "area_km2"].iloc[0], 0.02, places=6)

    def test_completeness_counts_unclassified_features(self) -> None:
        gdf = frame([square(0, 0), square(200, 0)], class_code=[1, None])
        report = validate.classification_completeness(gdf, ("class_code",))
        self.assertEqual(report["missing_features"].iloc[0], 1)
        self.assertEqual(report["missing_pct"].iloc[0], 50.0)

    def test_mismatched_crs_raises_before_any_overlay(self) -> None:
        # An overlay between two projections returns an empty result rather than
        # an error, so this has to be caught beforehand.
        with self.assertRaises(ValueError) as caught:
            validate.assert_crs(
                {"good": frame([square(0, 0)]), "bad": frame([square(0, 0)], crs="EPSG:21781")},
                CRS,
            )
        self.assertIn("EPSG:21781", str(caught.exception))

    def test_class_summary_percentages_add_up(self) -> None:
        gdf = frame([square(0, 0), square(200, 0), square(400, 0)], class_code=[1, 1, 5])
        summary = validate.summarise_by_class(gdf)
        self.assertAlmostEqual(summary["area_pct"].sum(), 100.0, places=2)
        self.assertEqual(summary.loc[summary["class_code"] == 1, "features"].iloc[0], 2)


class TestLookupReading(unittest.TestCase):
    """Reading the lookup tables, which is where two silent failures lived.

    The LN lookup is cp1252 and semicolon separated while the TLM lookup is
    UTF-8 and comma separated, and the LN one uses three different missing-value
    markers. None of that was handled before, which is how the validity columns
    came to be ignored: they arrived as the literal string "<Null>" and every
    numeric comparison against them quietly did nothing.
    """

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)

    def write(self, name: str, text: str, encoding: str = "utf-8") -> Path:
        target = self.path / name
        target.write_text(text, encoding=encoding)
        return target

    def test_reads_semicolon_separated_cp1252(self) -> None:
        path = self.write(
            "ln.csv",
            "ID;Nutzung_DE;Class\n501;Sommergerste;Ackerland\n701;Reben;Dauerkulturen\n",
            encoding="cp1252",
        )
        table = io.read_lookup_csv(path, config.LOOKUP_NA_VALUES)
        self.assertEqual(list(table.columns), ["ID", "Nutzung_DE", "Class"])
        self.assertEqual(len(table), 2)

    def test_reads_comma_separated_utf8(self) -> None:
        path = self.write("tlm.csv", "OBJECTID *,OBJEKTARTD,class\n1,Wald,Wald\n")
        table = io.read_lookup_csv(path, config.LOOKUP_NA_VALUES)
        self.assertEqual(list(table.columns), ["OBJECTID *", "OBJEKTARTD", "class"])

    def test_all_three_sentinels_become_missing(self) -> None:
        # <Null>, NULL and <zero> all appear in the real Feb 2026 lookup.
        path = self.write(
            "sentinels.csv",
            "ID;Gueltig_Bis;Hauptkategorie_DE;crops_en\n"
            "501;<Null>;NULL;<zero>\n"
            "509;2022;Ackerflaeche;Barley\n",
            encoding="cp1252",
        )
        table = io.read_lookup_csv(path, config.LOOKUP_NA_VALUES)
        first = table.iloc[0]
        self.assertTrue(pd.isna(first["Gueltig_Bis"]))
        self.assertTrue(pd.isna(first["Hauptkategorie_DE"]))
        self.assertTrue(pd.isna(first["crops_en"]))

    def test_validity_column_is_numeric_once_sentinels_are_declared(self) -> None:
        # The whole point: with the sentinels handled, Gueltig_Bis can be
        # compared against a year. Without them the column is text and the
        # comparison silently matches nothing.
        path = self.write(
            "validity.csv", "ID;Gueltig_Bis\n501;<Null>\n509;2022\n", encoding="cp1252"
        )
        table = io.read_lookup_csv(path, config.LOOKUP_NA_VALUES)
        as_numbers = pd.to_numeric(table["Gueltig_Bis"], errors="coerce")
        self.assertEqual(as_numbers.notna().sum(), 1)
        self.assertEqual(as_numbers.dropna().iloc[0], 2022)

    def test_trailing_whitespace_is_stripped(self) -> None:
        # The real lookup has "Reben " with a trailing space in Group_de, which
        # splits any groupby against "Reben".
        path = self.write("ws.csv", "ID;Group_de\n701;Reben \n702;Obst\n", encoding="cp1252")
        table = io.read_lookup_csv(path, config.LOOKUP_NA_VALUES)
        self.assertEqual(list(table["Group_de"]), ["Reben", "Obst"])

    def test_a_missing_file_says_so(self) -> None:
        with self.assertRaises(FileNotFoundError):
            io.read_lookup_csv(self.path / "absent.csv", config.LOOKUP_NA_VALUES)

    def test_an_unsupported_format_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            io.read_lookup_csv(self.write("notes.txt", "hello"), config.LOOKUP_NA_VALUES)


class TestConfigLoading(unittest.TestCase):
    """Config resolution, so a path never depends on the working directory."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.config_path = self.path / "config.toml"
        self.config_path.write_text(
            'crs = "EPSG:2056"\n'
            "\n[tlm]\n"
            'tlm_gpkg = "data/tlm.gpkg"\n'
            'tlm_lookup_table = "TLM_LUT.csv"\n'
            'output_gpkg = "out/tlm_total.gpkg"\n'
            "\n[year]\n"
            'tlm_total_gpkg = "out/tlm_total.gpkg"\n'
            'lookup_table = "LN_LUT.csv"\n'
            'output_gpkg = "out/TLM_LN_{year}.gpkg"\n'
            'ln_inputs = ["ln/v2.shp", "ln/v3.shp"]\n'
            "erase_with_selected_only = true\n"
            "\n[years.2024]\n"
            'ln_inputs = ["ln2024/v2.shp"]\n'
            'lookup_table = "LN_LUT_2024.csv"\n',
            encoding="utf-8",
        )

    def test_relative_paths_resolve_against_the_config_file(self) -> None:
        # Not against the working directory, so the same config works from a
        # notebook, a script and a scheduled job without anyone thinking about it.
        cfg, _ = config.load(self.config_path)
        self.assertEqual(cfg.tlm_gpkg, self.path / "data" / "tlm.gpkg")
        self.assertTrue(cfg.output_gpkg.is_absolute())

    def test_absolute_paths_are_left_alone(self) -> None:
        self.config_path.write_text(
            'crs = "EPSG:2056"\n\n[tlm]\n'
            'tlm_gpkg = "/mnt/geodata/tlm.gpkg"\n'
            'tlm_lookup_table = "TLM_LUT.csv"\n'
            'output_gpkg = "out.gpkg"\n',
            encoding="utf-8",
        )
        cfg, _ = config.load(self.config_path)
        self.assertEqual(str(cfg.tlm_gpkg), "/mnt/geodata/tlm.gpkg")

    def test_the_year_is_substituted_into_the_output_name(self) -> None:
        cfg = config.load_year(self.config_path, 2025)
        self.assertEqual(cfg.output_gpkg.name, "TLM_LN_2025.gpkg")
        self.assertEqual(cfg.year, 2025)

    def test_per_year_overrides_win(self) -> None:
        # Each year can name its own inputs and its own vintage of the lookup,
        # which matters because the lookup carries validity windows.
        cfg = config.load_year(self.config_path, 2024)
        self.assertEqual(len(cfg.ln_inputs), 1)
        self.assertEqual(cfg.ln_inputs[0].name, "v2.shp")
        self.assertEqual(cfg.lookup_table.name, "LN_LUT_2024.csv")

    def test_years_without_an_override_fall_back(self) -> None:
        cfg = config.load_year(self.config_path, 2025)
        self.assertEqual(len(cfg.ln_inputs), 2)
        self.assertEqual(cfg.lookup_table.name, "LN_LUT.csv")

    def test_settings_that_change_results_are_read(self) -> None:
        cfg = config.load_year(self.config_path, 2025)
        self.assertTrue(cfg.erase_with_selected_only)
        self.assertEqual(cfg.overlap_group_code, 99)

    def test_a_year_with_no_inputs_fails_loudly(self) -> None:
        self.config_path.write_text(
            'crs = "EPSG:2056"\n\n[year]\n'
            'tlm_total_gpkg = "t.gpkg"\n'
            'lookup_table = "l.csv"\n'
            'output_gpkg = "o_{year}.gpkg"\n',
            encoding="utf-8",
        )
        with self.assertRaises(SystemExit):
            config.load_year(self.config_path, 2025)


class TestEndToEndShape(unittest.TestCase):
    """A miniature run, to check the pieces fit together.

    Deliberately tiny: four LN parcels and one TLM polygon, arranged so the
    expected output area can be worked out by hand.
    """

    def test_selection_erase_and_merge_conserve_area(self) -> None:
        # TLM covers 0..300 in x, 0..100 in y: 30,000 m2.
        tlm = classify.ensure_target_fields(frame([box(0, 0, 300, 100)]))
        tlm.loc[:, ["class", "class_code", "source"]] = ["Wald", 5, "TLM_total"]

        # LN: one arable parcel 0..100, one pond 100..200, one overlay 200..300.
        ln = frame(
            [box(0, 0, 100, 100), box(100, 0, 200, 100), box(200, 0, 300, 100)],
            Group_Code=[10, 60, 99],
            Class_Code=[1, 6, 6],
            Class=["Ackerland", "Sonstige", "Sonstige"],
            lnf_code=[501, 904, 921],
        )

        kept, dropped = geometry.select_by_group(ln, 99)
        self.assertEqual(len(kept), 2)      # arable and pond both stay
        self.assertEqual(len(dropped), 1)

        # Erasing with all LN removes the whole TLM polygon.
        erased_all = geometry.erase(tlm, ln, min_area_m2=0)
        self.assertEqual(len(erased_all), 0)

        merged = geometry.merge_layers(
            [classify.harmonise(kept, "LN_2025"), classify.ensure_target_fields(erased_all)],
            CRS, "final",
        )
        # 20,000 m2 of the original 30,000: the overlay third is the hole.
        self.assertAlmostEqual(merged.geometry.area.sum(), 20_000.0, places=6)

        gaps = validate.gap_report(erased_all, kept, dropped)
        self.assertAlmostEqual(gaps["gap_area_km2"].iloc[0], 10_000 / 1e6, places=6)

        # Erasing with only the kept features leaves the overlay third of TLM,
        # so the result is gapless and area is conserved exactly.
        erased_kept = geometry.erase(tlm, kept, min_area_m2=0)
        merged_kept = geometry.merge_layers(
            [classify.harmonise(kept, "LN_2025"), classify.ensure_target_fields(erased_kept)],
            CRS, "final",
        )
        self.assertAlmostEqual(merged_kept.geometry.area.sum(), 30_000.0, places=6)

    def test_the_final_layer_has_a_single_class_schema(self) -> None:
        tlm = classify.ensure_target_fields(frame([box(200, 0, 300, 100)]))
        tlm.loc[:, ["class", "class_code", "source"]] = ["Wald", 5, "TLM_total"]
        ln = classify.harmonise(
            frame([box(0, 0, 100, 100)], Class=["Ackerland"], Class_Code=[1], Group_Code=[10]),
            "LN_2025",
        )
        merged = geometry.merge_layers([ln, tlm], CRS, "final")

        for column in ("Class", "Class_Code", "Group_Code"):
            self.assertNotIn(column, merged.columns)
        self.assertFalse(merged["class_code"].isna().any())
        self.assertEqual(sorted(merged["source"].unique()), ["LN_2025", "TLM_total"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
