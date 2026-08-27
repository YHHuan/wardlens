from __future__ import annotations

from typing import Protocol

from wardlens.models import PatientBundle, PatientListResult, PatientSummary


class EMRError(RuntimeError):
    pass


class LoginError(EMRError):
    pass


class InterfaceChangedError(EMRError):
    pass


class IncompleteFetchError(EMRError):
    pass


class RequestBudgetExceeded(EMRError):
    pass


class EMRAdapter(Protocol):
    @property
    def logged_in(self) -> bool: ...

    def login(self, username: str, password: str) -> None: ...

    def logout(self) -> None: ...

    def search_patients(
        self,
        *,
        doctor_id: str = "",
        ward: str = "",
        histnos: list[str] | None = None,
    ) -> PatientListResult: ...

    def fetch_patient_bundle(self, patient: PatientSummary) -> PatientBundle: ...
