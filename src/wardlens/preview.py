from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from wardlens.models import PatientSummary
from wardlens.security.deidentify import Deidentifier
from wardlens.security.outbound import PreviewBlocked
from wardlens.services.ai import PreparedRequest


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
                "草稿只留本機記憶體，不會更動原始病歷。不要刪除 SYSTEM／USER 邊界標記。"
            ),
        ).pack(fill=tk.X)
        self.status = tk.StringVar()
        ttk.Label(outer, textvariable=self.status).pack(anchor="w", pady=6)
        self.text = ScrolledText(outer, wrap=tk.NONE, undo=False, font=("Consolas", 10))
        self.text.pack(fill=tk.BOTH, expand=True)
        self.text.insert("1.0", blocked.result.text)
        self.text.tag_configure("risk", background="#ffd6a5", foreground="#642800")
        buttons = ttk.Frame(outer)
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
        return self.text.get("1.0", "end-1c")

    def _highlight(self) -> None:
        findings = self.scanner.scan(self._draft(), self.patient)
        self.text.tag_remove("risk", "1.0", tk.END)
        for finding in findings:
            self.text.tag_add("risk", f"1.0+{finding.start}c", f"1.0+{finding.end}c")
        self.status.set(f"疑似識別項：{len(findings)} 處。修正後仍須重新檢查；通過不代表已送出。")
        if findings:
            self.text.see(f"1.0+{findings[0].start}c")

    def _mask_selection(self) -> None:
        if not self.text.tag_ranges(tk.SEL):
            messagebox.showinfo("先選取文字", "請反白選取需要遮罩的識別碼。", parent=self.window)
            return
        self.text.replace(tk.SEL_FIRST, tk.SEL_LAST, "[MANUAL_IDENTIFIER_REMOVED]")
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
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", self.scanner.mask_findings(draft, findings))
        self._highlight()

    def _apply(self) -> None:
        try:
            prepared = self.recheck(self._draft())
        except PreviewBlocked as exc:
            # Show any additional automatic redactions before another manual pass.
            self.text.delete("1.0", tk.END)
            self.text.insert("1.0", exc.result.text)
            self._highlight()
            messagebox.showwarning("尚未通過", str(exc), parent=self.window)
        except ValueError:
            messagebox.showerror(
                "預覽格式已變更",
                "請保留 SYSTEM／USER 邊界與非空內容；可取消後重新建立。",
                parent=self.window,
            )
        else:
            self.result = prepared
            self.window.destroy()
