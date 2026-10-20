"""Unit and end-to-end tests for the CDA Statistical Data Profiler."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from cda_profiler.cli import main, profile_folder
from cda_profiler.config import ProfilerConfig
from cda_profiler.inference.classifiers import (
    classify_value,
    compute_pattern_mask,
    is_disguised_null,
    validate_xsi_type_compatibility,
)
from cda_profiler.models.profile import AnomalyCategory, InferredType


def _make_cda_xml(
    doc_id: str,
    mrn: str = "100200300",
    given_name: str = "Alice",
    birth_time_attr: str = 'value="19800512"',
    postal_code: str = "02115",
    vital_val_attr: str = 'xsi:type="PQ" value="98.6" unit="degF"',
    include_telecom: bool = True,
    telecom_attr: str = 'value="tel:+1-555-234-5678"',
) -> str:
    telecom_xml = f"<telecom {telecom_attr}/>" if include_telecom else ""
    if postal_code is None:
        postal_xml = ""
    elif postal_code == "EMPTY_TAG":
        postal_xml = "<postalCode></postalCode>"
    else:
        postal_xml = f"<postalCode>{postal_code}</postalCode>"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ClinicalDocument xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:sdtc="urn:hl7-org:sdtc">
  <typeId root="2.16.840.1.113883.1.3" extension="POCD_HD000040"/>
  <id root="2.16.840.1.113883.19.5" extension="{doc_id}"/>
  <effectiveTime value="20251015143000-0400"/>
  <recordTarget>
    <patientRole>
      <id root="2.16.840.1.113883.19.5.1" extension="{mrn}"/>
      <addr>
        <streetAddressLine>100 Healthcare Way</streetAddressLine>
        <city>Boston</city>
        <state>MA</state>
        {postal_xml}
      </addr>
      {telecom_xml}
      <patient>
        <name>
          <given>{given_name}</given>
          <family>Smith</family>
        </name>
        <administrativeGenderCode code="F" codeSystem="2.16.840.1.113883.5.1"/>
        <birthTime {birth_time_attr}/>
      </patient>
    </patientRole>
  </recordTarget>
  <component>
    <structuredBody>
      <component>
        <section>
          <templateId root="2.16.840.1.113883.10.20.22.2.4.1"/>
          <code code="8716-3" codeSystem="2.16.840.1.113883.6.1" displayName="Vital Signs"/>
          <title>Vital Signs</title>
          <entry>
            <organizer classCode="CLUSTER" moodCode="EVN">
              <templateId root="2.16.840.1.113883.10.20.22.4.26"/>
              <component>
                <observation classCode="OBS" moodCode="EVN">
                  <templateId root="2.16.840.1.113883.10.20.22.4.27"/>
                  <code code="8310-5" codeSystem="2.16.840.1.113883.6.1" displayName="Body temperature"/>
                  <effectiveTime value="20251015"/>
                  <value {vital_val_attr}/>
                </observation>
              </component>
            </organizer>
          </entry>
        </section>
      </component>
    </structuredBody>
  </component>
</ClinicalDocument>
"""


class TestClassifiers(unittest.TestCase):
    def test_primitive_type_classification(self) -> None:
        self.assertEqual(
            classify_value("42", "/obs/value/@value", "INT").inferred_type,
            InferredType.INTEGER,
        )
        self.assertEqual(
            classify_value("98.6", "/obs/value/@value", "PQ").inferred_type,
            InferredType.FLOAT,
        )
        self.assertEqual(
            classify_value("19800512", "/patient/birthTime/@value").inferred_type,
            InferredType.HL7_DATETIME,
        )
        self.assertTrue(
            classify_value("19800512", "/patient/birthTime/@value").is_valid_date
        )
        # Invalid Feb 30 date on birthTime path
        invalid_ts = classify_value("19750230", "/patient/birthTime/@value")
        self.assertEqual(invalid_ts.inferred_type, InferredType.HL7_DATETIME)
        self.assertFalse(invalid_ts.is_valid_date)

        # ISO-8601 date
        iso_res = classify_value("1980-05-12", "/patient/birthTime/@value")
        self.assertEqual(iso_res.inferred_type, InferredType.ISO_DATETIME)

        # OID and UUID
        self.assertEqual(
            classify_value("2.16.840.1.113883.10.20.22.4.27").inferred_type,
            InferredType.OID,
        )
        self.assertEqual(
            classify_value("urn:uuid:f81d4fae-7dec-11d0-a765-00a0c91e6bf6").inferred_type,
            InferredType.UUID,
        )

    def test_pattern_mask_and_disguised_null(self) -> None:
        self.assertEqual(compute_pattern_mask("02115-1234"), "D{5}-D{4}")
        self.assertTrue(is_disguised_null("N/A", {"n/a", "unknown"}))
        self.assertFalse(is_disguised_null("Boston", {"n/a", "unknown"}))

    def test_xsi_type_conflict(self) -> None:
        err = validate_xsi_type_compatibility("PQ", "/obs/value/@value", InferredType.STRING)
        self.assertIsNotNone(err)
        ok = validate_xsi_type_compatibility("PQ", "/obs/value/@value", InferredType.FLOAT)
        self.assertIsNone(ok)


class TestEndToEndProfiler(unittest.TestCase):
    def test_folder_profiling_and_anomaly_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            corpus_dir = Path(tmp_dir) / "cdas"
            out_dir = Path(tmp_dir) / "output"
            corpus_dir.mkdir()

            # Generate 20 CDA XML documents:
            # - 17 fully conforming documents
            # - Doc 18: non-conforming string ("120/80") in PQ float field, ISO date ("1980-05-12") in birthTime,
            #           disguised null ("N/A") in given name, missing telecom
            # - Doc 19: invalid calendar date ("19750230") in birthTime, nullFlavor="NI" on vital sign value,
            #           empty tag on postalCode
            # - Doc 20: extreme numeric outlier ("986.0") in body temp, pattern outlier ("ABCDE") in postalCode,
            #           nullFlavor="UNK" on birthTime
            for i in range(1, 18):
                xml = _make_cda_xml(
                    doc_id=f"DOC-{i:03d}",
                    mrn=f"{100000000 + i}",
                    birth_time_attr=f'value="198{i % 9}0512"',
                    postal_code=f"021{10 + i:02d}",
                    vital_val_attr=f'xsi:type="PQ" value="{98.0 + (i * 0.1):.1f}" unit="degF"',
                )
                (corpus_dir / f"cda_{i:02d}.xml").write_text(xml, encoding="utf-8")

            # Doc 18
            (corpus_dir / "cda_18.xml").write_text(
                _make_cda_xml(
                    doc_id="DOC-018",
                    given_name="N/A",
                    birth_time_attr='value="1980-05-12"',
                    vital_val_attr='xsi:type="PQ" value="120/80" unit="mmHg"',
                    include_telecom=False,
                ),
                encoding="utf-8",
            )

            # Doc 19
            (corpus_dir / "cda_19.xml").write_text(
                _make_cda_xml(
                    doc_id="DOC-019",
                    birth_time_attr='value="19750230"',
                    postal_code="EMPTY_TAG",
                    vital_val_attr='xsi:type="PQ" nullFlavor="NI"',
                ),
                encoding="utf-8",
            )

            # Doc 20
            (corpus_dir / "cda_20.xml").write_text(
                _make_cda_xml(
                    doc_id="DOC-020",
                    birth_time_attr='nullFlavor="UNK"',
                    postal_code="ABC-POSTAL",
                    vital_val_attr='xsi:type="PQ" value="986.0" unit="degF"',
                ),
                encoding="utf-8",
            )

            profile = profile_folder(corpus_dir, ProfilerConfig())
            self.assertEqual(profile.total_documents_parsed, 20)

            by_path = {f.canonical_path: f for f in profile.field_profiles}

            # 1. Check birthTime/@value:
            # Present in 19/20 docs (Doc 20 has birthTime/@nullFlavor="UNK")
            # Populated in 19 docs: 18 HL7_DATETIME (one of which is invalid 19750230), 1 ISO_DATETIME (1980-05-12)
            bt = by_path["/ClinicalDocument/recordTarget/patientRole/patient/birthTime/@value"]
            self.assertEqual(bt.docs_present, 19)
            self.assertEqual(bt.docs_missing, 1)
            self.assertEqual(bt.null_flavor_breakdown.get("UNK"), 1)
            self.assertEqual(bt.apparent_type, "HL7_DATETIME")
            self.assertEqual(bt.invalid_date_count, 1)
            self.assertGreaterEqual(bt.non_conforming_count, 2)

            # 2. Check VitalSigns observation value/@value:
            # Anchored path includes section[VitalSigns:8716-3] and observation[VitalSignObs]
            vs_path = (
                "/ClinicalDocument/component/structuredBody/component/"
                "section[VitalSigns:8716-3]/entry/organizer[VitalSignsOrganizer]/"
                "component/observation[VitalSignObs]/value/@value"
            )
            self.assertIn(vs_path, by_path)
            vs = by_path[vs_path]
            self.assertEqual(vs.apparent_type, "FLOAT")
            self.assertEqual(vs.null_flavor_breakdown.get("NI"), 1)
            self.assertEqual(vs.xsi_conflict_count, 1)
            categories = {a.category for a in vs.anomalies}
            self.assertIn(AnomalyCategory.TYPE_MISMATCH, categories)
            self.assertIn(AnomalyCategory.XSI_TYPE_CONFLICT, categories)
            self.assertIn(AnomalyCategory.NUMERIC_OUTLIER, categories)

            # 3. Check given name disguised null ("N/A")
            given = by_path["/ClinicalDocument/recordTarget/patientRole/patient/name/given"]
            self.assertEqual(given.disguised_null_count, 1)

            # 4. Check postalCode empty tag & pattern outlier ("ABC-POSTAL" vs "D{5}")
            postal = by_path["/ClinicalDocument/recordTarget/patientRole/addr/postalCode"]
            self.assertEqual(postal.empty_count, 1)
            self.assertEqual(postal.dominant_pattern_mask, "D{5}")
            postal_cats = {a.category for a in postal.anomalies}
            self.assertIn(AnomalyCategory.EMPTY_WITHOUT_NULLFLAVOR, postal_cats)
            self.assertIn(AnomalyCategory.PATTERN_OUTLIER, postal_cats)

            # 5. Test CLI report generation
            rc = main([str(corpus_dir), "-o", str(out_dir), "--quiet"])
            self.assertEqual(rc, 0)
            self.assertTrue((out_dir / "cda_profile_report.html").exists())
            self.assertTrue((out_dir / "cda_field_profile.csv").exists())
            self.assertTrue((out_dir / "cda_anomalies.csv").exists())
            self.assertTrue((out_dir / "cda_profile_summary.json").exists())


if __name__ == "__main__":
    unittest.main()
