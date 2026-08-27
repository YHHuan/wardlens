from __future__ import annotations

from wardlens.emr.demo import DemoEMRAdapter
from wardlens.services.summary import extract_top_todos, render_bundle_overview


def test_demo_contains_more_than_fifteen_patients() -> None:
    adapter = DemoEMRAdapter()
    adapter.login()
    result = adapter.search_patients()
    assert result.complete
    assert result.declared_total == 18
    assert len(result.patients) == 18


def test_demo_bundle_and_todos_are_source_grounded() -> None:
    adapter = DemoEMRAdapter()
    adapter.login()
    patient = adapter.search_patients().patients[0]
    bundle = adapter.fetch_patient_bundle(patient)
    todos = extract_top_todos(bundle)
    overview = render_bundle_overview(bundle)
    assert todos
    assert "follow blood culture" in todos[0]
    assert "來源稽核" in overview
    assert "hash" in overview


def test_demo_can_filter_specific_ids_without_padding() -> None:
    adapter = DemoEMRAdapter()
    adapter.login()
    result = adapter.search_patients(histnos=["90000002", "90000018"])
    assert [patient.histno for patient in result.patients] == ["90000002", "90000018"]
