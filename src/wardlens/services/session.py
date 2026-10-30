"""Single-user clinical session shared by the local browser's authenticated APIs.

The browser is a view, not the authority for consent, stale previews or jobs.
Clinical state is never serialized to settings or diagnostics.
"""
from __future__ import annotations

import base64
import copy
import io
import re
import secrets
import threading
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from wardlens import __version__
from wardlens.config import DEEP_PROFILES, DEFAULT_PROFILES, AppSettings, DeveloperConfigError
from wardlens.emr.base import EMRError
from wardlens.emr.demo import DemoEMRAdapter
from wardlens.emr.vgh import VGHReadOnlyAdapter
from wardlens.llm.oauth import OpenRouterOAuth
from wardlens.llm.openrouter import OpenRouterError
from wardlens.llm.prompts import PromptBuilder, PromptEnvelope
from wardlens.models import PatientBundle, PatientSummary
from wardlens.security.deidentify import DataLeakRisk
from wardlens.security.outbound import PreviewBlocked
from wardlens.security.secrets import CredentialSecretStore, SecretStoreError
from wardlens.services.ai import AIWorkflowService, PreparedRequest


class UserError(ValueError):
    """Only fixed, deliberately user-facing messages cross the HTTP boundary."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _text(body: dict, key: str, maximum: int = 600_000, *, default: str = "") -> str:
    value = body.get(key, default)
    if not isinstance(value, str) or len(value) > maximum:
        raise UserError("輸入格式或長度不符，請縮小資料後重試。")
    return value


def _boolean(body: dict, key: str) -> bool:
    value = body.get(key)
    if not isinstance(value, bool):
        raise UserError("設定欄位必須是勾選／未勾選。")
    return value


def _integer(body: dict, key: str) -> int:
    value = body.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise UserError("資料版本格式不符，請重新載入介面。")
    return value


def _empty_answer() -> dict:
    return {"text": "", "status": "idle", "model": "", "error": ""}


def _empty_drafts() -> dict:
    return {name: {"data": "", "question": "", "workflow": name,
                   "profile": "emergency_fast" if name == "emergency" else "fast", "revision": 0}
            for name in ("rounding", "emergency")}


class ClinicalSession:
    def __init__(self, *, settings: AppSettings | None = None, settings_path: Path | None = None,
                 ai: AIWorkflowService | None = None, secret_store=None, adapter_factory=None,
                 presenter=None, oauth=None) -> None:
        self.settings = settings or AppSettings.load(settings_path)
        self.settings_path = settings_path
        self.ai = ai or AIWorkflowService(builder=PromptBuilder(self.settings.custom_prompts))
        self.secret_store = secret_store or CredentialSecretStore()
        self.oauth = oauth or OpenRouterOAuth()
        self._adapter_factory = adapter_factory or (
            lambda demo: DemoEMRAdapter() if demo else VGHReadOnlyAdapter(self.settings))
        self.adapter = self._adapter_factory(self.settings.demo_mode)
        self._presenter = presenter
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._closed = False
        self._key = ""
        self.epoch = 0
        self.patients: dict[str, PatientSummary] = {}
        self.bundles: dict[str, PatientBundle] = {}
        self.selected_id = ""
        self.overview = None
        self.drafts = _empty_drafts()
        self.answers = {name: _empty_answer() for name in self.drafts}
        self.previews: dict[str, dict] = {}
        self.blocked: dict[str, dict] = {}
        self.job = None
        self.busy = False
        self.status = "尚未登入"
        self.list_status = "尚未載入病人清單"
        try:
            self._key = self.secret_store.get("openrouter_api_key") or ""
        except SecretStoreError:
            pass

    @property
    def current_patient(self) -> PatientSummary | None:
        return self.patients.get(self.selected_id)

    @property
    def current_bundle(self) -> PatientBundle | None:
        patient = self.current_patient
        return self.bundles.get(patient.local_key) if patient else None

    def _patient_view(self, key: str, patient: PatientSummary) -> dict:
        return {"id": key, "name": patient.name, "location": patient.location,
                "histno": patient.histno, "age": patient.age, "sex": patient.sex,
                "loaded": patient.local_key in self.bundles}

    def _age(self, prepared: PreparedRequest | None = None) -> int | None:
        stamp = prepared.source_fetched_at if prepared else (
            self.current_bundle.fetched_at if self.current_bundle else None)
        if stamp is None:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return max(0, int((datetime.now(UTC) - stamp.astimezone(UTC)).total_seconds() / 60))

    def state(self) -> dict:
        with self._lock:
            if self._closed:
                raise UserError("WardLens 已結束，請重新開啟程式。", 409)
            return copy.deepcopy({
                "version": __version__, "epoch": self.epoch, "logged_in": self.adapter.logged_in,
                "demo": self.settings.demo_mode, "status": self.status, "busy": self.busy,
                "patients": [self._patient_view(k, p) for k, p in self.patients.items()],
                "list_status": self.list_status, "selected_id": self.selected_id,
                "selected": self._patient_view(self.selected_id, self.current_patient) if self.current_patient else None,
                "overview": self.overview, "drafts": self.drafts, "answers": self.answers,
                "job": self.job, "profiles": [asdict(self.settings.profile(k)) for k in DEFAULT_PROFILES],
                "settings": {k: getattr(self.settings, k) for k in (
                    "external_ai_enabled", "privacy_acknowledged", "require_zdr", "developer_mode", "lab_lookback_months")},
                "has_api_key": bool(self._key), "snapshot_age_minutes": self._age(),
            })

    def _save(self) -> None:
        self.settings.save(self.settings_path)

    def _invalidate(self, workspace: str | None = None) -> None:
        for name in ([workspace] if workspace else self.drafts):
            self.previews.pop(name, None)
            self.blocked.pop(name, None)

    def _clear(self, *, patients: bool = False) -> None:
        self.epoch += 1
        self.drafts = _empty_drafts()
        self.answers = {name: _empty_answer() for name in self.drafts}
        self._invalidate()
        self.overview = None
        if patients:
            self.patients.clear()
            self.bundles.clear()
            self.selected_id = ""
            self.list_status = "尚未載入病人清單"
        self.job = None

    def _view(self, bundle: PatientBundle) -> dict:
        if self._presenter:
            return self._presenter(bundle)
        from wardlens.services.rounding import build_rounding_view

        return build_rounding_view(bundle)

    def _fill(self, bundle: PatientBundle) -> None:
        self.overview = self._view(bundle)
        context = self.ai.builder.editor_context(bundle)
        for name, draft in self.drafts.items():
            draft["data"] = context
            draft["revision"] += 1
            self.answers[name] = _empty_answer()
        self._invalidate()

    def _require_epoch(self, body: dict) -> None:
        if _integer(body, "epoch") != self.epoch:
            raise UserError("病人或資料已變更，請使用目前畫面重新操作。", 409)

    def _workspace(self, body: dict, *, exact_revision: bool = True) -> str:
        self._require_epoch(body)
        name = body.get("workspace")
        if name not in self.drafts:
            raise UserError("未知工作區。")
        if exact_revision and _integer(body, "revision") != self.drafts[name]["revision"]:
            raise UserError("輸入已變更，請重新檢查送出資料。", 409)
        return name

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, (UserError, EMRError, OpenRouterError, SecretStoreError, DeveloperConfigError, DataLeakRisk)):
            return str(exc)
        return "操作未完成（WL-WEB-OP）；請重試或使用桌面備援。"

    def _job(self, kind: str, work, apply=None, *, workspace: str | None = None) -> dict:
        if self.busy:
            raise UserError("工作進行中，請完成或停止後再操作。", 409)
        job_id = secrets.token_urlsafe(16)
        epoch = self.epoch
        self.job = {"id": job_id, "kind": kind, "status": "running", "result": None, "error": ""}
        self.busy = True
        self._cancel.clear()
        self.status = "正在處理…"

        def worker() -> None:
            try:
                with self._lock:
                    if self._closed or self.epoch != epoch:
                        return
                    if self._cancel.is_set():
                        self.job["status"] = "cancelled"
                        if workspace:
                            self.answers[workspace]["status"] = "cancelled"
                        return
                result = work()
                with self._lock:
                    if self._closed or self.epoch != epoch:
                        return
                    if apply:
                        result = apply(result)
                    self.job.update(status="cancelled" if self._cancel.is_set() else "complete", result=result)
                    self.status = "已停止" if self._cancel.is_set() else "完成"
            except Exception as exc:
                with self._lock:
                    if not self._closed and self.epoch == epoch:
                        message = "生成已停止；以上內容不完整。" if self._cancel.is_set() else self._safe_error(exc)
                        self.job.update(status="cancelled" if self._cancel.is_set() else "failed", error=message)
                        self.status = message
                        if workspace:
                            self.answers[workspace].update(status="cancelled" if self._cancel.is_set() else "incomplete", error=message)
            finally:
                with self._lock:
                    self.busy = False
                    if self._closed:
                        try:
                            self.adapter.logout()
                        except Exception:
                            pass

        threading.Thread(target=worker, daemon=True, name="wardlens-local-job").start()
        return {"started": True, "job_id": job_id}

    def action(self, name: str, body: dict) -> dict:
        with self._lock:
            if self._closed:
                raise UserError("WardLens 已結束。", 409)
            if not isinstance(body, dict):
                raise UserError("請使用 JSON object。")
            if name == "cancel":
                if self.busy and self.job and self.job["kind"] == "generate":
                    self._cancel.set()
                    self.ai.client.cancel()
                    self.status = "正在停止生成…"
                return {}
            if name == "close":
                self.close()
                return {}
            if self.busy:
                raise UserError("工作進行中，請等待完成或先停止生成。", 409)
            handler = getattr(self, "_action_" + name, None)
            if handler is None or not re.fullmatch(r"[a-z_]{1,40}", name):
                raise UserError("未知操作。")
            return handler(body)

    def _action_login(self, body: dict) -> dict:
        demo = _boolean(body, "demo")
        username, password = _text(body, "username", 300), _text(body, "password", 1000)
        self.adapter.logout()
        self._clear(patients=True)
        self.settings.demo_mode = demo
        self._save()
        self.adapter = self._adapter_factory(demo)
        adapter = self.adapter
        return self._job("login", lambda: adapter.login(username, password), lambda _: {})

    def _action_logout(self, body: dict) -> dict:
        self.adapter.logout()
        self._clear(patients=True)
        self.status = "已登出並清除記憶體資料"
        return {}

    def _action_search(self, body: dict) -> dict:
        if not self.adapter.logged_in:
            raise UserError("請先登入；Demo 可直接登入。")
        doctor, ward = _text(body, "doctor_id", 200), _text(body, "ward", 200)
        histnos = [s for s in re.split(r"[\s,，;；]+", _text(body, "histnos", 3000)) if s]
        self._clear(patients=True)

        def apply(result):
            self.patients = {f"patient-{i}": p for i, p in enumerate(result.patients, 1)}
            self.list_status = f"{len(result.patients)} 人｜" + ("清單完整" if result.complete else "清單不完整")
            if result.warnings:
                self.list_status += "｜" + "；".join(result.warnings)
            return {}

        return self._job("search", lambda: self.adapter.search_patients(doctor_id=doctor, ward=ward, histnos=histnos or None), apply)

    def _action_select(self, body: dict) -> dict:
        self._require_epoch(body)
        key = _text(body, "patient_id", 100)
        if key not in self.patients:
            raise UserError("請從目前清單選取病人。")
        self._clear()
        self.selected_id = key
        if self.current_bundle:
            self._fill(self.current_bundle)
        return {}

    def _action_load(self, body: dict) -> dict:
        self._require_epoch(body)
        if not self.current_patient or not self.adapter.logged_in:
            raise UserError("請先登入並選取病人。")
        months = _integer(body, "months")
        if months not in {1, 3, 6, 12}:
            raise UserError("回溯月份請選 1、3、6 或 12。")
        self.settings.lab_lookback_months = months
        self._save()
        patient = self.current_patient

        def apply(bundle):
            self.bundles[patient.local_key] = bundle
            self._fill(bundle)
            return {}

        return self._job("load", lambda: self.adapter.fetch_patient_bundle(patient), apply)

    def _action_draft(self, body: dict) -> dict:
        name = self._workspace(body, exact_revision=False)
        revision = _integer(body, "revision")
        old = self.drafts[name]
        candidate = {"data": _text(body, "data"), "question": _text(body, "question", 20000),
                     "workflow": _text(body, "workflow", 30), "profile": _text(body, "profile", 60),
                     "revision": revision}
        if candidate["profile"] not in DEFAULT_PROFILES or candidate["workflow"] not in (
            {"emergency"} if name == "emergency" else {"rounding", "admission", "qa"}):
            raise UserError("模型路由或工作類型不符。")
        if revision < old["revision"] or (revision == old["revision"] and candidate != old):
            raise UserError("較新的輸入已儲存，請重新確認目前畫面。", 409)
        if candidate != old:
            self.drafts[name] = candidate
            self._invalidate(name)
            self.answers[name] = _empty_answer()
        return {"revision": revision}

    def _prepare(self, name: str, envelope: PromptEnvelope) -> dict:
        draft = self.drafts[name]
        stamp = self.current_bundle.fetched_at if self.current_bundle else None
        self._invalidate(name)
        try:
            prepared = self.ai.prepare_reviewed(envelope.canonical(), workflow=draft["workflow"],
                                                patient=self.current_patient, source_fetched_at=stamp)
        except PreviewBlocked as exc:
            parsed = PromptEnvelope.parse(exc.result.text)
            self.blocked[name] = {"epoch": self.epoch, "revision": draft["revision"]}
            return {"blocked": True, "system": parsed.system, "user": parsed.user,
                    "risks": sorted({r.kind for r in exc.result.residual_risks}), "error": str(exc)}
        profile = self.settings.profile(draft["profile"])
        preview_id = secrets.token_urlsafe(24)
        self.previews[name] = {"id": preview_id, "prepared": prepared, "epoch": self.epoch,
                               "revision": draft["revision"], "profile": profile}
        parsed = prepared.envelope()
        return {"blocked": False, "preview_id": preview_id, "sha256": prepared.preview.sha256,
                "clinical": self.ai.builder.clinical_preview(parsed), "system": parsed.system,
                "user": parsed.user, "model": profile.model, "snapshot_age_minutes": self._age(prepared),
                "slow": name == "emergency" and profile.key in DEEP_PROFILES,
                "require_zdr": self.settings.require_zdr}

    def _action_prepare(self, body: dict) -> dict:
        name = self._workspace(body)
        draft = self.drafts[name]
        try:
            envelope = self.ai.builder.build(draft["workflow"], bundle=self.current_bundle,
                context_text=draft["data"], question=draft["question"], emergency_text=draft["question"])
        except ValueError as exc:
            raise UserError("請先填寫問題或目前病況。") from exc
        return self._prepare(name, envelope)

    def _action_repair(self, body: dict) -> dict:
        name = self._workspace(body)
        expected = self.blocked.get(name)
        if not expected or expected != {"epoch": self.epoch, "revision": self.drafts[name]["revision"]}:
            raise UserError("修正草稿已失效，請重新建立預覽。", 409)
        return self._prepare(name, PromptEnvelope(_text(body, "system", 100000), _text(body, "user", 700000)))

    def _checked_preview(self, body: dict) -> tuple[str, dict]:
        name = self._workspace(body)
        saved = self.previews.get(name)
        if not saved or not secrets.compare_digest(saved["id"], _text(body, "preview_id", 100)):
            raise UserError("請重新檢查送出資料；預覽已失效或已使用。", 409)
        prepared = saved["prepared"]
        if not secrets.compare_digest(prepared.preview.sha256, _text(body, "sha256", 100)):
            raise UserError("預覽內容不符，請重新檢查。", 409)
        if saved["epoch"] != self.epoch or saved["revision"] != self.drafts[name]["revision"]:
            raise UserError("病人或輸入已變更，請重新預覽。", 409)
        if body.get("confirm") is not True:
            raise UserError("請先閱讀去識別預覽，再確認本次操作。", 403)
        self.ai.guard.verify(prepared.preview, prepared.envelope().canonical(), self.current_patient)
        return name, saved

    def _action_generate(self, body: dict) -> dict:
        name, saved = self._checked_preview(body)
        if not self.settings.external_ai_enabled or not self.settings.privacy_acknowledged:
            raise UserError("請先在設定啟用外部 AI 並確認院內資料使用權限。", 403)
        if not self._key:
            raise UserError("請先連接 OpenRouter 或貼上 API key。")
        prepared, profile = saved["prepared"], saved["profile"]
        if profile != self.settings.profile(self.drafts[name]["profile"]):
            raise UserError("模型設定已改變，請重新預覽。", 409)
        age = self._age(prepared)
        if age is not None and age >= 15 and body.get("confirm_stale") is not True:
            raise UserError("資料超過 15 分鐘，請重新載入或明確確認使用舊快照。", 403)
        if name == "emergency" and profile.key in DEEP_PROFILES and body.get("confirm_slow") is not True:
            raise UserError("急症慢速複核須確認已先處置／叫支援。", 403)
        require_zdr = self.settings.require_zdr
        if not require_zdr and body.get("confirm_zdr_off") is not True:
            raise UserError("ZDR 已關閉，請明確確認本次外部傳送。", 403)
        self.previews.pop(name)  # One explicit send can never create two billed requests.
        self.answers[name] = {"text": "", "status": "running", "model": profile.model, "error": ""}
        key = self._key
        epoch = self.epoch

        def work():
            if self._closed or self._cancel.is_set():
                return {}
            stream = self.ai.stream(key, prepared, profile, require_zdr=require_zdr, cancel_event=self._cancel)
            try:
                for event in stream:
                    with self._lock:
                        if self._closed or self._cancel.is_set() or self.epoch != epoch:
                            break
                        if event.delta:
                            self.answers[name]["text"] += event.delta
            finally:
                stream.close()
            with self._lock:
                if not self._closed and self.epoch == epoch:
                    self.answers[name]["status"] = "cancelled" if self._cancel.is_set() else "complete"
            return {}

        return self._job("generate", work, workspace=name)

    def _action_copy_prompt(self, body: dict) -> dict:
        _, saved = self._checked_preview(body)
        self.ai.record_copy(saved["prepared"])
        return {"text": saved["prepared"].preview.text}

    def _action_settings(self, body: dict) -> dict:
        keys = ("external_ai_enabled", "privacy_acknowledged", "require_zdr", "developer_mode")
        values = {k: _boolean(body, k) for k in keys}
        for key, value in values.items():
            setattr(self.settings, key, value)
        self._save()
        self._invalidate()
        return {}

    def _save_key(self, key: str) -> dict:
        value = key.strip()
        self.ai.client.verify_key(value)
        with self._lock:
            if self._closed:
                return {}
            self.secret_store.set("openrouter_api_key", value)
            self._key = value
        return {"text": "OpenRouter 已連接；key 已存入 Windows Credential Manager。"}

    def _action_key_save(self, body: dict) -> dict:
        key = _text(body, "key", 5000).strip()
        if not key.startswith("sk-or-") or len(key) < 30:
            raise UserError("這段內容不像 OpenRouter API key。")
        return self._job("key_save", lambda: self._save_key(key))

    def _action_key_delete(self, body: dict) -> dict:
        self.secret_store.delete("openrouter_api_key")
        self._key = ""
        self._invalidate()
        return {}

    def _action_oauth(self, body: dict) -> dict:
        return self._job("oauth", lambda: self._save_key(self.oauth.connect()))

    def _action_connection(self, body: dict) -> dict:
        if not self._key:
            raise UserError("請先連接 OpenRouter。")
        return self._job("connection", lambda: {"text": self.ai.client.check_connection(
            self._key, [self.settings.profile(k) for k in DEFAULT_PROFILES], require_zdr=self.settings.require_zdr)})

    def _developer(self) -> None:
        if not self.settings.developer_mode:
            raise UserError("請先啟用開發者模式。", 403)

    def _action_developer_get(self, body: dict) -> dict:
        self._developer()
        return {"config": self.settings.developer_payload(),
                "profiles": [asdict(self.settings.profile(k)) for k in DEFAULT_PROFILES],
                "prompts": {k: self.ai.builder.system_prompt(k) for k in ("rounding", "admission", "qa", "emergency")}}

    def _action_developer_save(self, body: dict) -> dict:
        self._developer()
        candidate = copy.deepcopy(self.settings)
        try:
            candidate.apply_developer_payload(body.get("config"))
        except DeveloperConfigError as exc:
            raise UserError(str(exc)) from exc
        candidate.save(self.settings_path)
        self.settings.custom_profiles = candidate.custom_profiles
        self.settings.custom_models = candidate.custom_models
        self.settings.custom_prompts = candidate.custom_prompts
        self.ai.builder = PromptBuilder(self.settings.custom_prompts)
        self._invalidate()
        return {}

    def _action_models(self, body: dict) -> dict:
        self._developer()
        zdr = _boolean(body, "zdr")
        return self._job("models", lambda: {"models": [{"id": name, "name": name}
            for name in self.ai.client.available_models(zdr_only=zdr)]})

    def _action_browser_probe(self, body: dict) -> dict:
        from wardlens.browser_probe import run_browser_probe

        return self._job("browser_probe", run_browser_probe)

    def _action_browser_prepare(self, body: dict) -> dict:
        from wardlens.browser_probe import prepare_driver

        return self._job("browser_prepare", lambda: {"text": "Microsoft 驅動已準備：" + prepare_driver()})

    def _action_export(self, body: dict) -> dict:
        if body.get("confirm") is not True or not self.patients:
            raise UserError("請確認將可識別清單匯出到院方允許的位置。", 403)
        kind = body.get("kind")
        patients = list(self.patients.values())
        if kind == "docx":
            from wardlens.services.export import export_patient_list_docx

            stream = io.BytesIO()
            export_patient_list_docx(patients, self.bundles, stream)
            data = stream.getvalue()
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif kind == "csv":
            import csv

            stream = io.StringIO(newline="")
            writer = csv.writer(stream)
            writer.writerow(["Ward", "Bed", "Name", "HistNo", "Age", "Sex", "CaseNo"])
            for p in patients:
                # Prevent spreadsheet formula execution in an explicit local export.
                writer.writerow(["'" + x if x.startswith(("=", "+", "-", "@", "\t", "\r")) else x
                                 for x in (p.ward, p.bed, p.name, p.histno, p.age, p.sex, p.case_no)])
            data = stream.getvalue().encode("utf-8-sig")
            mime = "text/csv;charset=utf-8"
        else:
            raise UserError("匯出格式不支援。")
        return {"filename": f"WardLens_patient_list.{kind}", "mime": mime,
                "data_base64": base64.b64encode(data).decode("ascii")}

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel.set()
            self._key = ""
            self._clear(patients=True)
            self.status = "已結束"
            try:
                self.ai.client.cancel()
            except Exception:
                pass
            try:
                self.adapter.logout()
            except Exception:
                pass
