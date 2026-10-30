from wardlens.models import PatientBundle, PatientSummary, SourceRecord
from wardlens.services.summary import recent_reports


def test_report_index_status_does_not_finalize_constituent_preliminary_reports():
    bundle = PatientBundle(patient=PatientSummary(histno="synthetic"), records=[
        SourceRecord(source_type="report_index", title="Reports", status="final",
                     content="Chest radiograph: final.\nBlood culture: preliminary."),
        SourceRecord(source_type="report", title="Pathology", status="pending", content="Pathology"),
    ])
    assert recent_reports(bundle) == [
        "Chest radiograph: final.", "Blood culture: preliminary.", "Pathology [pending]",
    ]
