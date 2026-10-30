import base64
import io
import json
import threading
import time
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from wardlens.config import AppSettings
from wardlens.llm.openrouter import OpenRouterError, StreamEvent
from wardlens.security.secrets import MemorySecretStore
from wardlens.services.ai import AIWorkflowService
from wardlens.services.session import ClinicalSession, UserError


class Client:
    def __init__(self):
        self.calls = []
        self.wait = None
        self.failure = False

    def cancel(self):
        if self.wait:
            self.wait.set()

    def stream(self, key, envelope, profile, **kwargs):
        self.calls.append((key, envelope, profile, kwargs))
        yield StreamEvent(delta="## 今日重點\n中文輸出")
        if self.wait:
            self.wait.wait(3)
        if self.failure:
            raise OpenRouterError("合成串流中斷")
        yield StreamEvent(delta="\n完成")


@pytest.fixture
def session(tmp_path):
    store = MemorySecretStore()
    store.set("openrouter_api_key", "synthetic-private-key-never-returned")
    client = Client()
    value = ClinicalSession(settings=AppSettings(external_ai_enabled=True, privacy_acknowledged=True),
        settings_path=tmp_path / "settings.json", secret_store=store,
        ai=AIWorkflowService(client=client), presenter=lambda bundle: {"sources": [], "warnings": bundle.warnings})
    yield value
    value.close()


def finish(session):
    deadline = time.monotonic() + 3
    while session.busy and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not session.busy
    assert session.job["status"] == "complete", session.job


def choose_demo(session):
    session.action("login", {"demo": True, "username": "", "password": ""})
    finish(session)
    session.action("search", {"doctor_id": "", "ward": "", "histnos": ""})
    finish(session)
    session.action("select", {"epoch": session.epoch, "patient_id": "patient-1"})


def draft(session, workspace="rounding", **updates):
    current = dict(session.drafts[workspace])
    current.update(data="Synthetic CRP 4 mg/dL", question="趨勢?", revision=current["revision"] + 1)
    current.update(updates)
    session.action("draft", dict(current, epoch=session.epoch, workspace=workspace))
    return {"epoch": session.epoch, "workspace": workspace, "revision": current["revision"]}


def preview(session, workspace="rounding", **updates):
    body = draft(session, workspace, **updates)
    result = session.action("prepare", body)
    assert not result["blocked"]
    return dict(body, preview_id=result["preview_id"], sha256=result["sha256"], confirm=True)


def test_demo_load_preserves_questions_and_patient_change_clears_both_workspaces(session):
    choose_demo(session)
    draft(session, question="keep-question")
    session.action("load", {"epoch": session.epoch, "months": 3})
    finish(session)
    state = session.state()
    assert len(state["patients"]) == 18 and state["overview"] is not None
    assert state["drafts"]["rounding"]["question"] == "keep-question"
    assert "資料截止" in state["drafts"]["emergency"]["data"]
    session.action("select", {"epoch": session.epoch, "patient_id": "patient-2"})
    assert all(not d["data"] and not d["question"] for d in session.drafts.values())
    assert not session.previews and not session.overview


def test_selected_patient_identifiers_are_redacted_without_a_loaded_bundle(session):
    choose_demo(session)
    patient = session.current_patient
    result = session.action("prepare", draft(session, data=f"{patient.name} {patient.histno} CRP 4 mg/dL"))
    assert patient.name not in result["user"] and patient.histno not in result["user"]


def test_old_revision_and_old_patient_preview_cannot_send(session):
    choose_demo(session)
    prepared = preview(session)
    draft(session, question="updated")
    with pytest.raises(UserError, match="輸入已變更"):
        session.action("generate", prepared)
    prepared = preview(session)
    session.action("select", {"epoch": session.epoch, "patient_id": "patient-2"})
    with pytest.raises(UserError, match="病人或資料"):
        session.action("generate", prepared)
    assert not session.ai.client.calls


def test_generation_requires_consent_and_consumes_preview_once(session):
    prepared = preview(session)
    with pytest.raises(UserError, match="先閱讀"):
        session.action("generate", dict(prepared, confirm=False))
    with pytest.raises(UserError, match="預覽內容不符"):
        session.action("generate", dict(prepared, sha256="different"))
    session.action("generate", prepared)
    finish(session)
    assert session.answers["rounding"]["status"] == "complete"
    assert "中文" in session.answers["rounding"]["text"]
    with pytest.raises(UserError, match="已使用"):
        session.action("generate", prepared)
    assert len(session.ai.client.calls) == 1


def test_server_enforces_privacy_stale_and_slow_confirmations(session):
    choose_demo(session)
    session.action("load", {"epoch": session.epoch, "months": 3})
    finish(session)
    session.current_bundle.fetched_at = datetime.now(UTC) - timedelta(hours=1)
    prepared = preview(session, "emergency", profile="deep", question="Synthetic change")
    with pytest.raises(UserError, match="15 分鐘"):
        session.action("generate", prepared)
    with pytest.raises(UserError, match="慢速"):
        session.action("generate", dict(prepared, confirm_stale=True))
    session.settings.require_zdr = False
    with pytest.raises(UserError, match="ZDR"):
        session.action("generate", dict(prepared, confirm_stale=True, confirm_slow=True))
    session.settings.external_ai_enabled = False
    with pytest.raises(UserError, match="啟用外部"):
        session.action("generate", dict(prepared, confirm_stale=True, confirm_slow=True, confirm_zdr_off=True))
    assert not session.ai.client.calls


def test_busy_and_cancellation_preserve_incomplete_status(session):
    choose_demo(session)
    prepared = preview(session)
    session.ai.client.wait = threading.Event()
    session.action("generate", prepared)
    with pytest.raises(UserError, match="工作進行中"):
        session.action("select", {"epoch": session.epoch, "patient_id": "patient-2"})
    session.action("cancel", {})
    deadline = time.monotonic() + 3
    while session.busy and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not session.busy
    assert session.answers["rounding"]["status"] == "cancelled"
    assert session.job["status"] == "cancelled"


def test_failure_is_not_completion_and_get_state_never_starts_a_new_request(session):
    prepared = preview(session)
    session.ai.client.failure = True
    session.action("generate", prepared)
    deadline = time.monotonic() + 3
    while session.busy and time.monotonic() < deadline:
        time.sleep(0.005)
    for _ in range(5):
        assert session.state()["answers"]["rounding"]["status"] == "incomplete"
    assert len(session.ai.client.calls) == 1


def test_repair_is_rechecked_and_cannot_follow_a_patient_change(session):
    choose_demo(session)
    body = draft(session, data="Synthetic code 87654321")
    blocked = session.action("prepare", body)
    assert blocked["blocked"] and "possible_long_identifier" in blocked["risks"]
    repaired = session.action("repair", dict(body, system=blocked["system"], user="Synthetic CRP 4 mg/dL"))
    assert not repaired["blocked"]
    session.action("select", {"epoch": session.epoch, "patient_id": "patient-2"})
    with pytest.raises(UserError):
        session.action("repair", dict(body, system=blocked["system"], user="Synthetic CRP 4 mg/dL"))


def test_credentials_never_in_state_or_settings_and_exports_are_explicit(session, tmp_path):
    choose_demo(session)
    state = json.dumps(session.state())
    assert "synthetic-private-key" not in state
    assert "synthetic-private-key" not in (tmp_path / "settings.json").read_text()
    with pytest.raises(UserError):
        session.action("export", {"kind": "docx"})
    result = session.action("export", {"kind": "docx", "confirm": True})
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(result["data_base64"]))) as archive:
        assert "word/document.xml" in archive.namelist()
    assert {p.name for p in tmp_path.iterdir()} == {"settings.json"}


def test_developer_save_invalidates_preview_without_resetting_key(session):
    prepared = preview(session)
    session.settings.developer_mode = True
    config = session.settings.developer_payload()
    config["custom_prompts"] = {"rounding": "Use a concise clinical summary. " * 5}
    session.action("developer_save", {"config": config})
    with pytest.raises(UserError, match="預覽已失效"):
        session.action("generate", prepared)
    assert session.state()["has_api_key"]


def test_close_removes_patient_and_draft_references(session):
    choose_demo(session)
    preview(session)
    session.close()
    assert not session.patients and not session.bundles and not session.previews
    assert all(not d["data"] for d in session.drafts.values())
    with pytest.raises(UserError):
        session.state()
