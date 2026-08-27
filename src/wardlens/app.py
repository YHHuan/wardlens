from __future__ import annotations

import argparse
import re
import threading
import tkinter as tk
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from wardlens import __version__
from wardlens.config import DEFAULT_PROFILES, AppSettings, app_data_dir
from wardlens.emr.base import EMRError
from wardlens.emr.demo import DemoEMRAdapter
from wardlens.emr.vgh import VGHReadOnlyAdapter
from wardlens.llm.openrouter import OpenRouterError
from wardlens.models import PatientBundle, PatientListResult, PatientSummary
from wardlens.security.audit import HashOnlyAuditLog
from wardlens.security.deidentify import DataLeakRisk
from wardlens.security.secrets import CredentialSecretStore, SecretStoreError
from wardlens.services.ai import AIWorkflowService, PreparedRequest
from wardlens.services.export import export_patient_list_csv, export_patient_list_docx
from wardlens.services.summary import render_bundle_overview

WORKFLOW_LABELS = {
    "查房重點": "rounding",
    "Admission Note": "admission",
    "問選取病人": "qa",
}


class WardLensApp:
    def __init__(self, root: tk.Tk, *, force_demo: bool = False) -> None:
        self.root = root
        self.settings = AppSettings.load()
        if force_demo:
            self.settings.demo_mode = True
        self.settings.save()

        self.audit = HashOnlyAuditLog(app_data_dir() / "audit.jsonl")
        self.ai = AIWorkflowService(audit=self.audit)
        self.secret_store = CredentialSecretStore()
        self.adapter = self._new_adapter()
        self.patients: dict[str, PatientSummary] = {}
        self.bundles: dict[str, PatientBundle] = {}
        self.current_patient: PatientSummary | None = None
        self.current_bundle: PatientBundle | None = None
        self.ai_prepared: PreparedRequest | None = None
        self.emergency_prepared: PreparedRequest | None = None
        self._session_api_key = ""
        self._busy = False
        self._cancel_event = threading.Event()
        self._clipboard_snapshot = ""

        self._build_variables()
        self._configure_root()
        self._build_ui()
        self._apply_mode_state()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_variables(self) -> None:
        self.demo_var = tk.BooleanVar(value=self.settings.demo_mode)
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.doctor_var = tk.StringVar()
        self.ward_var = tk.StringVar()
        self.histnos_var = tk.StringVar()
        self.status_var = tk.StringVar(value="尚未登入")
        self.list_status_var = tk.StringVar(value="尚未載入病人清單")
        self.selected_var = tk.StringVar(value="尚未選取病人")

        self.workflow_var = tk.StringVar(value="查房重點")
        self.profile_var = tk.StringVar(value="fast")
        self.ai_preview_status_var = tk.StringVar(value="尚未建立 outbound 預覽")
        self.ai_result_status_var = tk.StringVar(value="")

        self.emergency_profile_var = tk.StringVar(value="emergency_fast")
        self.emergency_with_patient_var = tk.BooleanVar(value=True)
        self.emergency_preview_status_var = tk.StringVar(value="尚未建立 outbound 預覽")
        self.emergency_mode_warning_var = tk.StringVar(value="急症第一輪建議 emergency_fast。")

        self.external_ai_var = tk.BooleanVar(value=self.settings.external_ai_enabled)
        self.policy_ack_var = tk.BooleanVar(value=self.settings.privacy_acknowledged)
        self.zdr_var = tk.BooleanVar(value=self.settings.require_zdr)

    def _configure_root(self) -> None:
        self.root.title(f"WardLens {__version__}｜住院查房助手")
        self.root.geometry("1320x820")
        self.root.minsize(1050, 680)
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Danger.TLabel", foreground="#a32121")
        style.configure("Muted.TLabel", foreground="#555555")
        style.configure("Title.TLabel", font=("Segoe UI", 14, "bold"))
        style.configure("Treeview", rowheight=25)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X)
        ttk.Label(header, text="WardLens", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(
            header,
            text="唯讀擷取｜AI 草稿｜重大決策請核對原始病歷與院內流程",
            style="Danger.TLabel",
        ).pack(side=tk.LEFT, padx=(14, 0))
        ttk.Label(header, textvariable=self.status_var).pack(side=tk.RIGHT)

        self._build_login_bar(outer)

        body = ttk.Panedwindow(outer, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        left = ttk.Frame(body, padding=(0, 0, 8, 0))
        right = ttk.Frame(body)
        body.add(left, weight=2)
        body.add(right, weight=5)
        self._build_patient_panel(left)
        self._build_notebook(right)

    def _build_login_bar(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="連線與清單", padding=8)
        frame.pack(fill=tk.X, pady=(8, 0))

        self.demo_check = ttk.Checkbutton(
            frame,
            text="合成資料 Demo",
            variable=self.demo_var,
            command=self._switch_mode,
        )
        self.demo_check.grid(row=0, column=0, padx=(0, 8), sticky="w")
        ttk.Label(frame, text="入口網帳號").grid(row=0, column=1, sticky="e")
        self.username_entry = ttk.Entry(frame, textvariable=self.username_var, width=14)
        self.username_entry.grid(row=0, column=2, padx=4)
        ttk.Label(frame, text="密碼").grid(row=0, column=3, sticky="e")
        self.password_entry = ttk.Entry(frame, textvariable=self.password_var, show="•", width=15)
        self.password_entry.grid(row=0, column=4, padx=4)
        ttk.Button(frame, text="登入", command=self._login).grid(row=0, column=5, padx=(4, 12))
        ttk.Button(frame, text="登出／清除", command=self._logout).grid(
            row=0, column=6, padx=(0, 12)
        )

        ttk.Separator(frame, orient=tk.VERTICAL).grid(row=0, column=7, sticky="ns", padx=4)
        ttk.Label(frame, text="醫師燈號").grid(row=0, column=8, sticky="e")
        ttk.Entry(frame, textvariable=self.doctor_var, width=11).grid(row=0, column=9, padx=4)
        ttk.Label(frame, text="病房").grid(row=0, column=10, sticky="e")
        ttk.Entry(frame, textvariable=self.ward_var, width=8).grid(row=0, column=11, padx=4)
        ttk.Button(frame, text="載入清單", command=self._search_patients).grid(
            row=0, column=12, padx=4
        )

        ttk.Label(frame, text="指定病歷號（逗號／空白分隔）").grid(
            row=1, column=1, columnspan=2, sticky="e", pady=(7, 0)
        )
        ttk.Entry(frame, textvariable=self.histnos_var, width=34).grid(
            row=1, column=3, columnspan=3, sticky="ew", padx=4, pady=(7, 0)
        )
        ttk.Label(
            frame,
            text="帳密只留在本次記憶體；不寫入設定檔。",
            style="Muted.TLabel",
        ).grid(row=1, column=8, columnspan=5, sticky="w", pady=(7, 0))
        frame.columnconfigure(3, weight=1)

    def _build_patient_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="病人清單", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            parent, textvariable=self.list_status_var, style="Muted.TLabel", wraplength=370
        ).pack(fill=tk.X, pady=(2, 6))
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        columns = ("location", "name", "histno", "age_sex")
        self.patient_tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", selectmode="browse"
        )
        for key, title, width in (
            ("location", "床位", 78),
            ("name", "姓名", 86),
            ("histno", "病歷號", 90),
            ("age_sex", "年齡／性別", 76),
        ):
            self.patient_tree.heading(key, text=title)
            self.patient_tree.column(key, width=width, minwidth=55, anchor="w")
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.patient_tree.yview)
        self.patient_tree.configure(yscrollcommand=scrollbar.set)
        self.patient_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.patient_tree.bind("<<TreeviewSelect>>", self._patient_selected)
        self.patient_tree.bind("<Double-1>", lambda _event: self._load_selected_patient())

        actions = ttk.Frame(parent)
        actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(actions, text="載入選取病人資料", command=self._load_selected_patient).pack(
            fill=tk.X
        )
        row = ttk.Frame(actions)
        row.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(row, text="匯出 DOCX", command=lambda: self._export("docx")).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3)
        )
        ttk.Button(row, text="匯出 CSV", command=lambda: self._export("csv")).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0)
        )

    def _build_notebook(self, parent: ttk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True)
        overview = ttk.Frame(notebook, padding=8)
        ai_tab = ttk.Frame(notebook, padding=8)
        emergency = ttk.Frame(notebook, padding=8)
        privacy = ttk.Frame(notebook, padding=8)
        notebook.add(overview, text="病人總覽")
        notebook.add(ai_tab, text="查房／病歷 AI")
        notebook.add(emergency, text="急症處置")
        notebook.add(privacy, text="隱私與模型")
        self._build_overview_tab(overview)
        self._build_ai_tab(ai_tab)
        self._build_emergency_tab(emergency)
        self._build_privacy_tab(privacy)

    def _build_overview_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, textvariable=self.selected_var, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            parent,
            text="未載入／解析不到不等於沒有；每一筆都附來源 hash 與擷取警告。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 6))
        self.overview_text = ScrolledText(parent, wrap=tk.WORD, font=("Consolas", 10))
        self.overview_text.pack(fill=tk.BOTH, expand=True)
        self._replace_text(self.overview_text, "請從左側選取病人，再按「載入選取病人資料」。")

    def _build_ai_tab(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent)
        controls.pack(fill=tk.X)
        ttk.Label(controls, text="工作").pack(side=tk.LEFT)
        workflow = ttk.Combobox(
            controls,
            textvariable=self.workflow_var,
            values=list(WORKFLOW_LABELS),
            state="readonly",
            width=16,
        )
        workflow.pack(side=tk.LEFT, padx=(4, 12))
        workflow.bind("<<ComboboxSelected>>", lambda _event: self._invalidate_ai_preview())
        ttk.Label(controls, text="模式／模型").pack(side=tk.LEFT)
        profile = ttk.Combobox(
            controls,
            textvariable=self.profile_var,
            values=("fast", "deep", "gemini_fast", "claude_second"),
            state="readonly",
            width=18,
        )
        profile.pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(
            controls,
            text="fast=Terra/low；deep=Sol/high；也可選 Gemini Flash 或 Claude",
            style="Muted.TLabel",
        ).pack(side=tk.LEFT)

        ttk.Label(parent, text="想問什麼（查房整理可留空）").pack(anchor="w", pady=(8, 2))
        self.question_text = ScrolledText(parent, height=4, wrap=tk.WORD)
        self.question_text.pack(fill=tk.X)
        self.question_text.bind("<KeyRelease>", lambda _event: self._invalidate_ai_preview())

        action = ttk.Frame(parent)
        action.pack(fill=tk.X, pady=6)
        ttk.Button(action, text="1. 建立去識別預覽", command=self._prepare_ai).pack(side=tk.LEFT)
        ttk.Button(action, text="2. 送出 OpenRouter", command=self._send_ai).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(action, text="複製 input 給其他 AI", command=self._copy_ai_prompt).pack(
            side=tk.LEFT
        )
        ttk.Button(action, text="停止生成", command=self._cancel_generation).pack(side=tk.RIGHT)
        ttk.Label(parent, textvariable=self.ai_preview_status_var, style="Muted.TLabel").pack(
            anchor="w"
        )

        panes = ttk.Panedwindow(parent, orient=tk.VERTICAL)
        panes.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        preview_frame = ttk.LabelFrame(
            panes, text="實際 outbound input（唯讀；送出 hash 必須完全相同）", padding=4
        )
        result_frame = ttk.LabelFrame(panes, text="AI 草稿（使用前逐項核對來源）", padding=4)
        panes.add(preview_frame, weight=2)
        panes.add(result_frame, weight=3)
        self.ai_preview_text = ScrolledText(
            preview_frame, wrap=tk.WORD, height=8, font=("Consolas", 9)
        )
        self.ai_preview_text.pack(fill=tk.BOTH, expand=True)
        self.ai_preview_text.configure(state=tk.DISABLED)
        self.ai_result_text = ScrolledText(result_frame, wrap=tk.WORD, height=12)
        self.ai_result_text.pack(fill=tk.BOTH, expand=True)
        ttk.Label(result_frame, textvariable=self.ai_result_status_var, style="Muted.TLabel").pack(
            anchor="w"
        )

    def _build_emergency_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(
            parent,
            text="若 peri-arrest、嚴重呼吸窘迫、休克、意識惡化或大出血：立即叫 senior／RRT／Code／ICU；不要等 AI。",
            style="Danger.TLabel",
            wraplength=980,
        ).pack(fill=tk.X)
        ttk.Label(
            parent,
            text="可在文字框按 Win+H 使用 Windows 語音輸入；資訊不完整也可先送出。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(3, 7))
        self.emergency_text = ScrolledText(parent, height=7, wrap=tk.WORD)
        self.emergency_text.pack(fill=tk.X)
        self.emergency_text.bind(
            "<KeyRelease>", lambda _event: self._invalidate_emergency_preview()
        )

        controls = ttk.Frame(parent)
        controls.pack(fill=tk.X, pady=6)
        ttk.Checkbutton(
            controls,
            text="附上目前選取病人的已載入資料",
            variable=self.emergency_with_patient_var,
            command=self._invalidate_emergency_preview,
        ).pack(side=tk.LEFT)
        ttk.Label(controls, text="模式").pack(side=tk.LEFT, padx=(14, 3))
        combo = ttk.Combobox(
            controls,
            textvariable=self.emergency_profile_var,
            values=("emergency_fast", "deep", "claude_second", "gemini_fast"),
            state="readonly",
            width=18,
        )
        combo.pack(side=tk.LEFT)
        combo.bind("<<ComboboxSelected>>", lambda _event: self._emergency_profile_changed())
        ttk.Button(controls, text="1. 預覽", command=self._prepare_emergency).pack(
            side=tk.LEFT, padx=(12, 4)
        )
        ttk.Button(controls, text="2. 送出", command=self._send_emergency).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(controls, text="複製 input", command=self._copy_emergency_prompt).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(controls, text="停止", command=self._cancel_generation).pack(side=tk.RIGHT)
        ttk.Label(
            parent, textvariable=self.emergency_preview_status_var, style="Muted.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            parent,
            textvariable=self.emergency_mode_warning_var,
            style="Danger.TLabel",
        ).pack(anchor="w")

        panes = ttk.Panedwindow(parent, orient=tk.VERTICAL)
        panes.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        preview_frame = ttk.LabelFrame(panes, text="實際 outbound input", padding=4)
        result_frame = ttk.LabelFrame(panes, text="床邊 cognitive aid 草稿", padding=4)
        panes.add(preview_frame, weight=2)
        panes.add(result_frame, weight=3)
        self.emergency_preview_text = ScrolledText(
            preview_frame, wrap=tk.WORD, height=7, font=("Consolas", 9)
        )
        self.emergency_preview_text.pack(fill=tk.BOTH, expand=True)
        self.emergency_preview_text.configure(state=tk.DISABLED)
        self.emergency_result_text = ScrolledText(result_frame, wrap=tk.WORD, height=12)
        self.emergency_result_text.pack(fill=tk.BOTH, expand=True)

    def _build_privacy_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="雲端 AI 預設關閉", style="Title.TLabel").pack(anchor="w")
        text = (
            "WardLens 不會把 API key 寫死在 EXE。OpenRouter key 儲存在 Windows Credential Manager；"
            "院內 EMR 帳密只保留於目前登入 session。任何臨床文字送出前都會做 deterministic 去識別、"
            "顯示實際 payload，並核對 preview hash；但這不等於保證匿名，仍須院方核准。"
        )
        ttk.Label(parent, text=text, wraplength=940, justify=tk.LEFT).pack(fill=tk.X, pady=(6, 12))

        ttk.Checkbutton(
            parent,
            text="允許本程式連線外部 AI（OpenRouter）",
            variable=self.external_ai_var,
            command=self._save_privacy_settings,
        ).pack(anchor="w", pady=3)
        ttk.Checkbutton(
            parent,
            text="我已確認院內政策／病人資料使用權限，並會逐次檢查 outbound 預覽",
            variable=self.policy_ack_var,
            command=self._save_privacy_settings,
        ).pack(anchor="w", pady=3)
        ttk.Checkbutton(
            parent,
            text="強制 Zero Data Retention endpoint（fail closed）",
            variable=self.zdr_var,
            command=self._save_privacy_settings,
        ).pack(anchor="w", pady=3)

        key_row = ttk.Frame(parent)
        key_row.pack(fill=tk.X, pady=(15, 8))
        ttk.Button(key_row, text="設定／驗證 OpenRouter API key", command=self._set_api_key).pack(
            side=tk.LEFT
        )
        ttk.Button(key_row, text="刪除已存 API key", command=self._delete_api_key).pack(
            side=tk.LEFT, padx=8
        )

        ttk.Separator(parent).pack(fill=tk.X, pady=12)
        ttk.Label(parent, text="預設模型路由", style="Title.TLabel").pack(anchor="w")
        rows = []
        for key, profile in DEFAULT_PROFILES.items():
            rows.append(
                f"{key:16}  {profile.model:31}  reasoning={profile.reasoning_effort:5}  {profile.intended_use}"
            )
        model_box = ScrolledText(parent, height=9, wrap=tk.NONE, font=("Consolas", 9))
        model_box.pack(fill=tk.X, pady=(6, 0))
        self._replace_text(model_box, "\n".join(rows))
        model_box.configure(state=tk.DISABLED)
        ttk.Label(
            parent,
            text=(
                f"EMR 全域上限：{self.settings.max_requests_per_minute} requests/min；"
                f"檢驗 lookback：{self.settings.lab_lookback_months} 個月；"
                "只載入選取病人的詳細資料。"
            ),
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(10, 0))

    def _new_adapter(self):
        return DemoEMRAdapter() if self.settings.demo_mode else VGHReadOnlyAdapter(self.settings)

    def _switch_mode(self) -> None:
        if self._busy:
            self.demo_var.set(self.settings.demo_mode)
            return
        self._logout(silent=True)
        self.settings.demo_mode = self.demo_var.get()
        self.settings.save()
        self.adapter = self._new_adapter()
        self._apply_mode_state()
        self.status_var.set("Demo 模式" if self.settings.demo_mode else "真實院內模式：尚未登入")

    def _apply_mode_state(self) -> None:
        state = tk.DISABLED if self.demo_var.get() else tk.NORMAL
        self.username_entry.configure(state=state)
        self.password_entry.configure(state=state)

    def _login(self) -> None:
        username = self.username_var.get().strip()
        password = self.password_var.get()
        self.password_var.set("")

        def task() -> None:
            self.adapter.login(username, password)

        self._run_async(
            "正在登入…",
            task,
            lambda _result: self.status_var.set(
                "已登入（Demo）" if self.demo_var.get() else "已登入院內系統"
            ),
        )

    def _logout(self, *, silent: bool = False) -> None:
        if self._busy and not silent:
            messagebox.showwarning("工作進行中", "請等待目前工作完成或先停止生成。")
            return
        try:
            self.adapter.logout()
        except Exception:
            pass
        self._clear_clinical_state()
        self.username_var.set("")
        self.password_var.set("")
        self.status_var.set("已登出並清除記憶體資料")
        if not silent:
            messagebox.showinfo(
                "WardLens", "登入 session、病人清單、已載入病歷與 AI 預覽已從記憶體清除。"
            )

    def _clear_clinical_state(self) -> None:
        self.patients.clear()
        self.bundles.clear()
        self.current_patient = None
        self.current_bundle = None
        self.ai_prepared = None
        self.emergency_prepared = None
        for item in self.patient_tree.get_children():
            self.patient_tree.delete(item)
        self.list_status_var.set("尚未載入病人清單")
        self.selected_var.set("尚未選取病人")
        self._replace_text(self.overview_text, "病人資料已清除。")
        self._replace_text(self.ai_preview_text, "")
        self._replace_text(self.ai_result_text, "")
        self._replace_text(self.emergency_preview_text, "")
        self._replace_text(self.emergency_result_text, "")

    def _search_patients(self) -> None:
        if not self.adapter.logged_in:
            messagebox.showerror("尚未登入", "請先登入；Demo 模式可直接按登入。")
            return
        histnos = [
            value for value in re.split(r"[\s,，;；]+", self.histnos_var.get().strip()) if value
        ]

        def task() -> PatientListResult:
            return self.adapter.search_patients(
                doctor_id=self.doctor_var.get(),
                ward=self.ward_var.get(),
                histnos=histnos or None,
            )

        self._run_async("正在載入完整病人清單…", task, self._show_patient_list)

    def _show_patient_list(self, result: PatientListResult) -> None:
        self._clear_clinical_state()
        for index, patient in enumerate(result.patients, start=1):
            item = f"patient-{index}"
            self.patients[item] = patient
            self.patient_tree.insert(
                "",
                tk.END,
                iid=item,
                values=(
                    patient.location,
                    patient.name,
                    patient.histno,
                    " / ".join(value for value in (patient.age, patient.sex) if value),
                ),
            )
        declared = (
            f"／頁面宣告 {result.declared_total}" if result.declared_total is not None else ""
        )
        completeness = "完整" if result.complete else "不完整，請勿直接使用"
        warning = "；".join(result.warnings[:3])
        self.list_status_var.set(
            f"取得 {len(result.patients)} 人{declared}／{result.pages_fetched} 頁／{completeness}"
            + (f"｜{warning}" if warning else "")
        )
        self.status_var.set(f"清單已載入：{len(result.patients)} 人")

    def _patient_selected(self, _event: tk.Event[Any] | None = None) -> None:
        selection = self.patient_tree.selection()
        if not selection:
            return
        patient = self.patients.get(selection[0])
        if patient is None:
            return
        self.current_patient = patient
        self.current_bundle = self.bundles.get(patient.local_key)
        self.selected_var.set(
            f"{patient.location or '床位未辨識'}｜{patient.name or patient.safe_label}｜{patient.age} {patient.sex}".strip()
        )
        if self.current_bundle is not None:
            self._replace_text(self.overview_text, render_bundle_overview(self.current_bundle))
        else:
            self._replace_text(self.overview_text, "尚未載入此病人的詳細資料；空白不代表沒有異常。")
        self._invalidate_ai_preview()
        self._invalidate_emergency_preview()

    def _load_selected_patient(self) -> None:
        if self.current_patient is None:
            messagebox.showerror("未選取病人", "請先從左側清單選取病人。")
            return
        patient = self.current_patient

        def task() -> PatientBundle:
            return self.adapter.fetch_patient_bundle(patient)

        def success(bundle: PatientBundle) -> None:
            self.bundles[patient.local_key] = bundle
            self.current_bundle = bundle
            self._replace_text(self.overview_text, render_bundle_overview(bundle))
            self.status_var.set(
                f"已載入選取病人：{len(bundle.records)} 個來源／{len(bundle.warnings)} 個警告"
            )
            self._invalidate_ai_preview()
            self._invalidate_emergency_preview()

        self._run_async("正在以唯讀方式載入選取病人…", task, success)

    def _export(self, kind: str) -> None:
        if not self.patients:
            messagebox.showerror("無清單", "請先載入病人清單。")
            return
        if not messagebox.askyesno(
            "確認匯出可識別資料",
            "匯出檔包含姓名、病歷號與床位。請只存放於院方允許的位置，並於使用後依院內規範刪除。繼續？",
        ):
            return
        extension = ".docx" if kind == "docx" else ".csv"
        path_text = filedialog.asksaveasfilename(
            defaultextension=extension,
            filetypes=[("Word document", "*.docx")] if kind == "docx" else [("CSV", "*.csv")],
            initialfile=f"WardLens_patient_list{extension}",
        )
        if not path_text:
            return
        path = Path(path_text)
        patients = list(self.patients.values())
        try:
            if kind == "docx":
                export_patient_list_docx(
                    patients, self.bundles, path, doctor_id=self.doctor_var.get()
                )
            else:
                export_patient_list_csv(patients, path)
        except Exception as exc:
            self._show_error(exc)
            return
        messagebox.showinfo("匯出完成", f"已儲存：{path}")

    def _prepare_ai(self) -> None:
        if self.current_bundle is None:
            messagebox.showerror("病人資料未載入", "請先載入選取病人的詳細資料。")
            return
        workflow = WORKFLOW_LABELS[self.workflow_var.get()]
        question = self.question_text.get("1.0", tk.END).strip()
        try:
            self.ai_prepared = self.ai.prepare(
                workflow, bundle=self.current_bundle, question=question
            )
        except Exception as exc:
            self._show_error(exc)
            return
        self._replace_text(self.ai_preview_text, self.ai_prepared.preview.text)
        self.ai_preview_status_var.set(
            f"已移除 {self.ai_prepared.preview.replacement_count} 個識別項；payload SHA-256 "
            f"{self.ai_prepared.preview.sha256[:16]}…"
        )

    def _prepare_emergency(self) -> None:
        event_text = self.emergency_text.get("1.0", tk.END).strip()
        bundle = self.current_bundle if self.emergency_with_patient_var.get() else None
        if self.emergency_with_patient_var.get() and bundle is None:
            messagebox.showerror("病人資料未載入", "請先載入選取病人，或取消勾選附上病人資料。")
            return
        try:
            self.emergency_prepared = self.ai.prepare(
                "emergency",
                bundle=bundle,
                emergency_text=event_text,
            )
        except Exception as exc:
            self._show_error(exc)
            return
        self._replace_text(self.emergency_preview_text, self.emergency_prepared.preview.text)
        self.emergency_preview_status_var.set(
            f"已移除 {self.emergency_prepared.preview.replacement_count} 個識別項；payload SHA-256 "
            f"{self.emergency_prepared.preview.sha256[:16]}…"
        )

    def _send_ai(self) -> None:
        self._send_prepared(self.ai_prepared, self.profile_var.get(), self.ai_result_text)

    def _send_emergency(self) -> None:
        self._send_prepared(
            self.emergency_prepared,
            self.emergency_profile_var.get(),
            self.emergency_result_text,
        )

    def _send_prepared(
        self, prepared: PreparedRequest | None, profile_key: str, output: ScrolledText
    ) -> None:
        if self._busy:
            messagebox.showwarning("工作進行中", "請等待目前工作完成或先停止生成。")
            return
        if prepared is None:
            messagebox.showerror(
                "尚未預覽", "請先建立並閱讀 outbound 預覽；修改輸入後也必須重新預覽。"
            )
            return
        if not self._cloud_allowed():
            return
        api_key = self._get_api_key()
        if not api_key:
            messagebox.showerror("缺少 API key", "請到「隱私與模型」設定 OpenRouter API key。")
            return
        profile = self.settings.profile(profile_key)
        if prepared.workflow == "emergency" and profile_key in {"deep", "claude_second"}:
            if not messagebox.askyesno(
                "慢速複核模式",
                "這是較慢的第二輪複核，不適合等待中的急救第一輪。已先完成床邊處置並叫支援，仍要使用此模式？",
            ):
                return
        snapshot_note = ""
        if prepared.source_fetched_at is not None:
            fetched_at = prepared.source_fetched_at
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            age_minutes = max(
                0,
                int((datetime.now(UTC) - fetched_at.astimezone(UTC)).total_seconds() / 60),
            )
            snapshot_note = f"\n資料 snapshot 約 {age_minutes} 分鐘前擷取。"
            if age_minutes >= 15 and not messagebox.askyesno(
                "病人資料可能已過時",
                f"這份 snapshot 已約 {age_minutes} 分鐘。建議先重新載入病人資料；仍要送出舊 snapshot？",
            ):
                return
        if not messagebox.askyesno(
            "確認送出",
            f"即將把預覽框中的去識別內容送至 OpenRouter，模型為 {profile.model}。\n\n"
            f"去識別內容仍可能具有再識別風險；請確認預覽與院內授權。{snapshot_note}\n繼續？",
        ):
            return
        banner = (
            "【AI 急症 cognitive aid 草稿｜可能不完整｜不得延誤叫支援或直接當醫囑】\n\n"
            if prepared.workflow == "emergency"
            else "【AI 草稿｜尚未核對來源｜不得直接貼回病歷或當成醫囑】\n\n"
        )
        self._replace_text(output, banner)
        self._cancel_event.clear()
        self._busy = True
        self.status_var.set(f"正在生成：{profile.label}…")

        def worker() -> None:
            try:
                stream = self.ai.stream(
                    api_key,
                    prepared,
                    profile,
                    require_zdr=self.zdr_var.get(),
                )
                last_provider = ""
                try:
                    for event in stream:
                        if self._cancel_event.is_set():
                            stream.close()
                            self.root.after(0, lambda: self.status_var.set("生成已由使用者停止"))
                            return
                        if event.delta:
                            self.root.after(
                                0, lambda text=event.delta: self._append_text(output, text)
                            )
                        if event.provider_name:
                            last_provider = event.provider_name
                finally:
                    try:
                        stream.close()
                    except Exception:
                        pass
                self.root.after(
                    0,
                    lambda: self.status_var.set(
                        f"生成完成｜{profile.model}"
                        + (f"｜{last_provider}" if last_provider else "")
                    ),
                )
            except Exception as exc:
                if self._cancel_event.is_set():
                    self.root.after(0, lambda: self.status_var.set("生成已由使用者停止"))
                else:
                    self.root.after(
                        0,
                        lambda error=exc: self._handle_stream_error(error, output),
                    )
            finally:
                self.root.after(0, self._mark_idle)

        threading.Thread(target=worker, daemon=True, name="wardlens-llm").start()

    def _copy_ai_prompt(self) -> None:
        self._copy_prepared(self.ai_prepared)

    def _copy_emergency_prompt(self) -> None:
        self._copy_prepared(self.emergency_prepared)

    def _copy_prepared(self, prepared: PreparedRequest | None) -> None:
        if prepared is None:
            messagebox.showerror("尚未預覽", "請先建立並閱讀去識別 outbound 預覽。")
            return
        if not self.policy_ack_var.get():
            messagebox.showerror(
                "尚未確認政策", "請先在「隱私與模型」確認院內政策與 outbound 預覽責任。"
            )
            return
        if not messagebox.askyesno(
            "複製去識別 input",
            "剪貼簿可能透過 Windows／手機雲端同步。程式會在設定秒數後嘗試清除，但無法撤回已同步內容。繼續？",
        ):
            return
        try:
            self.ai.record_copy(prepared)
            self.root.clipboard_clear()
            self.root.clipboard_append(prepared.preview.text)
            self.root.update_idletasks()
            self._clipboard_snapshot = prepared.preview.text
            self.root.after(
                self.settings.clipboard_clear_seconds * 1000, self._clear_clipboard_if_unchanged
            )
        except Exception as exc:
            self._show_error(exc)
            return
        self.status_var.set(
            f"已複製去識別 input；約 {self.settings.clipboard_clear_seconds} 秒後嘗試清除"
        )

    def _clear_clipboard_if_unchanged(self) -> None:
        if not self._clipboard_snapshot:
            return
        try:
            if self.root.clipboard_get() == self._clipboard_snapshot:
                self.root.clipboard_clear()
        except tk.TclError:
            pass
        self._clipboard_snapshot = ""

    def _invalidate_ai_preview(self) -> None:
        if self.ai_prepared is not None:
            self.ai_prepared = None
            self.ai_preview_status_var.set("輸入或病人已變更；必須重新建立預覽")

    def _invalidate_emergency_preview(self) -> None:
        if self.emergency_prepared is not None:
            self.emergency_prepared = None
            self.emergency_preview_status_var.set("輸入或病人已變更；必須重新建立預覽")

    def _cloud_allowed(self) -> bool:
        if not self.external_ai_var.get() or not self.policy_ack_var.get():
            messagebox.showerror(
                "外部 AI 未啟用",
                "請到「隱私與模型」同時啟用外部 AI，並確認院內政策／資料使用權限。",
            )
            return False
        if not self.zdr_var.get() and not messagebox.askyesno(
            "ZDR 已關閉",
            "目前沒有強制 Zero Data Retention endpoint。這會增加資料留存風險，仍要繼續嗎？",
        ):
            return False
        return True

    def _save_privacy_settings(self) -> None:
        self.settings.external_ai_enabled = self.external_ai_var.get()
        self.settings.privacy_acknowledged = self.policy_ack_var.get()
        self.settings.require_zdr = self.zdr_var.get()
        self.settings.save()

    def _set_api_key(self) -> None:
        key = simpledialog.askstring(
            "OpenRouter API key",
            "貼上 key（不會顯示；驗證成功後存入 Windows Credential Manager）：",
            show="•",
            parent=self.root,
        )
        if not key:
            return
        key = key.strip()

        def task() -> str:
            self.ai.client.verify_key(key)
            self.secret_store.set("openrouter_api_key", key)
            return key

        def success(value: str) -> None:
            self._session_api_key = value
            messagebox.showinfo("API key", "驗證成功，已存入作業系統憑證庫。")

        self._run_async("正在驗證 OpenRouter API key…", task, success)

    def _delete_api_key(self) -> None:
        if not messagebox.askyesno(
            "刪除 API key", "確定從作業系統憑證庫刪除 WardLens 的 OpenRouter key？"
        ):
            return
        try:
            self.secret_store.delete("openrouter_api_key")
            self._session_api_key = ""
        except Exception as exc:
            self._show_error(exc)
            return
        messagebox.showinfo("API key", "已刪除。")

    def _get_api_key(self) -> str:
        if self._session_api_key:
            return self._session_api_key
        try:
            self._session_api_key = self.secret_store.get("openrouter_api_key") or ""
        except SecretStoreError as exc:
            self._show_error(exc)
            return ""
        return self._session_api_key

    def _cancel_generation(self) -> None:
        self._cancel_event.set()
        self.ai.client.cancel()
        self.status_var.set("正在停止生成…")

    def _emergency_profile_changed(self) -> None:
        key = self.emergency_profile_var.get()
        if key in {"deep", "claude_second"}:
            self.emergency_mode_warning_var.set(
                "此為較慢的第二輪複核；急症第一輪請先用 emergency_fast，且不要等待 AI 才叫支援。"
            )
        else:
            self.emergency_mode_warning_var.set("急症第一輪仍須同步處置與叫支援，不要等待模型。")

    def _handle_stream_error(self, exc: Exception, output: ScrolledText) -> None:
        self._append_text(output, "\n\n【生成中斷：以上內容不完整，不可依此執行完整處置】")
        self._show_error(exc)

    def _run_async(
        self,
        label: str,
        task: Callable[[], Any],
        success: Callable[[Any], None],
    ) -> None:
        if self._busy:
            messagebox.showwarning("工作進行中", "請等待目前工作完成或先停止生成。")
            return
        self._busy = True
        self.status_var.set(label)

        def worker() -> None:
            try:
                result = task()
            except Exception as exc:
                self.root.after(0, lambda error=exc: self._show_error(error))
            else:
                self.root.after(0, lambda value=result: self._finish_success(success, value))
            finally:
                self.root.after(0, self._mark_idle)

        threading.Thread(target=worker, daemon=True, name="wardlens-task").start()

    def _finish_success(self, callback: Callable[[Any], None], value: Any) -> None:
        try:
            callback(value)
        except Exception as exc:
            self._show_error(exc)

    def _mark_idle(self) -> None:
        self._busy = False

    def _show_error(self, exc: Exception) -> None:
        if isinstance(exc, DataLeakRisk):
            title = "去識別檢查未通過"
            detail = str(exc)
        elif isinstance(exc, (EMRError, OpenRouterError, SecretStoreError, ValueError)):
            title = "無法完成"
            detail = str(exc)
        else:
            title = "未預期錯誤"
            detail = "發生未預期錯誤；請保留程式版本與操作步驟回報。病人內容不會寫入 log。"
        self.status_var.set(detail)
        messagebox.showerror(title, detail)

    @staticmethod
    def _replace_text(widget: ScrolledText, value: str) -> None:
        old_state = str(widget.cget("state"))
        if old_state == tk.DISABLED:
            widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        if old_state == tk.DISABLED:
            widget.configure(state=tk.DISABLED)

    @staticmethod
    def _append_text(widget: ScrolledText, value: str) -> None:
        widget.insert(tk.END, value)
        widget.see(tk.END)

    def _on_close(self) -> None:
        self._cancel_event.set()
        self.ai.client.cancel()
        self._clear_clipboard_if_unchanged()
        try:
            self.adapter.logout()
        except Exception:
            pass
        self._clear_clinical_state()
        self._session_api_key = ""
        self.root.destroy()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WardLens portable rounding assistant")
    parser.add_argument("--demo", action="store_true", help="start with synthetic demo data")
    parser.add_argument("--version", action="version", version=f"WardLens {__version__}")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    root = tk.Tk()
    WardLensApp(root, force_demo=args.demo)
    root.mainloop()
