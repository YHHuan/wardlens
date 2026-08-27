from __future__ import annotations

import json
from datetime import datetime
from importlib.resources import files

from wardlens.models import PatientBundle, PatientListResult, PatientSummary, SourceRecord


class DemoEMRAdapter:
    def __init__(self) -> None:
        path = files("wardlens").joinpath("resources/demo/patients.json")
        self._payload = json.loads(path.read_text(encoding="utf-8"))
        self._logged_in = False

    @property
    def logged_in(self) -> bool:
        return self._logged_in

    def login(self, username: str = "", password: str = "") -> None:
        self._logged_in = True

    def logout(self) -> None:
        self._logged_in = False

    def search_patients(
        self,
        *,
        doctor_id: str = "",
        ward: str = "",
        histnos: list[str] | None = None,
    ) -> PatientListResult:
        patients = [self._patient(item) for item in self._payload["patients"]]
        if histnos:
            wanted = {value.strip() for value in histnos}
            patients = [patient for patient in patients if patient.histno in wanted]
        if ward:
            patients = [patient for patient in patients if patient.ward.lower() == ward.lower()]
        return PatientListResult(
            patients=patients,
            complete=True,
            pages_fetched=2,
            declared_total=len(patients),
            warnings=["目前為合成資料 Demo；未連線院內系統。"],
        )

    def fetch_patient_bundle(self, patient: PatientSummary) -> PatientBundle:
        item = next(item for item in self._payload["patients"] if item["histno"] == patient.histno)
        records = []
        for record in item.get("records", []):
            observed_at = (
                datetime.fromisoformat(record["observed_at"]) if record.get("observed_at") else None
            )
            records.append(
                SourceRecord(
                    source_type=record["source_type"],
                    title=record["title"],
                    content=record["content"],
                    source_record_id=record["source_record_id"],
                    observed_at=observed_at,
                    status=record.get("status", ""),
                )
            )
        return PatientBundle(
            patient=patient,
            records=records,
            warnings=["合成資料僅用於功能測試；不可作為臨床依據。"],
        )

    @staticmethod
    def _patient(item: dict[str, object]) -> PatientSummary:
        return PatientSummary(
            histno=str(item["histno"]),
            name=str(item.get("name", "")),
            ward=str(item.get("ward", "")),
            bed=str(item.get("bed", "")),
            age=str(item.get("age", "")),
            sex=str(item.get("sex", "")),
            case_no=str(item.get("case_no", "")),
        )
