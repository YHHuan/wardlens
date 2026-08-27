from __future__ import annotations

import json
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from wardlens.config import (
    ALLOWED_REASONING_EFFORTS,
    DEFAULT_PROFILES,
    PROMPT_WORKFLOWS,
    AppSettings,
)
from wardlens.llm.openrouter import OpenRouterClient
from wardlens.llm.prompts import PromptBuilder


class DeveloperSettingsDialog:
    """Non-secret local editor for model routes and system prompts."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        settings: AppSettings,
        client: OpenRouterClient,
        on_saved: Callable[[], None],
    ) -> None:
        self.parent = parent
        self.settings = settings
        self.client = client
        self.on_saved = on_saved
        self.default_builder = PromptBuilder()
        current_builder = PromptBuilder(overrides=settings.custom_prompts)
        self.working_prompts = {
            workflow: current_builder.system_prompt(workflow) for workflow in PROMPT_WORKFLOWS
        }
        self.current_workflow = "rounding"
        self.model_vars: dict[str, tuple[tk.StringVar, tk.StringVar, tk.StringVar]] = {}
        self.model_boxes: list[ttk.Combobox] = []
        self.status_var = tk.StringVar(value="尚未查詢 OpenRouter 模型目錄")
        self.prompt_workflow_var = tk.StringVar(value=self.current_workflow)

        self.window = tk.Toplevel(parent)
        self.window.title("WardLens 開發者模式｜不包含 API key")
        self.window.geometry("1120x760")
        self.window.minsize(900, 620)
        self.window.transient(parent)
        self.window.grab_set()
        self._build()

    def _build(self) -> None:
        outer = ttk.Frame(self.window, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            outer,
            text=(
                "修改會直接影響臨床草稿。每次變更後請先用合成資料驗證；"
                "自訂設定不包含 API key，但匯出的 JSON 會包含完整 prompt，請勿把病人資料寫進 prompt。"
            ),
            foreground="#a32121",
            wraplength=1040,
        ).pack(fill=tk.X, pady=(0, 8))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill=tk.BOTH, expand=True)
        models_tab = ttk.Frame(notebook, padding=10)
        prompts_tab = ttk.Frame(notebook, padding=10)
        notebook.add(models_tab, text="模型路由")
        notebook.add(prompts_tab, text="System prompts")
        self._build_models(models_tab)
        self._build_prompts(prompts_tab)

        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(buttons, text="匯入非機密設定…", command=self._import_config).pack(side=tk.LEFT)
        ttk.Button(buttons, text="匯出非機密設定…", command=self._export_config).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(buttons, text="全部回復內建值", command=self._restore_defaults).pack(
            side=tk.LEFT, padx=(14, 0)
        )
        ttk.Button(buttons, text="取消", command=self.window.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="驗證並儲存", command=self._save).pack(side=tk.RIGHT, padx=(0, 8))

    def _build_models(self, parent: ttk.Frame) -> None:
        ttk.Label(
            parent,
            text=(
                "傳輸端固定為 OpenRouter；模型 ID 可直接輸入或從即時目錄選取。"
                "勾選 ZDR 時，刷新只顯示目前具有 ZDR endpoint 的模型。"
            ),
            wraplength=1000,
        ).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 10))
        for column, title in enumerate(("路由", "模型 ID", "Reasoning", "Max tokens", "用途")):
            ttk.Label(parent, text=title).grid(row=1, column=column, sticky="w", padx=4)

        for row, (key, default) in enumerate(DEFAULT_PROFILES.items(), start=2):
            current = self.settings.profile(key)
            model_var = tk.StringVar(value=current.model)
            effort_var = tk.StringVar(value=current.reasoning_effort)
            tokens_var = tk.StringVar(value=str(current.max_tokens))
            self.model_vars[key] = (model_var, effort_var, tokens_var)
            ttk.Label(parent, text=f"{key}\n{default.label}").grid(
                row=row, column=0, sticky="w", padx=4, pady=5
            )
            model_box = ttk.Combobox(parent, textvariable=model_var, width=42)
            model_box.grid(row=row, column=1, sticky="ew", padx=4, pady=5)
            self.model_boxes.append(model_box)
            ttk.Combobox(
                parent,
                textvariable=effort_var,
                values=ALLOWED_REASONING_EFFORTS,
                state="readonly",
                width=10,
            ).grid(row=row, column=2, sticky="w", padx=4, pady=5)
            ttk.Entry(parent, textvariable=tokens_var, width=10).grid(
                row=row, column=3, sticky="w", padx=4, pady=5
            )
            ttk.Label(parent, text=default.intended_use, wraplength=260).grid(
                row=row, column=4, sticky="w", padx=4, pady=5
            )

        action_row = len(DEFAULT_PROFILES) + 2
        action = ttk.Frame(parent)
        action.grid(row=action_row, column=0, columnspan=5, sticky="w", padx=4, pady=(14, 4))
        ttk.Button(
            action,
            text="刷新目前 ZDR 模型目錄",
            command=lambda: self._refresh_models(zdr_only=True),
        ).pack(side=tk.LEFT)
        ttk.Button(
            action,
            text="刷新全部模型目錄",
            command=lambda: self._refresh_models(zdr_only=False),
        ).pack(side=tk.LEFT, padx=8)
        ttk.Label(parent, textvariable=self.status_var, foreground="#555555").grid(
            row=action_row + 1, column=0, columnspan=5, sticky="w", padx=4
        )
        parent.columnconfigure(1, weight=1)

    def _build_prompts(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent)
        controls.pack(fill=tk.X)
        ttk.Label(controls, text="工作").pack(side=tk.LEFT)
        selector = ttk.Combobox(
            controls,
            textvariable=self.prompt_workflow_var,
            values=PROMPT_WORKFLOWS,
            state="readonly",
            width=18,
        )
        selector.pack(side=tk.LEFT, padx=6)
        selector.bind("<<ComboboxSelected>>", self._switch_prompt)
        ttk.Label(
            controls,
            text="內容會完整出現在 outbound 預覽中",
            foreground="#555555",
        ).pack(side=tk.LEFT, padx=10)
        ttk.Button(controls, text="此項回復內建值", command=self._reset_current_prompt).pack(
            side=tk.RIGHT
        )

        self.prompt_text = ScrolledText(parent, wrap=tk.WORD, font=("Consolas", 10))
        self.prompt_text.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.prompt_text.insert("1.0", self.working_prompts[self.current_workflow])

    def _capture_prompt(self) -> None:
        self.working_prompts[self.current_workflow] = self.prompt_text.get("1.0", tk.END).strip()

    def _switch_prompt(self, _event: object | None = None) -> None:
        self._capture_prompt()
        self.current_workflow = self.prompt_workflow_var.get()
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert("1.0", self.working_prompts[self.current_workflow])

    def _reset_current_prompt(self) -> None:
        workflow = self.current_workflow
        value = self.default_builder.system_prompt(workflow)
        self.working_prompts[workflow] = value
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert("1.0", value)

    def _refresh_models(self, *, zdr_only: bool) -> None:
        self.status_var.set("正在讀取 OpenRouter 即時模型目錄…")

        def worker() -> None:
            try:
                models = self.client.available_models(zdr_only=zdr_only)
            except Exception as exc:
                self.window.after(0, lambda error=exc: self._show_refresh_error(error))
                return
            self.window.after(0, lambda values=models: self._apply_model_catalog(values, zdr_only))

        threading.Thread(target=worker, daemon=True, name="wardlens-model-catalog").start()

    def _apply_model_catalog(self, models: list[str], zdr_only: bool) -> None:
        if not self.window.winfo_exists():
            return
        for box in self.model_boxes:
            box.configure(values=models)
        scope = "ZDR" if zdr_only else "全部"
        self.status_var.set(f"已取得 {len(models)} 個{scope}模型；可從下拉選取或直接輸入 ID。")

    def _show_refresh_error(self, exc: Exception) -> None:
        if not self.window.winfo_exists():
            return
        self.status_var.set(str(exc))
        messagebox.showerror("模型目錄", str(exc), parent=self.window)

    def _candidate_payload(self) -> dict[str, object]:
        self._capture_prompt()
        candidate = AppSettings()
        for key, (model_var, effort_var, tokens_var) in self.model_vars.items():
            candidate.set_profile_override(
                key,
                model=model_var.get(),
                reasoning_effort=effort_var.get(),
                max_tokens=tokens_var.get(),
            )
        for workflow, prompt in self.working_prompts.items():
            candidate.set_prompt_override(
                workflow, prompt, self.default_builder.system_prompt(workflow)
            )
        return candidate.developer_payload()

    def _save(self) -> None:
        previous = self.settings.developer_payload()
        try:
            payload = self._candidate_payload()
            self.settings.apply_developer_payload(payload)
            self.settings.save()
        except Exception as exc:
            self.settings.apply_developer_payload(previous)
            messagebox.showerror("設定無效", str(exc), parent=self.window)
            return
        self.on_saved()
        messagebox.showinfo(
            "開發者設定",
            "已儲存。現有 outbound 預覽已失效，請用合成資料重新建立並檢查。",
            parent=self.window,
        )
        self.window.destroy()

    def _restore_defaults(self) -> None:
        if not messagebox.askyesno(
            "回復內建值",
            "將畫面中的所有模型與 prompt 回復內建值；按「驗證並儲存」後才會套用。繼續？",
            parent=self.window,
        ):
            return
        for key, default in DEFAULT_PROFILES.items():
            model_var, effort_var, tokens_var = self.model_vars[key]
            model_var.set(default.model)
            effort_var.set(default.reasoning_effort)
            tokens_var.set(str(default.max_tokens))
        self.working_prompts = {
            workflow: self.default_builder.system_prompt(workflow) for workflow in PROMPT_WORKFLOWS
        }
        self.current_workflow = self.prompt_workflow_var.get()
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert("1.0", self.working_prompts[self.current_workflow])

    def _export_config(self) -> None:
        try:
            payload = self._candidate_payload()
        except Exception as exc:
            messagebox.showerror("設定無效", str(exc), parent=self.window)
            return
        path_text = filedialog.asksaveasfilename(
            parent=self.window,
            defaultextension=".json",
            filetypes=[("WardLens developer config", "*.json")],
            initialfile="wardlens-developer-config.json",
        )
        if not path_text:
            return
        try:
            Path(path_text).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            messagebox.showerror("無法匯出", str(exc), parent=self.window)
            return
        messagebox.showinfo(
            "匯出完成",
            "已匯出模型與完整 prompt；程式不會加入 API key。若你曾把敏感資料寫進 prompt，檔案也會包含它，請自行核對。",
            parent=self.window,
        )

    def _import_config(self) -> None:
        path_text = filedialog.askopenfilename(
            parent=self.window,
            filetypes=[("WardLens developer config", "*.json"), ("JSON", "*.json")],
        )
        if not path_text:
            return
        try:
            path = Path(path_text)
            if path.stat().st_size > 1_000_000:
                raise ValueError("設定檔超過 1 MB，已拒絕匯入。")
            payload = json.loads(path.read_text(encoding="utf-8"))
            candidate = AppSettings()
            candidate.apply_developer_payload(payload)
        except Exception as exc:
            messagebox.showerror("無法匯入", str(exc), parent=self.window)
            return
        for key in DEFAULT_PROFILES:
            profile = candidate.profile(key)
            model_var, effort_var, tokens_var = self.model_vars[key]
            model_var.set(profile.model)
            effort_var.set(profile.reasoning_effort)
            tokens_var.set(str(profile.max_tokens))
        builder = PromptBuilder(overrides=candidate.custom_prompts)
        self.working_prompts = {
            workflow: builder.system_prompt(workflow) for workflow in PROMPT_WORKFLOWS
        }
        self.current_workflow = self.prompt_workflow_var.get()
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert("1.0", self.working_prompts[self.current_workflow])
        messagebox.showinfo(
            "匯入完成",
            "已載入畫面；請檢查後按「驗證並儲存」。",
            parent=self.window,
        )
