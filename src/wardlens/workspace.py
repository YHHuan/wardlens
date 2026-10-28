from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from wardlens.llm.prompts import PromptBuilder
from wardlens.models import EMR_TIMEZONE, PatientBundle


class ClinicalWorkspace(ttk.Frame):
    """Two editable, session-only inputs and one answer, shared by both AI tabs."""

    def __init__(
        self, parent, *, emergency: bool, preview_status: tk.StringVar,
        on_change: Callable[[], None], on_load: Callable[[], None],
        on_refresh: Callable[[], None], on_preview: Callable[[], None],
        on_send: Callable[[], None], on_copy: Callable[[], None],
        on_stop: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.bundle: PatientBundle | None = None
        self.dirty = False
        self.on_change = on_change
        self.data_status = tk.StringVar(value="可貼上資料，或帶入已載入的病人；只留本次記憶體。")
        self.preview_status = preview_status
        panes = ttk.Panedwindow(self, orient=tk.VERTICAL)
        panes.pack(fill=tk.BOTH, expand=True)
        input_area = ttk.Frame(panes)
        answer_area = ttk.LabelFrame(panes, text="AI 回答草稿｜請核對來源，不可直接當醫囑", padding=5)
        panes.add(input_area, weight=3)
        panes.add(answer_area, weight=3)
        inputs = ttk.Panedwindow(input_area, orient=tk.HORIZONTAL)
        inputs.pack(fill=tk.BOTH, expand=True)
        data = ttk.LabelFrame(inputs, text="① 病人資料｜可直接補充、刪改", padding=5)
        question = ttk.LabelFrame(
            inputs, text="② 目前病況／想問的問題" if emergency else "② 想問的問題｜查房整理可留空", padding=5,
        )
        inputs.add(data, weight=3)
        inputs.add(question, weight=2)
        tools = ttk.Frame(data)
        tools.pack(fill=tk.X)
        self.data_buttons = []
        for label, callback in (
            ("帶入已載入資料", on_load), ("重新抓取", on_refresh), ("清空", self.clear_data),
        ):
            button = ttk.Button(tools, text=label, command=callback)
            button.pack(side=tk.LEFT, padx=(0, 4))
            self.data_buttons.append(button)
        ttk.Label(data, textvariable=self.data_status, wraplength=460, foreground="#555555").pack(fill=tk.X, pady=4)
        self.data_text = ScrolledText(data, height=9, width=40, wrap=tk.WORD, undo=False)
        self.data_text.pack(fill=tk.BOTH, expand=True)
        ttk.Label(question, text="可用 Win+H 語音輸入；不必編輯背景 prompt。", wraplength=300).pack(fill=tk.X, pady=4)
        self.question_text = ScrolledText(question, height=9, width=28, wrap=tk.WORD, undo=False)
        self.question_text.pack(fill=tk.BOTH, expand=True)
        for widget in (self.data_text, self.question_text):
            widget.bind("<<Modified>>", lambda _event, w=widget: self._modified(w))
        actions = ttk.Frame(input_area)
        actions.pack(fill=tk.X, pady=6)
        for label, callback in (
            ("檢查送出資料…", on_preview), ("送出 AI", on_send),
            ("複製 input", on_copy), ("停止", on_stop),
        ):
            ttk.Button(actions, text=label, command=callback).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Label(input_area, textvariable=preview_status, wraplength=900, foreground="#555555").pack(fill=tk.X)
        self.result_text = ScrolledText(answer_area, height=10, wrap=tk.WORD, undo=False)
        self.result_text.pack(fill=tk.BOTH, expand=True)

    @staticmethod
    def _replace(widget: ScrolledText, text: str) -> None:
        previous = str(widget.cget("state"))
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text)
        widget.edit_modified(False)
        widget.configure(state=previous)

    def _modified(self, widget: ScrolledText) -> None:
        if not widget.edit_modified():
            return
        widget.edit_modified(False)
        if widget is self.data_text:
            self.dirty = True
            self.data_status.set("資料已在本機編輯；只使用此框內容，不會暗中附回原始病歷。")
        self.on_change()

    def load_bundle(self, bundle: PatientBundle) -> None:
        self.bundle = bundle
        self._replace(self.data_text, PromptBuilder().editor_context(bundle))
        self._replace(self.result_text, "")
        self.dirty = False
        when = bundle.fetched_at.astimezone(EMR_TIMEZONE).strftime("%m/%d %H:%M")
        self.data_status.set(f"已帶入 {when} 的病人資料；可直接編輯，重新抓取才會更新。")
        self.on_change()

    def clear_data(self) -> None:
        # Keep known identifiers for redaction, but the empty string still replaces
        # all automatic chart context in PromptBuilder.
        self._replace(self.data_text, "")
        self.dirty = True
        self.data_status.set("資料框已清空；不會附上隱藏病歷，可自行貼上／輸入。")
        self.on_change()

    def clear(self) -> None:
        self.bundle = None
        self.dirty = False
        for widget in (self.data_text, self.question_text, self.result_text):
            self._replace(widget, "")
        self.data_status.set("可貼上資料，或帶入已載入的病人；只留本次記憶體。")
        self.on_change()

    def set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        for widget in (self.data_text, self.question_text, *self.data_buttons):
            widget.configure(state=state)

    def context(self) -> str:
        return self.data_text.get("1.0", "end-1c").strip()
