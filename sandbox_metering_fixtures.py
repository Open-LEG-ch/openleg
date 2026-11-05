# SPDX-License-Identifier: AGPL-3.0-or-later
"""Invented, independently specified September evidence for Sonnwil."""

import gzip
from functools import lru_cache

COMMUNITY = "sonnwil-operating-leg"
EXTERNAL_COMMUNITY = "SONNWIL-SIMULATION-LEG"
TERRITORY = "zurich"
START = "2026-08-31T22:00:00Z"
END = "2026-09-30T22:00:00Z"
INTERVALS = 2880
POINTS = (
    ("SONNWIL-SIM-C1", "sonnwil-resident", "consumption"),
    ("SONNWIL-SIM-C2", "sonnwil-producer", "consumption"),
    ("SONNWIL-SIM-C3", "sonnwil-operator", "consumption"),
    ("SONNWIL-SIM-C4", "sonnwil-neighbour", "consumption"),
    ("SONNWIL-SIM-P1", "sonnwil-producer", "production"),
)
KINDS = ("valid", "duplicate", "corrected", "missing", "unmapped", "precision")


def seed_metering(db):
    """Register the operating fixture's dated assignments through normal commands."""
    import vnb_calculated_values

    points = [
        {
            "metering_point_id": point,
            "community_id": COMMUNITY,
            "building_id": participant,
            "vnb_community_id": EXTERNAL_COMMUNITY,
            "expected_directions": [direction],
            "alias": "SIMULATION SONNWIL " + point,
        }
        for point, participant, direction in POINTS
    ]
    if db.upsert_metering_points(points) != len(points):
        raise RuntimeError("synthetic metering registry unavailable")
    for point, _participant, _direction in POINTS:
        vnb_calculated_values.record_meter_assignment(
            TERRITORY,
            point,
            {
                "confirmed_membership": True,
                "effective_from": START,
                "effective_until": END,
                "source_reference": "SIMULATION-SONNWIL-SEPTEMBER-2026",
            },
        )


@lru_cache(maxsize=len(KINDS))
def fixture(kind):
    """Return a deterministic gzip envelope; its contents remain ordinary E66 XML."""
    if kind not in KINDS:
        raise ValueError("unknown fixture")
    if kind == "duplicate":
        return fixture("valid")
    corrected = kind == "corrected"
    identifier = "SONNWIL-SIM-" + kind.upper()
    blocks = []
    for point, _participant, direction in POINTS:
        if kind == "unmapped" and point == POINTS[0][0]:
            point = "SONNWIL-SIM-UNMAPPED"
        local = "0.150" if corrected else "0.125"
        values = (
            (
                "8716867000030",
                "0.250"
                if direction == "consumption"
                else ("0.600" if corrected else "0.500"),
            ),
            (
                "2404050010123",
                local
                if direction == "consumption"
                else ("0.600" if corrected else "0.500"),
            ),
            (
                "2404050010124",
                ("0.100" if corrected else "0.125")
                if direction == "consumption"
                else "0.000",
            ),
        )
        for code, value in values:
            count = INTERVALS - (kind == "missing" and not blocks)
            observations = "".join(
                "<Observation><Position><Sequence>"
                + str(index)
                + "</Sequence></Position><Volume>"
                + (
                    "0.2501"
                    if kind == "precision" and not blocks and index == 1
                    else value
                )
                + "</Volume></Observation>"
                for index in range(1, count + 1)
            )
            tag = (
                "ConsumptionMeteringPoint"
                if direction == "consumption"
                else "ProductionMeteringPoint"
            )
            blocks.append(
                f"<MeteringData><Community><CommunityID>{EXTERNAL_COMMUNITY}</CommunityID></Community>"
                f"<{tag}><VSENationalID>{point}</VSENationalID></{tag}>"
                f"<Product><ID><VSENationalCode>{code}</VSENationalCode></ID><MeasureUnit>KWH</MeasureUnit></Product>"
                f"<Interval><StartDateTime>{START}</StartDateTime><EndDateTime>{END}</EndDateTime></Interval>"
                "<Resolution><Resolution>15</Resolution><Unit>MIN</Unit></Resolution>"
                + observations
                + "</MeteringData>"
            )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?><!-- SIMULATION SONNWIL: entirely fictional flat profiles, no pilot data. -->'
        "<ValidatedMeteredData_16><ValidatedMeteredData_HeaderInformation>"
        f"<InstanceDocument><DocumentID>{identifier}</DocumentID><DocumentType><ebIXCode>E66</ebIXCode></DocumentType>"
        f"<Status>{'5' if corrected else '9'}</Status><Creation>2026-10-02T04:00:00Z</Creation></InstanceDocument>"
        "<Sender><ID><EICID>SONNWIL-SIMULATION-VNB</EICID></ID></Sender>"
        "<Receiver><ID><EICID>SONNWIL-SIMULATION-CEM</EICID></ID></Receiver>"
        f"<BusinessScopeProcess><ReportPeriod><StartDateTime>{START}</StartDateTime><EndDateTime>{END}</EndDateTime>"
        "</ReportPeriod></BusinessScopeProcess></ValidatedMeteredData_HeaderInformation>"
        + "".join(blocks)
        + "</ValidatedMeteredData_16>"
    )
    return gzip.compress(xml.encode(), mtime=0)
