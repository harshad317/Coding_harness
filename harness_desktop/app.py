"""Tkinter desktop app for the coding harness."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .service import DesktopHarnessService, DesktopRunRequest, DesktopRunResult


class CodingHarnessApp(tk.Tk):
    def __init__(self, service: DesktopHarnessService | None = None):
        super().__init__()
        self.title("D_val Coding Harness")
        self.geometry("1180x820")
        self.minsize(980, 680)
        self.service = service or DesktopHarnessService()
        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.current_diff_path: Path | None = None
        self.running = False

        self._configure_style()
        self._build_layout()
        self.after(150, self._drain_events)

    def _configure_style(self) -> None:
        self.configure(bg="#f4f6f8")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#f4f6f8")
        style.configure("Panel.TFrame", background="#ffffff", relief="solid", borderwidth=1)
        style.configure("TLabel", background="#f4f6f8", foreground="#202124")
        style.configure("Panel.TLabel", background="#ffffff", foreground="#202124")
        style.configure("Header.TLabel", font=("TkDefaultFont", 18, "bold"), background="#f4f6f8")
        style.configure("Section.TLabel", font=("TkDefaultFont", 12, "bold"), background="#ffffff")
        style.configure("Primary.TButton", font=("TkDefaultFont", 11, "bold"))

    def _build_layout(self) -> None:
        top = ttk.Frame(self, padding=(18, 14))
        top.pack(fill=tk.X)
        ttk.Label(top, text="D_val Coding Harness", style="Header.TLabel").pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(top, textvariable=self.status_var).pack(side=tk.RIGHT)

        body = ttk.Frame(self, padding=(16, 0, 16, 16))
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=0, minsize=360)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        controls = ttk.Frame(body, style="Panel.TFrame", padding=14)
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        controls.columnconfigure(0, weight=1)

        ttk.Label(controls, text="Repo Run", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.repo_path = tk.StringVar()
        self._entry_row(controls, "Repo Path", self.repo_path, row=1, browse=True)

        ttk.Label(controls, text="Instruction", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=(10, 4))
        self.instruction = tk.Text(controls, height=8, wrap=tk.WORD, relief=tk.SOLID, borderwidth=1)
        self.instruction.insert("1.0", "Fix the failing tests and keep changes minimal.")
        self.instruction.grid(row=4, column=0, sticky="ew")

        self.test_command = tk.StringVar(value="python -m pytest -q")
        self.model = tk.StringVar(value="gpt-5.4-mini")
        self.max_iterations = tk.StringVar(value="3")
        self.max_bash_calls = tk.StringVar(value="10")
        self.repo_timeout = tk.StringVar(value="60")
        self.max_tokens = tk.StringVar(value="4096")
        self.temperature = tk.StringVar(value="0")
        self.model_timeout = tk.StringVar(value="300")
        self.max_repo_bytes = tk.StringVar(value="200000")
        self.max_file_bytes = tk.StringVar(value="30000")
        self.apply_changes = tk.BooleanVar(value=False)

        self._entry_row(controls, "Test Command", self.test_command, row=5)
        self._entry_row(controls, "Model", self.model, row=7)
        grid = ttk.Frame(controls, style="Panel.TFrame")
        grid.grid(row=9, column=0, sticky="ew", pady=(8, 0))
        for idx in range(2):
            grid.columnconfigure(idx, weight=1)
        fields = [
            ("Iterations", self.max_iterations),
            ("Bash Budget", self.max_bash_calls),
            ("Repo Timeout", self.repo_timeout),
            ("Max Tokens", self.max_tokens),
            ("Temperature", self.temperature),
            ("Model Timeout", self.model_timeout),
            ("Repo Bytes", self.max_repo_bytes),
            ("File Bytes", self.max_file_bytes),
        ]
        for idx, (label, var) in enumerate(fields):
            frame = ttk.Frame(grid, style="Panel.TFrame")
            frame.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=(0 if idx % 2 == 0 else 6, 6 if idx % 2 == 0 else 0), pady=4)
            ttk.Label(frame, text=label, style="Panel.TLabel").pack(anchor="w")
            ttk.Entry(frame, textvariable=var).pack(fill=tk.X, pady=(3, 0))

        ttk.Checkbutton(controls, text="Apply clean run to source repo", variable=self.apply_changes).grid(row=10, column=0, sticky="w", pady=(12, 4))
        self.run_button = ttk.Button(controls, text="Start Run", style="Primary.TButton", command=self._start_run)
        self.run_button.grid(row=11, column=0, sticky="ew", pady=(8, 0))
        self.open_diff_button = ttk.Button(controls, text="Open Last Diff", command=self._open_diff, state=tk.DISABLED)
        self.open_diff_button.grid(row=12, column=0, sticky="ew", pady=(8, 0))

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        summary = ttk.Frame(right, style="Panel.TFrame", padding=12)
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        summary.columnconfigure(0, weight=1)
        ttk.Label(summary, text="Run Evidence", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.summary_var = tk.StringVar(value="No run yet.")
        ttk.Label(summary, textvariable=self.summary_var, style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 0))

        tabs = ttk.Notebook(right)
        tabs.grid(row=1, column=0, sticky="nsew")
        self.events_text = self._tab_text(tabs, "Events")
        self.changed_text = self._tab_text(tabs, "Changed Files")
        self.suggestions_text = self._tab_text(tabs, "Critical Suggestions")
        self.steps_text = self._tab_text(tabs, "Step Trace")
        self.diff_text = self._tab_text(tabs, "Diff")
        self.test_output_text = self._tab_text(tabs, "Test Output")

    def _entry_row(self, parent: ttk.Frame, label: str, var: tk.StringVar, *, row: int, browse: bool = False) -> None:
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=(10, 4))
        holder = ttk.Frame(parent, style="Panel.TFrame")
        holder.grid(row=row + 1, column=0, sticky="ew")
        holder.columnconfigure(0, weight=1)
        ttk.Entry(holder, textvariable=var).grid(row=0, column=0, sticky="ew")
        if browse:
            ttk.Button(holder, text="Browse", command=self._browse_repo).grid(row=0, column=1, padx=(8, 0))

    def _tab_text(self, notebook: ttk.Notebook, title: str) -> tk.Text:
        frame = ttk.Frame(notebook)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        text = tk.Text(frame, wrap=tk.WORD, relief=tk.FLAT, padx=10, pady=10)
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        notebook.add(frame, text=title)
        return text

    def _browse_repo(self) -> None:
        path = filedialog.askdirectory(title="Select repository")
        if path:
            self.repo_path.set(path)

    def _start_run(self) -> None:
        if self.running:
            return
        try:
            request = self._request_from_form()
        except ValueError as exc:
            messagebox.showerror("Invalid run configuration", str(exc))
            return

        self.running = True
        self.current_diff_path = None
        self.status_var.set("Running")
        self.summary_var.set("Harness run in progress.")
        self.run_button.configure(state=tk.DISABLED)
        self.open_diff_button.configure(state=tk.DISABLED)
        self._clear_outputs()
        self._append(self.events_text, "Queued run\n")

        self.service.start_background(
            request,
            on_event=lambda msg: self.event_queue.put(("event", msg)),
            on_complete=lambda result: self.event_queue.put(("complete", result)),
        )

    def _request_from_form(self) -> DesktopRunRequest:
        return DesktopRunRequest(
            repo_path=self.repo_path.get(),
            instruction=self.instruction.get("1.0", tk.END),
            test_command=self.test_command.get(),
            model=self.model.get(),
            max_iterations=self._int_value("Iterations", self.max_iterations),
            max_bash_calls=self._int_value("Bash Budget", self.max_bash_calls),
            repo_timeout=self._int_value("Repo Timeout", self.repo_timeout),
            max_tokens=self._int_value("Max Tokens", self.max_tokens),
            temperature=self._float_value("Temperature", self.temperature),
            model_timeout=self._int_value("Model Timeout", self.model_timeout),
            max_repo_bytes=self._int_value("Repo Bytes", self.max_repo_bytes),
            max_file_bytes=self._int_value("File Bytes", self.max_file_bytes),
            apply=self.apply_changes.get(),
        )

    @staticmethod
    def _int_value(label: str, var: tk.StringVar) -> int:
        try:
            value = int(var.get())
        except ValueError:
            raise ValueError(f"{label} must be an integer") from None
        if value <= 0:
            raise ValueError(f"{label} must be positive")
        return value

    @staticmethod
    def _float_value(label: str, var: tk.StringVar) -> float:
        try:
            value = float(var.get())
        except ValueError:
            raise ValueError(f"{label} must be a number") from None
        if value < 0:
            raise ValueError(f"{label} cannot be negative")
        return value

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "event":
                self._append(self.events_text, f"{payload}\n")
            elif kind == "complete":
                self._show_result(payload)
        self.after(150, self._drain_events)

    def _show_result(self, result: DesktopRunResult) -> None:
        self.running = False
        self.run_button.configure(state=tk.NORMAL)
        self.status_var.set("Succeeded" if result.succeeded else "Failed")
        self.current_diff_path = result.diff_path if result.diff_path.exists() else None
        self.open_diff_button.configure(state=tk.NORMAL if self.current_diff_path else tk.DISABLED)
        if result.error:
            self.summary_var.set(f"Run failed: {result.error}")
        else:
            self.summary_var.set(f"Run succeeded. Diff: {result.diff_path}")

        if not result.record:
            return

        extra = result.record.extra
        self._set_text(self.changed_text, "\n".join(extra.get("changed_paths") or []) or "No changed files.")
        self._set_text(self.suggestions_text, "\n".join(f"- {item}" for item in extra.get("critical_suggestions") or []) or "No suggestions.")
        steps = list(extra.get("analysis_steps") or [])
        steps.extend(f"Omitted: {item}" for item in extra.get("snapshot_omitted") or [])
        self._set_text(self.steps_text, "\n".join(steps) or "No step trace.")
        self._set_text(self.diff_text, extra.get("final_diff") or "No diff.")
        self._set_text(self.test_output_text, extra.get("last_test_output") or "No test output.")

    def _open_diff(self) -> None:
        if not self.current_diff_path:
            return
        path = str(self.current_diff_path)
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            elif os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", path], check=False)
        except OSError as exc:
            messagebox.showerror("Could not open diff", str(exc))

    def _clear_outputs(self) -> None:
        for widget in (
            self.events_text,
            self.changed_text,
            self.suggestions_text,
            self.steps_text,
            self.diff_text,
            self.test_output_text,
        ):
            self._set_text(widget, "")

    @staticmethod
    def _append(widget: tk.Text, text: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.insert(tk.END, text)
        widget.see(tk.END)
        widget.configure(state=tk.NORMAL)

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text)
        widget.configure(state=tk.NORMAL)


def main() -> None:
    app = CodingHarnessApp()
    app.mainloop()


if __name__ == "__main__":
    main()
