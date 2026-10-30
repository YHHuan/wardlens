import json
from datetime import datetime

from wardlens.models import EMR_TIMEZONE, PatientBundle, PatientSummary, SourceRecord
from wardlens.services.rounding import build_rounding_view
from wardlens.services.summary import admission_top_items, extract_top_todos, render_bundle_overview


def bundle_of(content, source_type="lab_chemistry"):
    return PatientBundle(
        PatientSummary(histno="90000001"),
        records=[SourceRecord(source_type, "Synthetic table", content)],
        fetched_at=datetime(2026, 10, 30, 12, tzinfo=EMR_TIMEZONE),
    )


def test_crp_uneven_40_day_span_keeps_actual_dates_and_source_values():
    bundle = bundle_of("Date\tCRP (mg/dL)\n2026-10-30\t1.5\n2026-09-20\t12.0\n2026-10-29\t4.0")
    view = build_rounding_view(bundle)
    row = view["trends"][0]
    assert row["label"] == "CRP" and row["unit"] == "mg/dL"
    assert [point["value"] for point in row["values"]] == ["12.0", "4.0", "1.5"]
    times = [datetime.fromisoformat(point["timestamp"]) for point in row["values"]]
    assert (times[1] - times[0]).days == 39
    assert (times[2] - times[1]).days == 1
    assert row["span"] == "跨 40 天" and row["chartable"]
    assert row["values"][0]["time"] == "2026-09-20"
    assert row["values"][0]["time_precision"] == "day"
    assert view["sources"][0]["content"] == bundle.records[0].content
    assert "改善" not in json.dumps(view, ensure_ascii=False)


def test_cbc_core_order_and_same_day_hb_preserve_time_without_differential():
    view = build_rounding_view(bundle_of(
        "日期\t時間\tPLT (10^3/uL)\tHGB (g/dL)\tWBC (10^3/uL)\n"
        "115/10/30\t18:00\t210\t9.1\t8.2\n115/10/30\t06:00\t215\t9.8\t8.1", "lab_cbc",
    ))
    assert [row["label"] for row in view["trends"]] == ["WBC", "Hb", "PLT"]
    assert all(row["group"] == "cbc" for row in view["trends"])
    hb = view["trends"][1]
    assert [point["value"] for point in hb["values"]] == ["9.8", "9.1"]
    assert [point["time"] for point in hb["values"]] == ["115/10/30 06:00", "115/10/30 18:00"]
    assert hb["span"] == "跨 12 小時" and hb["chartable"]
    assert all(point["time_precision"] == "minute" for point in hb["values"])


def test_differential_is_separate_from_core_cbc_and_unit_row_is_respected():
    view = build_rounding_view(bundle_of(
        "Date\tSEG\tWBC\tHGB\tPLT\nUnit\t%\t10^3/uL\tg/dL\t10^3/uL\n"
        "2026-10-30\t80\t12.1\t10.8\t240", "lab_cbc",
    ))
    assert [row["label"] for row in view["trends"]] == ["WBC", "Hb", "PLT", "SEG"]
    assert view["trends"][-1]["group"] == "differential"
    assert view["trends"][-1]["unit"] == "%"
    assert all(row["point_count"] == 1 for row in view["trends"])


def test_different_units_are_never_merged_or_converted():
    view = build_rounding_view(bundle_of(
        "Date\tTest\tValue\tUnit\n2026-10-29\tCRP\t2.0\tmg/dL\n"
        "2026-10-30\tCRP\t10.0\tmg/L",
    ))
    assert len(view["trends"]) == 2
    assert {row["unit"] for row in view["trends"]} == {"mg/dL", "mg/L"}
    assert all(not row["chartable"] for row in view["trends"])
    assert {row["values"][0]["value"] for row in view["trends"]} == {"2.0", "10.0"}


def test_comparators_and_missing_values_remain_text_and_disable_chart():
    view = build_rounding_view(bundle_of(
        "Date\tCRP (mg/dL)\n2026-10-28\t<0.1\n2026-10-29\t-\n2026-10-30\t0.2",
    ))
    row = view["trends"][0]
    assert [point["value"] for point in row["values"]] == ["<0.1", "-", "0.2"]
    assert [point["numeric"] for point in row["values"]] == [None, None, 0.2]
    assert not row["chartable"]
    assert "比較符" in row["note"]


def test_unknown_dates_are_not_replaced_by_fetch_time_or_restored_from_day_codes():
    view = build_rounding_view(bundle_of("Date\tCRP (mg/dL)\nD-40\t2.0\nunknown\t1.5"))
    row = view["trends"][0]
    assert [point["time"] for point in row["values"]] == ["D-40", "unknown"]
    assert all(point["timestamp"] == "" for point in row["values"])
    assert not row["chartable"] and row["span"] == "時間未建立"
    assert "時間未辨識" in row["note"]


def test_transposed_table_preserves_merged_date_clock_headers():
    view = build_rounding_view(bundle_of(
        "Test\tUnit\t2026/10/29\t2026/10/29\t2026/10/30\n"
        "Test\tUnit\t18:00\t06:00\t09:00\n"
        "Creatinine\tmg/dL\t1.1\t1.0\t1.8",
    ))
    row = view["trends"][0]
    assert row["label"] == "Cr" and row["chartable"]
    assert [point["value"] for point in row["values"]] == ["1.0", "1.1", "1.8"]
    assert [point["time"] for point in row["values"]] == ["2026/10/29 06:00", "2026/10/29 18:00", "2026/10/30 09:00"]


def test_invalid_table_alignment_falls_back_to_original_with_warning():
    text = "Date\tWBC\tHb\n2026-10-30\t8.2"
    view = build_rounding_view(bundle_of(text, "lab_cbc"))
    assert not view["trends"]
    assert view["sources"][0]["content"] == text
    assert any("欄數不符" in warning for warning in view["warnings"])


def test_narrative_labs_remain_readable_without_fabricated_points():
    text = "Synthetic note: CRP was lower, but values were not provided."
    view = build_rounding_view(bundle_of(text))
    assert not view["trends"]
    assert view["sources"][0]["content"] == text
    assert any("未辨識可靠" in warning for warning in view["warnings"])


def test_unit_is_not_inferred_from_familiar_test_name():
    view = build_rounding_view(bundle_of("Date\tCRP\n2026-10-29\t8.0\n2026-10-30\t4.0"))
    row = view["trends"][0]
    assert row["unit"] == "" and "單位未提供" in row["note"]
    assert not row["chartable"]


def test_cell_explicit_units_are_retained_without_guessing():
    view = build_rounding_view(bundle_of("Date\tCreatinine\n2026-10-29\t1.0 mg/dL\n2026-10-30\t1.1 mg/dL"))
    row = view["trends"][0]
    assert row["unit"] == "mg/dL" and row["chartable"]
    assert row["values"][-1]["value"] == "1.1 mg/dL"


def test_five_point_preview_retains_all_observations_and_is_json_safe():
    text = "Date\tCRP (mg/L)\n" + "\n".join(f"2026-10-{day:02}\t{day}.0" for day in range(1, 10))
    view = build_rounding_view(bundle_of(text))
    row = view["trends"][0]
    assert len(row["values"]) == 5
    assert len(row["all_values"]) == row["point_count"] == 9
    assert row["values"][0]["value"] == "5.0"
    assert "5／9" in row["note"]
    json.dumps(view, ensure_ascii=False, allow_nan=False)


def test_same_timestamp_conflict_is_visible_not_arbitrarily_deduplicated():
    view = build_rounding_view(bundle_of("Date\tHGB (g/dL)\n2026-10-30 08:00\t8.0\n2026-10-30 08:00\t10.0", "lab_cbc"))
    row = view["trends"][0]
    assert row["point_count"] == 2 and not row["chartable"]
    assert "不同數值" in row["note"] and "核對量測時間" in row["note"]


def test_vitals_keep_original_times_and_do_not_invent_missing_fields():
    view = build_rounding_view(bundle_of(
        "日期／時間\tBP mmHg\tHR bpm\n115/10/29 06:00\t120/80\t80\n115/10/30 06:00\t118/78\t84",
        "vital_signs",
    ))
    assert not view["trends"]
    assert [row["label"] for row in view["vitals"]] == ["BP", "HR"]
    assert view["vitals"][0]["values"][-1]["value"] == "118/78"
    assert view["vitals"][1]["values"][-1]["time"] == "115/10/30 06:00"
    assert view["sources"][0]["time"] == ""  # Never confuse source fetch and measurement time.


def test_hpi_without_explicit_tasks_is_an_excerpt_not_todo():
    bundle = bundle_of("HPI\nSynthetic admission narrative.\nNo pending reports.\nThe old note mentioned recheck.", "admission_note")
    assert extract_top_todos(bundle) == []
    view = build_rounding_view(bundle)
    assert view["todo_kind"] == "excerpt" and view["todos"]
    assert "頂端摘錄" in render_bundle_overview(bundle)


def test_explicit_todo_section_stops_at_hpi_and_chinese_actions_are_recognized():
    bundle = bundle_of("TODO:\n- follow cultures\n- review oxygen requirement\n## HPI: Synthetic narrative.\nOther history.", "admission_note")
    kind, items = admission_top_items(bundle)
    assert kind == "todo" and items == ["follow cultures", "review oxygen requirement"]
    bundle.records[0] = SourceRecord("admission_note", "Synthetic note", "1. 追蹤血液培養\n2. 確認目前氧氣需求\nHPI\nSynthetic narrative.")
    assert extract_top_todos(bundle) == ["追蹤血液培養", "確認目前氧氣需求"]


def test_no_admission_record_has_no_fabricated_excerpt():
    view = build_rounding_view(bundle_of("Date\tCRP\n2026-10-30\t1.0"))
    assert view["todo_kind"] == "none" and view["todos"] == []
