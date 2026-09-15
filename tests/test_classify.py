"""Classifier tests.

The cases below are the ones that actually matter in production. Several
descriptions are taken verbatim from live permit feeds (Chicago's
``ydr8-5enu``), because synthetic text is much tidier than the real thing
and hides the failure modes.
"""

from __future__ import annotations

import pytest

from dcpermits.classify import Classifier
from dcpermits.models import (
    ROLE_EXPANSION,
    ROLE_FITOUT,
    ROLE_NEW_BUILD,
    ROLE_POWER,
    TIER_CONFIRMED,
    TIER_POSSIBLE,
    TIER_PROBABLE,
    TIER_UNLIKELY,
    Permit,
)


@pytest.fixture(scope="module")
def classifier():
    return Classifier()


def permit(description=None, **kwargs):
    return Permit(source_id="test", jurisdiction="test",
                  description=description, **kwargs)


# ------------------------------------------------------- true positives


@pytest.mark.parametrize("description", [
    # Verbatim from Chicago's permit feed.
    "DDS / 2019 CBRC: REVISION TO PERMIT 101003044 FOR MODIFICATIONS TO "
    "ELECTRICAL EQUIPMENT VOLTAGE AND ASSOCIATED CABLING IN AN EXISTING "
    "FOUR (4) STORY DATA CENTER AS PER PLANS.",
    "NEW CONSTRUCTION OF A HYPERSCALE DATA CENTER",
    "INTERIOR BUILDOUT OF DATA HALL 3",
    "COLOCATION FACILITY EXPANSION",
    "NEW SERVER FARM BUILDING",
    "datacenter shell and core",
])
def test_direct_terms_are_detected(classifier, description):
    result = classifier.classify(permit(description))
    assert result.tier in (TIER_CONFIRMED, TIER_PROBABLE)
    assert result.score > 0


def test_explicit_data_center_alone_reaches_confirmed(classifier):
    # An unambiguous mention should not need a large valuation to qualify.
    result = classifier.classify(permit("TENANT IMPROVEMENT IN EXISTING DATA CENTER"))
    assert result.tier == TIER_CONFIRMED


def test_operator_alone_is_a_lead_not_a_confirmation(classifier):
    """Hyperscalers own retail stores, offices and fiber plant.

    An owner-field match with no other evidence must not assert a data
    center. Live data showed "APPLE INC" on retail and sign permits and
    "Google Fiber" on fiber-vault permits — both scored as confirmed under
    a score-only rule.
    """
    result = classifier.classify(permit(
        "NEW COMMERCIAL BUILDING SHELL", owner="Equinix LLC"))
    assert result.operator == "Equinix"
    assert result.tier == TIER_POSSIBLE


def test_operator_with_corroboration_reaches_probable(classifier):
    result = classifier.classify(permit(
        "NEW BUILDING SHELL WITH 8 GENERATORS AND SWITCHGEAR",
        owner="Equinix LLC", valuation=210_000_000))
    assert result.tier == TIER_PROBABLE


def test_operator_alone_never_reaches_confirmed_even_at_scale(classifier):
    result = classifier.classify(permit(
        "NEW RETAIL STORE", owner="APPLE INC",
        valuation=400_000_000, square_feet=600_000))
    assert result.tier != TIER_CONFIRMED


@pytest.mark.parametrize("description", [
    "New 353 SF utility equipment shelter compound to support Google Fiber's "
    "construction of a fiber network",
    "Google fiber AUS161 install fiber hut gas generator vaults new power fiber",
    "Google Fiber fence and driveway expansion and relocation of h-frame for power",
    "Install small cell antenna array on existing monopole",
])
def test_telecom_outside_plant_is_vetoed(classifier, description):
    """Verbatim false positives from a live Austin harvest."""
    result = classifier.classify(permit(description))
    assert result.tier == TIER_UNLIKELY, result.signals


def test_shell_entity_detected_but_scored_below_direct_name(classifier):
    shell = classifier.classify(permit("NEW BUILDING SHELL", owner="VADATA INC"))
    direct = classifier.classify(permit(
        "NEW BUILDING SHELL", owner="Amazon Web Services Inc"))
    assert shell.operator == "Amazon Web Services"
    assert shell.operator_match == "vadata"
    # A reported shell is a lead, not a fact — it must rank lower.
    assert shell.score < direct.score


def test_scale_amplifies_but_only_with_an_anchor(classifier):
    small = classifier.classify(permit("NEW DATA CENTER", valuation=500_000))
    large = classifier.classify(permit("NEW DATA CENTER", valuation=400_000_000))
    assert large.score > small.score


# ------------------------------------------------------ false positives


@pytest.mark.parametrize("description", [
    "INSTALL VOICE AND DATA CABLING AND DATA OUTLETS FOR OFFICE TENANT",
    "LOW VOLTAGE DATA WIRING, DATA JACKS AND DATA DROPS THROUGHOUT",
    "TELE/DATA ROUGH-IN FOR 4TH FLOOR",
    "RUN DATA CONDUIT TO NEW WORKSTATIONS",
])
def test_low_voltage_data_cabling_is_vetoed(classifier, description):
    """The dominant false-positive class in real permit text."""
    result = classifier.classify(permit(description, valuation=250_000))
    assert result.tier == TIER_UNLIKELY
    assert result.vetoed
    assert result.score == 0


def test_call_center_is_vetoed(classifier):
    result = classifier.classify(permit("TENANT IMPROVEMENT FOR NEW CALL CENTER"))
    assert result.tier == TIER_UNLIKELY


@pytest.mark.parametrize("description", [
    "NEW 6 STORY HOSPITAL WITH CENTRAL PLANT, CHILLERS, COOLING TOWERS, "
    "EMERGENCY GENERATORS AND SWITCHGEAR",
    "NEW ARENA WITH BACKUP GENERATORS, SWITCHGEAR AND CHILLED WATER PLANT",
    "NEW 1,000,000 SF DISTRIBUTION CENTER WITH GENERATORS AND TRANSFORMERS",
    "NEW HIGH SCHOOL WITH EMERGENCY GENERATOR AND CHILLER PLANT",
])
def test_large_non_datacenter_buildings_are_rejected(classifier, description):
    """Scale plus heavy MEP scope must not imply a data center.

    This is the failure mode that makes naive keyword-plus-valuation
    scoring useless: a hospital central plant looks exactly like a data
    hall if you only read the equipment list.
    """
    result = classifier.classify(
        permit(description, valuation=420_000_000, square_feet=600_000))
    assert result.tier == TIER_UNLIKELY, result.signals


def test_residential_noise_is_rejected(classifier):
    result = classifier.classify(permit(
        "SINGLE FAMILY DWELLING REROOF AND WATER HEATER REPLACEMENT",
        valuation=15_000))
    assert result.tier == TIER_UNLIKELY


def test_valuation_alone_never_qualifies(classifier):
    result = classifier.classify(permit(
        "NEW COMMERCIAL BUILDING", valuation=900_000_000, square_feet=2_000_000))
    assert result.tier == TIER_UNLIKELY


# ------------------------------------------------------- mixed / nuanced


def test_veto_is_overridden_by_a_direct_term(classifier):
    """"Data cabling in the new data center" is still a data-center permit."""
    result = classifier.classify(permit(
        "INSTALL DATA CABLING IN THE NEW DATA CENTER WHITE SPACE"))
    assert not result.vetoed
    assert result.tier in (TIER_CONFIRMED, TIER_PROBABLE)


def test_supporting_signals_alone_reach_only_possible(classifier):
    """No named anchor means the verdict must stay hedged."""
    result = classifier.classify(permit(
        "INSTALL 8 MW OF GENERATORS, N+1 SWITCHGEAR, CRAH UNITS AND RAISED FLOOR",
        valuation=60_000_000))
    assert result.tier == TIER_POSSIBLE


def test_single_supporting_signal_is_noise(classifier):
    result = classifier.classify(permit("REPLACE ROOFTOP TRANSFORMER"))
    assert result.tier == TIER_UNLIKELY


def test_datacenter_inside_another_building_type_still_counts(classifier):
    result = classifier.classify(permit(
        "HOSPITAL DATA CENTER BUILD OUT WITH CRAH UNITS AND UPS SYSTEM",
        valuation=8_000_000))
    assert result.tier in (TIER_PROBABLE, TIER_CONFIRMED)


# ------------------------------------------------------------------ roles


@pytest.mark.parametrize("description,expected", [
    ("NEW CONSTRUCTION OF DATA CENTER BUILDING SHELL", ROLE_NEW_BUILD),
    ("DATA CENTER EXPANSION PHASE 3", ROLE_EXPANSION),
    ("TENANT IMPROVEMENT FOR DATA HALL", ROLE_FITOUT),
    ("NEW ELECTRICAL SUBSTATION FOR DATA CENTER CAMPUS", ROLE_POWER),
])
def test_role_classification(classifier, description, expected):
    assert classifier.classify(permit(description)).role == expected


def test_new_build_outranks_fitout_when_both_appear(classifier):
    # Growth analysis depends on this ordering: a ground-up build that
    # also mentions interior work is still a new build.
    result = classifier.classify(permit(
        "NEW CONSTRUCTION OF DATA CENTER WITH INTERIOR REMODEL OF OFFICE AREA"))
    assert result.role == ROLE_NEW_BUILD


# --------------------------------------------------------------- plumbing


def test_word_boundaries_prevent_substring_matches(classifier):
    # "aws" is an Amazon alias; it must not fire inside "lawsuit".
    result = classifier.classify(permit("REPAIRS FOLLOWING LAWSUIT SETTLEMENT"))
    assert result.operator is None


def test_apply_writes_results_onto_the_permit(classifier):
    subject = permit("NEW HYPERSCALE DATA CENTER", valuation=300_000_000)
    classifier.apply(subject)
    assert subject.dc_tier == TIER_CONFIRMED
    assert subject.dc_score > 0
    assert subject.dc_signals
    assert subject.dc_role == "other" or subject.dc_role


def test_keywords_are_nonempty_and_deduped(classifier):
    keywords = classifier.keywords()
    assert "data center" in keywords
    assert len(keywords) == len(set(keywords))


def test_owner_and_applicant_fields_are_searched(classifier):
    # Operator names live in owner/applicant, not the description.
    result = classifier.classify(
        permit("NEW BUILDING", applicant="CyrusOne LLC"))
    assert result.operator == "CyrusOne"


def test_empty_permit_does_not_crash(classifier):
    result = classifier.classify(permit(None))
    assert result.tier == TIER_UNLIKELY
    assert result.score == 0
