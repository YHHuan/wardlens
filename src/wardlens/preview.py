from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from wardlens.llm.prompts import PromptBuilder, PromptEnvelope
from wardlens.models import PatientSummary
from wardlens.security.deidentify import Deidentifier
from wardlens.security.outbound import PreviewBlocked
from wardlens.services.ai import PreparedRequest


class PreviewConfirmationDialog:
    """On-demand review; background instructions collapsed, exact bytes accessible."""

    def __init__(self, parent: tk.Misc, *, prepared: PreparedRequest) -> None:
        self.approved = False
        self.window = tk.Toplevel(parent)
        self.window.title("確認送出的去識別資料｜尚未連線 AI")
        self.window.geometry("1000x700")
        self.window.transient(parent)
        self.window.grab_set()
        outer = ttk.Frame(self.window, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            outer, wraplength=950,
            text="以下是去識別後的資料與問題（非 AI 回答）。需要修改請返回主畫面的資料框／問題框。"
                 "背景 prompt 預設收合，仍會一起送出；請確認沒有可識別資訊。",
        ).pack(fill=tk.X)
        self.clinical_text = ScrolledText(outer, height=15, wrap=tk.WORD)
        self.clinical_text.pack(fill=tk.BOTH, expand=True, pady=8)
        self.clinical_text.insert("1.0", PromptBuilder.clinical_preview(prepared.envelope()))
        self.clinical_text.configure(state=tk.DISABLED)
        self.show_full = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            outer, text="進階：顯示背景 prompt 與完整傳送內容", variable=self.show_full,
            command=self._toggle_full,
        ).pack(anchor="w")
        self.advanced_frame = ttk.LabelFrame(outer, text="完整 input（唯讀，與核對 hash 對應）")
        full = ScrolledText(self.advanced_frame, height=8, wrap=tk.WORD)
        self.full_text = full
        full.pack(fill=tk.BOTH, expand=True)
        full.insert("1.0", prepared.preview.text)
        full.configure(state=tk.DISABLED)
        self.actions = ttk.Frame(outer)
        self.actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(self.actions, text=f"SHA-256 {prepared.preview.sha256[:16]}…").pack(side=tk.LEFT)
        ttk.Button(self.actions, text="確認此預覽", command=self._approve).pack(side=tk.RIGHT)
        ttk.Button(self.actions, text="返回修改／取消", command=self.window.destroy).pack(side=tk.RIGHT, padx=8)

    def _toggle_full(self) -> None:
        if self.show_full.get():
            self.advanced_frame.pack(fill=tk.BOTH, expand=True, before=self.actions, pady=5)
        else:
            self.advanced_frame.pack_forget()

    def _approve(self) -> None:
        self.approved = True
        self.window.destroy()


class PreviewReviewDialog:
    """Local, memory-only repair. Applying creates a preview, never a network request."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        blocked: PreviewBlocked,
        patient: PatientSummary | None,
        recheck: Callable[[str], PreparedRequest],
    ) -> None:
        self.result: PreparedRequest | None = None
        self.patient = patient
        self.recheck = recheck
        self.scanner = Deidentifier()
        self.window = tk.Toplevel(parent)
        self.window.title("本機去識別修正｜尚未送出任何資料")
        self.window.geometry("1050x720")
        self.window.transient(parent)
        self.window.grab_set()
        outer = ttk.Frame(self.window, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            outer,
            wraplength=990,
            foreground="#a32121",
            text=(
                "標記處可能是病歷號、檢體／報告編號，也可能是臨床數值。請先核對，再遮罩識別碼。"
                "草稿只留本機記憶體，不會更動原始病歷。這裡只修正本次預覽，不會覆蓋主頁的原始資料框。"
            ),
        ).pack(fill=tk.X)
        self.status = tk.StringVar()
        ttk.Label(outer, textvariable=self.status).pack(anchor="w", pady=6)
        self.text = ScrolledText(outer, wrap=tk.NONE, undo=False, font=("Consolas", 10))
        self.text.pack(fill=tk.BOTH, expand=True)
        envelope = PromptEnvelope.parse(blocked.result.text)
        self.text.insert("1.0", envelope.user)
        self.text.tag_configure("risk", background="#ffd6a5", foreground="#642800")
        self.show_system = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            outer, text="進階：顯示背景 prompt（僅此預覽）", variable=self.show_system,
            command=self._toggle_system,
        ).pack(anchor="w", pady=4)
        self.system_frame = ttk.LabelFrame(outer, text="背景 prompt")
        self.system_text = ScrolledText(self.system_frame, wrap=tk.WORD, height=7, undo=False)
        self.system_text.pack(fill=tk.BOTH, expand=True)
        self.system_text.insert("1.0", envelope.system)
        self.system_text.tag_configure("risk", background="#ffd6a5", foreground="#642800")
        buttons = ttk.Frame(outer)
        self.buttons = buttons
        buttons.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(buttons, text="重新標記／跳到第一處", command=self._highlight).pack(side=tk.LEFT)
        ttk.Button(buttons, text="遮罩選取文字", command=self._mask_selection).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(buttons, text="遮罩全部疑似代碼…", command=self._mask_all).pack(side=tk.LEFT)
        ttk.Button(buttons, text="取消", command=self.window.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="重新檢查並建立預覽", command=self._apply).pack(
            side=tk.RIGHT, padx=8
        )
        self._highlight()

    def _draft(self) -> str:
        return PromptEnvelope(
            system=self.system_text.get("1.0", "end-1c"), user=self.text.get("1.0", "end-1c"),
        ).canonical()

    def _toggle_system(self) -> None:
        if self.show_system.get():
            self.system_frame.pack(fill=tk.BOTH, expand=True, before=self.buttons)
        else:
            self.system_frame.pack_forget()

    def _highlight(self) -> None:
        count = 0
        for widget in (self.text, self.system_text):
            findings = self.scanner.scan(widget.get("1.0", "end-1c"), self.patient)
            count += len(findings)
            widget.tag_remove("risk", "1.0", tk.END)
            for finding in findings:
                widget.tag_add("risk", f"1.0+{finding.start}c", f"1.0+{finding.end}c")
            if findings:
                if widget is self.system_text:
                    self.show_system.set(True)
                    self._toggle_system()
                widget.see(f"1.0+{findings[0].start}c")
        self.status.set(f"疑似識別項：{count} 處。修正後仍須重新檢查；通過不代表已送出。")

    def _mask_selection(self) -> None:
        selected = next((w for w in (self.text, self.system_text) if w.tag_ranges(tk.SEL)), None)
        if selected is None:
            messagebox.showinfo("先選取文字", "請反白選取需要遮罩的識別碼。", parent=self.window)
            return
        selected.replace(tk.SEL_FIRST, tk.SEL_LAST, "[MANUAL_IDENTIFIER_REMOVED]")
        self._highlight()

    def _mask_all(self) -> None:
        draft = self._draft()
        findings = self.scanner.scan(draft, self.patient)
        if not findings:
            self._highlight()
            return
        if not messagebox.askyesno(
            "確認全部遮罩",
            "這會隱藏所有橘色標記，包括可能被誤判的臨床數值。"
            "請先檢查；遮罩後也要再次閱讀預覽。繼續？",
            parent=self.window,
        ):
            return
        self._replace_envelope(self.scanner.mask_findings(draft, findings))
        self._highlight()

    def _replace_envelope(self, text: str) -> None:
        envelope = PromptEnvelope.parse(text)
        for widget, value in ((self.text, envelope.user), (self.system_text, envelope.system)):
            widget.delete("1.0", tk.END)
            widget.insert("1.0", value)

    def _apply(self) -> None:
        try:
            prepared = self.recheck(self._draft())
        except PreviewBlocked as exc:
            # Show any additional automatic redactions before another manual pass.
            self._replace_envelope(exc.result.text)
            self._highlight()
            messagebox.showwarning("尚未通過", str(exc), parent=self.window)
        except ValueError:
            messagebox.showerror(
                "預覽格式已變更",
                "請保留非空的資料與背景 prompt；可取消後從主頁重新建立。",
                parent=self.window,
            )
        else:
            self.result = prepared
            self.window.destroy()
