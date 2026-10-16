from datetime import datetime, timedelta, timezone

import pytest

from wardlens.emr.vgh import VGHReadOnlyAdapter
from wardlens.models import PatientBundle, PatientSummary, SourceRecord
from wardlens.services.summary import render_bundle_overview

TAIWAN = timezone(timedelta(hours=8))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DOB 039/01/02", (1950, 1, 2)),
        ("DOB 39-1-2", (1950, 1, 2)),
        ("DOB 1950/01/02", (1950, 1, 2)),
        ("058/12/31", (1969, 12, 31)),
        ("059/01/01", (1970, 1, 1)),
        ("115/10/16", (2026, 10, 16)),
        ("2026-10-16", (2026, 10, 16)),
        ("113/02/29", (2024, 2, 29)),
        ("115/99/99 then 115/10/16", (2026, 10, 16)),
        ("2026/02/30 then 2026/10/16", (2026, 10, 16)),
    ],
)
def test_hospital_dates_are_windows_safe_and_host_timezone_independent(text, expected) -> None:
    observed = VGHReadOnlyAdapter._first_date(text)
    assert observed is not None
    assert (observed.year, observed.month, observed.day) == expected
    assert observed.utcoffset() == timedelta(hours=8)


@pytest.mark.parametrize("text", ["No date", "115/02/30", "000/01/01", "12345/01/01"])
def test_invalid_dates_do_not_create_observation_times(text) -> None:
    assert VGHReadOnlyAdapter._first_date(text) is None


def test_historical_observation_can_be_rendered_without_host_localtime() -> None:
    class NoHostLocaltime(datetime):
        def astimezone(self, tz=None):
            if tz is None:
                raise OSError(22, "Synthetic Windows negative timestamp failure")
            return super().astimezone(tz)

    bundle = PatientBundle(patient=PatientSummary(histno="90000001"))
    bundle.add_record(
        SourceRecord(
            source_type="progress_note",
            title="Synthetic historical source",
            content="Synthetic content",
            observed_at=NoHostLocaltime(1950, 1, 2, tzinfo=TAIWAN),
        )
    )
    assert "1950-01-02" in render_bundle_overview(bundle)
