"""End-to-end tests for the two pipeline functions. Run with: python test_pipeline.py

Separate from test_tlm_ln.py because these are a different kind of test. Those
check one function against arithmetic you can do on paper and finish in a fifth
of a second. These build real GeoPackages and shapefiles in a temporary
directory, run the whole workflow over them, and read the files back.

They exist because for a while the unit tests were green while the two functions
the notebooks actually call had never been executed by anything. That is the
worst place for a coverage gap to be: it looks like the code is tested.

The fixture is deliberately small but structurally faithful. Every quirk of the
real inputs is reproduced, because those quirks are what break readers:

  - three swissTLM3D layers in one GeoPackage, with objektart values that need
    selecting rather than taking wholesale,
  - a TLM lookup that is comma separated and UTF-8,
  - an LN lookup that is semicolon separated, cp1252, and uses three different
    missing-value sentinels,
  - LN inputs split across two model versions, as the cantons currently are,
  - a code with a closed validity window, and a code flagged as overlaying that
    sits outside group 99.

Areas are chosen so every expected number can be worked out by hand. The TLM
covers 0 to 300 in x and 0 to 100 in y with a settlement running to 350; the LN
parcels tile 0 to 400. Everything below follows from that.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from tlm_ln import classify, config, pipeline

CRS = "EPSG:2056"

logging.disable(logging.WARNING)  # the expected warnings are asserted, not printed


def frame(geometries, **columns) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(columns, geometry=list(geometries), crs=CRS)


def build_fixture(directory: Path) -> Path:
    """Write a miniature but structurally faithful dataset. Returns the config path."""

    # --- swissTLM3D stand-in: three layers in one GeoPackage ---------------
    tlm = directory / "tlm.gpkg"
    frame([box(0, 0, 100, 100), box(100, 0, 200, 100), box(600, 0, 700, 100)],
          objektart=["Reben", "Obstanlage", "Truppenuebungsplatz"]
          ).to_file(tlm, layer="tlm_areale_nutzungsareal", driver="GPKG", mode="w")

    # The settlement runs to 350, past the land cover that stops at 300, which
    # is what leaves something behind after the erase.
    frame([box(0, 0, 350, 100), box(800, 0, 850, 100)],
          objektart=["Ort", "Weiler"]
          ).to_file(tlm, layer="tlm_namen_siedlungsname", driver="GPKG", mode="w")

    frame([box(200, 0, 300, 100), box(500, 0, 600, 100), box(900, 0, 950, 100)],
          objektart=["Wald", "Fels", "Neuartige Flaeche"]
          ).to_file(tlm, layer="tlm_bb_bodenbedeckung", driver="GPKG", mode="w")

    # --- TLM lookup: comma separated, UTF-8, and missing the new class ------
    pd.DataFrame({
        "OBJECTID *": [1, 2],
        "OBJEKTARTD": ["Wald", "Fels"],
        "class": ["Wald", "Fels"],
        "class_code": [5, 74],
        "class_en": ["Forest", "Rock"],
    }).to_csv(directory / "TLM_LUT.csv", index=False)

    # --- LN lookup: semicolon separated, cp1252, three sentinels ------------
    pd.DataFrame({
        "ID": [501, 904, 921, 927],
        "Nutzung_DE": ["Sommergerste", "Teiche", "Hochstamm-Feldobstbaeume", "Andere Baeume"],
        "Gueltig_Von": ["<Null>", "<Null>", "<Null>", "<Null>"],
        "Gueltig_Bis": ["2022", "<Null>", "<Null>", "<Null>"],
        "ueberlagernd": [0, 0, 1, 1],
        "Hauptkategorie_DE": ["Ackerflaeche", "NULL", "NULL", "NULL"],
        "Class": ["Ackerland", "Sonstige", "Sonstige", "Sonstige"],
        "Class_Code": [1, 6, 6, 6],
        "Group_Code": [10, 60, 99, 60],
        "Group_de": ["Acker", "Sonstiges", "UeberlagerndeFl", "Sonstiges"],
        "Group_en": ["Arable_land", "Others", "Overlap_area", "Others"],
        "Class_en": ["arable", "Others", "Others", "Others"],
        "BFF_QI": [0, 1, 1, 1],
        "Pest_Group": ["Getreide", "<Null>", "<Null>", "<Null>"],
        "Pest_Code": [101, "<Null>", "<Null>", "<Null>"],
        "crops_en": ["Barley", "<zero>", "<Null>", "<Null>"],
    }).to_csv(directory / "LN_LUT.csv", index=False, sep=";", encoding="cp1252")

    # --- LN inputs, split across two model versions as the cantons are ------
    frame([box(0, 0, 100, 100), box(100, 0, 200, 100)],
          lnf_code=[501, 904]).to_file(directory / "ln_v2.shp")
    frame([box(200, 0, 300, 100), box(300, 0, 400, 100)],
          lnf_code=[921, 927]).to_file(directory / "ln_v3.shp")

    config_path = directory / "config.toml"
    config_path.write_text(
        'crs = "EPSG:2056"\n'
        "\n[tlm]\n"
        'tlm_gpkg = "tlm.gpkg"\n'
        'tlm_lookup_table = "TLM_LUT.csv"\n'
        'output_gpkg = "out_tlm.gpkg"\n'
        "\n[year]\n"
        'tlm_total_gpkg = "out_tlm.gpkg"\n'
        'lookup_table = "LN_LUT.csv"\n'
        'output_gpkg = "out_ln_{year}.gpkg"\n'
        'ln_inputs = ["ln_v2.shp", "ln_v3.shp"]\n',
        encoding="utf-8",
    )
    return config_path


class PipelineTestCase(unittest.TestCase):
    """Builds the fixture once and runs both pipelines once, for all subclasses.

    Both pipelines together take a couple of seconds on this fixture. Running
    them per test method would make the suite tedious enough that people stop
    running it, which defeats the purpose.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = Path(tempfile.mkdtemp(prefix="tlm_ln_test_"))
        cls.config_path = build_fixture(cls.directory)

        tlm_cfg, _ = config.load(cls.config_path)
        cls.tlm_result = pipeline.prepare_tlm(tlm_cfg, write=True)
        cls.tlm_cfg = tlm_cfg

        year_cfg = config.load_year(cls.config_path, 2025)
        cls.year_result = pipeline.run_year(year_cfg, write=True)
        cls.year_cfg = year_cfg

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.directory, ignore_errors=True)

    def area(self, result_key: str, layer: str) -> float:
        """Area of one layer in square metres, rounded to kill float noise."""
        layers = getattr(self, result_key)["layers"]
        return round(layers[layer].geometry.area.sum(), 6)


class TestPrepareTlm(PipelineTestCase):
    def test_selects_only_orchards_and_vineyards(self) -> None:
        # Truppenuebungsplatz is in the source layer and must not come through.
        reben = self.tlm_result["layers"]["TLM_Reben"]
        self.assertEqual(sorted(reben["objektart"]), ["Obstanlage", "Reben"])
        self.assertEqual(self.area("tlm_result", "TLM_Reben"), 20_000.0)

    def test_selects_only_settlements(self) -> None:
        # objektart == "Ort" only; Weiler is excluded.
        siedl = self.tlm_result["layers"]["TLM_Siedl"]
        self.assertEqual(list(siedl["objektart"]), ["Ort"])
        self.assertEqual(self.area("tlm_result", "TLM_Siedl"), 35_000.0)

    def test_erase_leaves_the_part_of_the_settlement_not_covered(self) -> None:
        # Settlement 0..350, land cover and orchards 0..300, so 50 x 100 remains.
        self.assertEqual(self.area("tlm_result", "Siedl_erase"), 5_000.0)

    def test_tlm_total_is_the_sum_of_its_parts(self) -> None:
        self.assertEqual(
            self.area("tlm_result", "TLM_total"),
            self.area("tlm_result", "Siedl_erase") + self.area("tlm_result", "TLM_ObstReben"),
        )

    def test_forest_collapses_to_one_group_code(self) -> None:
        bb = self.tlm_result["layers"]["TLM_BB"]
        self.assertEqual(bb.loc[bb["objektart"] == "Wald", "group_code"].iloc[0], 50)

    def test_a_class_missing_from_the_lookup_survives_unclassified(self) -> None:
        # "Neuartige Flaeche" stands in for a new swissTLM3D class. It must not
        # vanish, and must not be guessed at.
        bb = self.tlm_result["layers"]["TLM_BB"]
        row = bb.loc[bb["objektart"] == "Neuartige Flaeche"]
        self.assertEqual(len(row), 1)
        self.assertTrue(pd.isna(row["class"].iloc[0]))

    def test_completeness_report_counts_the_unclassified_area(self) -> None:
        report = self.tlm_result["completeness"]
        missing = report.loc[report["column"] == "class_code", "missing_features"].iloc[0]
        self.assertEqual(missing, 1)

    def test_layers_are_written_and_readable(self) -> None:
        written = set(gpd.list_layers(self.tlm_cfg.output_gpkg)["name"])
        self.assertTrue({"TLM_Reben", "TLM_Siedl", "TLM_BB", "Siedl_erase", "TLM_total"} <= written)
        again = gpd.read_file(self.tlm_cfg.output_gpkg, layer="TLM_total")
        self.assertEqual(round(again.geometry.area.sum(), 6), self.area("tlm_result", "TLM_total"))

    def test_manifest_records_inputs_and_versions(self) -> None:
        manifest = json.loads(
            self.tlm_cfg.output_gpkg.with_suffix(".manifest.json").read_text(encoding="utf-8")
        )
        self.assertIn("tlm_gpkg", manifest["inputs"])
        self.assertEqual(len(manifest["inputs"]["tlm_gpkg"]["sha256"]), 64)
        self.assertIn("geopandas", manifest["packages"])


class TestRunYear(PipelineTestCase):
    def test_merges_both_model_versions(self) -> None:
        merged = self.year_result["layers"]["Nutzungsflaechen_2025"]
        self.assertEqual(sorted(merged["lnf_code"]), [501, 904, 921, 927])

    def test_lookup_join_reaches_every_feature(self) -> None:
        ln = self.year_result["layers"]["LN_2025"]
        self.assertFalse(ln["Group_Code"].isna().any())

    def test_selection_drops_group_99_only(self) -> None:
        kept = self.year_result["layers"]["LN_2025_sel"]
        dropped = self.year_result["layers"]["LN_2025_overlay_dropped"]
        self.assertEqual(sorted(kept["lnf_code"]), [501, 904, 927])
        self.assertEqual(list(dropped["lnf_code"]), [921])

    def test_class_6_ground_cover_reaches_the_output(self) -> None:
        # The regression the whole group-99 change exists for: code 904, ponds
        # and ditches, is class 6 but not an overlay, and must survive.
        final = self.year_result["layers"]["TLM_LN_2025"]
        self.assertIn(904, list(final["lnf_code"].dropna()))

    def test_erase_removes_the_ln_footprint_from_tlm(self) -> None:
        # TLM_total is 50,000 m2: orchards and vineyards 0..200, forest 200..300,
        # the settlement remnant 300..350, rock 500..600, and the unclassified
        # polygon 900..950. LN covers 0..400, so it erases the first 35,000.
        # What survives is rock (10,000) plus the unclassified polygon (5,000).
        self.assertEqual(self.area("tlm_result", "TLM_total"), 50_000.0)
        self.assertEqual(self.area("year_result", "TLM_erased_2025"), 15_000.0)

    def test_final_area_is_kept_ln_plus_erased_tlm(self) -> None:
        self.assertEqual(
            self.area("year_result", "TLM_LN_2025"),
            self.area("year_result", "LN_2025_sel") + self.area("year_result", "TLM_erased_2025"),
        )

    def test_the_gap_is_measured_not_hidden(self) -> None:
        # The dropped overlay parcel at 200..300 was erased from TLM and not
        # merged back, so it is a hole. 100 x 100 = 10,000 m2.
        gaps = self.year_result["gaps"]
        self.assertAlmostEqual(gaps["gap_area_km2"].iloc[0], 10_000 / 1e6, places=6)
        self.assertAlmostEqual(gaps["gap_share_of_dropped_pct"].iloc[0], 100.0, places=2)

    def test_retired_code_is_reported(self) -> None:
        # 501 is valid to 2022 and this is a 2025 run. The three sentinels in the
        # lookup have to be parsed correctly for this to be detected at all,
        # so this test also covers the encoding and na_values handling.
        report = self.year_result["code_validity"]
        self.assertEqual(list(report["code"]), [501])
        self.assertEqual(report["issue"].iloc[0], "no longer valid")

    def test_overlay_flag_mismatch_is_reported(self) -> None:
        # 927 carries ueberlagernd = 1 but sits in group 60, so the group-99
        # rule keeps it. That is the open question for Katia, and it has to
        # surface every run rather than being resolved silently either way.
        report = self.year_result["overlap_flag_mismatch"]
        self.assertEqual(list(report["ID"]), [927])

    def test_published_layer_carries_one_lowercase_schema(self) -> None:
        # This is the assertion that caught the real defect: Pest_Group and
        # BFF_QI were reaching the output capitalised beside lowercase pest_code.
        final = self.year_result["layers"]["TLM_LN_2025"]
        capitalised = [c for c in final.columns if c != c.lower()]
        self.assertEqual(capitalised, [], f"still capitalised: {capitalised}")

    def test_join_artefacts_do_not_reach_the_published_layer(self) -> None:
        final = self.year_result["layers"]["TLM_LN_2025"]
        for column in classify.JOIN_ARTEFACTS:
            self.assertNotIn(column, final.columns)

    def test_traceability_columns_are_kept(self) -> None:
        final = self.year_result["layers"]["TLM_LN_2025"]
        self.assertIn("lnf_code", final.columns)
        self.assertIn("source", final.columns)
        self.assertEqual(
            sorted(final["source"].dropna().unique()),
            ["LN_2025", "TLM_Bodenbedeckung"],
        )

    def test_reports_are_written_beside_the_data(self) -> None:
        directory = self.year_cfg.output_gpkg.parent / "reports_2025"
        written = {p.name for p in directory.glob("*.csv")}
        self.assertTrue(
            {"areas.csv", "gaps.csv", "code_validity.csv", "overlap_flag_mismatch.csv"} <= written
        )

    def test_manifest_records_the_settings_that_change_results(self) -> None:
        manifest = json.loads(
            self.year_cfg.output_gpkg.with_suffix(".manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["settings"]["overlap_group_code"], "99")
        self.assertEqual(manifest["settings"]["erase_with_selected_only"], "False")
        self.assertEqual(len(manifest["inputs"]), 4)  # two LN inputs, lookup, TLM_total


class TestEraseWithSelectedOnly(unittest.TestCase):
    """The setting that decides whether the output has holes.

    Run separately because it needs a second pass over the same fixture with one
    setting changed, and the point is the difference between the two.
    """

    def test_setting_it_true_closes_the_gap(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="tlm_ln_erase_"))
        try:
            config_path = build_fixture(directory)
            tlm_cfg, _ = config.load(config_path)
            pipeline.prepare_tlm(tlm_cfg, write=True)

            default = pipeline.run_year(config.load_year(config_path, 2025), write=False)

            closed_cfg = config.load_year(config_path, 2025)
            object.__setattr__(closed_cfg, "erase_with_selected_only", True)
            closed = pipeline.run_year(closed_cfg, write=False)

            default_area = default["layers"]["TLM_LN_2025"].geometry.area.sum()
            closed_area = closed["layers"]["TLM_LN_2025"].geometry.area.sum()

            # The hole is the 100 x 100 overlay parcel. Closing it recovers
            # exactly that much, and the gap report agrees.
            self.assertAlmostEqual(closed_area - default_area, 10_000.0, places=6)
            self.assertAlmostEqual(default["gaps"]["gap_area_km2"].iloc[0], 0.01, places=6)
            self.assertAlmostEqual(closed["gaps"]["gap_area_km2"].iloc[0], 0.0, places=6)
        finally:
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
