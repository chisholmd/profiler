"""Context-aware XPath canonicalization for HL7 CDA / C-CDA documents."""

from __future__ import annotations

from typing import Dict, Optional

# Well-known HL7 C-CDA Section LOINC codes -> readable labels
KNOWN_LOINC_SECTIONS: Dict[str, str] = {
    "48765-2": "Allergies",
    "10160-0": "Medications",
    "11450-4": "Problems",
    "47519-4": "Procedures",
    "30954-2": "Results",
    "8716-3": "VitalSigns",
    "46240-8": "Encounters",
    "11369-6": "Immunizations",
    "29762-2": "SocialHistory",
    "18776-5": "PlanOfTreatment",
    "42348-3": "AdvanceDirectives",
    "10157-6": "FamilyHistory",
    "47420-5": "FunctionalStatus",
    "10164-2": "HistoryOfPresentIllness",
    "8648-8": "HospitalCourse",
    "29299-5": "ReasonForVisit",
    "10187-3": "ReviewOfSystems",
    "29545-1": "PhysicalExam",
    "51847-2": "AssessmentAndPlan",
}

# Well-known C-CDA Clinical Statement templateId roots -> readable labels
KNOWN_TEMPLATE_IDS: Dict[str, str] = {
    "2.16.840.1.113883.10.20.22.4.27": "VitalSignObs",
    "2.16.840.1.113883.10.20.22.4.26": "VitalSignsOrganizer",
    "2.16.840.1.113883.10.20.22.4.2": "ResultObs",
    "2.16.840.1.113883.10.20.22.4.1": "ResultOrganizer",
    "2.16.840.1.113883.10.20.22.4.4": "ProblemObs",
    "2.16.840.1.113883.10.20.22.4.3": "ProblemAct",
    "2.16.840.1.113883.10.20.22.4.7": "AllergyObs",
    "2.16.840.1.113883.10.20.22.4.30": "AllergyAct",
    "2.16.840.1.113883.10.20.22.4.16": "MedicationActivity",
    "2.16.840.1.113883.10.20.22.4.52": "ImmunizationActivity",
    "2.16.840.1.113883.10.20.22.4.14": "ProcedureActivityProc",
    "2.16.840.1.113883.10.20.22.4.49": "EncounterActivity",
    "2.16.840.1.113883.10.20.22.4.78": "SmokingStatusObs",
}

# Elements that can be anchored by templateId when anchor_templates=True
ANCHORABLE_CLINICAL_STATEMENTS = {
    "observation",
    "act",
    "substanceAdministration",
    "procedure",
    "encounter",
    "organizer",
    "supply",
}


def strip_namespace(tag_or_attr: str) -> str:
    """
    Strip XML namespace URI in Clark notation '{uri}local' and normalize known prefixes.
    Preserves standard distinguishing prefixes like 'sdtc:' or 'xsi:'.
    """
    if not tag_or_attr.startswith("{"):
        return tag_or_attr

    uri_end = tag_or_attr.find("}")
    if uri_end == -1:
        return tag_or_attr

    uri = tag_or_attr[1:uri_end]
    local = tag_or_attr[uri_end + 1 :]

    if "sdtc" in uri.lower():
        return f"sdtc:{local}"
    if "xmlschema-instance" in uri.lower():
        return f"xsi:{local}"
    return local


class CanonicalPathBuilder:
    """Builds normalized, optionally template/section-anchored canonical XPaths."""

    def __init__(self, anchor_templates: bool = True) -> None:
        self.anchor_templates = anchor_templates

    def format_segment(
        self,
        local_tag: str,
        section_code: Optional[str] = None,
        template_root: Optional[str] = None,
    ) -> str:
        """Format a single path segment with optional section or template qualifier."""
        if not self.anchor_templates:
            return local_tag

        if local_tag == "section":
            if section_code:
                label = KNOWN_LOINC_SECTIONS.get(section_code)
                if label:
                    return f"section[{label}:{section_code}]"
                return f"section[code={section_code}]"
            if template_root:
                return f"section[tpl={template_root}]"

        if local_tag in ANCHORABLE_CLINICAL_STATEMENTS and template_root:
            label = KNOWN_TEMPLATE_IDS.get(template_root)
            if label:
                return f"{local_tag}[{label}]"
            return f"{local_tag}[tpl={template_root}]"

        return local_tag
