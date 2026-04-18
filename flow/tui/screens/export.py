"""Modal for exporting habit data from the TUI (CSV or JSON)."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Checkbox, Input, Label, Select, Static

from ... import db, export as _export


class ExportScreen(ModalScreen[Path | None]):
    """Choose format + optional output path. Writes file and dismisses with
    the written path, or None on cancel."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    ExportScreen {
        align: center middle;
    }
    #export-form {
        background: $surface;
        border: thick $accent;
        padding: 1 2;
        width: 70;
        height: auto;
    }
    .field-label {
        margin-top: 1;
        color: $text;
    }
    #export-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, db_path: Path | None = None) -> None:
        super().__init__()
        self.db_path = db_path

    def compose(self) -> ComposeResult:
        default_out = str(Path.home() / "flow-export.csv")
        with Vertical(id="export-form"):
            yield Label("[bold]Export data[/bold]")

            yield Label("format", classes="field-label")
            yield Select(
                [("CSV", "csv"), ("JSON", "json")],
                value="csv",
                id="export-format",
                allow_blank=False,
            )

            yield Label("output path", classes="field-label")
            yield Input(value=default_out, id="export-path")

            yield Checkbox("include archived", id="export-archived")

            yield Static(
                "[dim]enter[/dim] write   [dim]escape[/dim] cancel",
                id="export-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#export-path", Input).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.control.id != "export-format":
            return
        path_input = self.query_one("#export-path", Input)
        current = Path(path_input.value)
        new_ext = ".csv" if event.value == "csv" else ".json"
        if current.suffix in (".csv", ".json"):
            path_input.value = str(current.with_suffix(new_ext))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._try_save()

    def _try_save(self) -> None:
        raw_path = self.query_one("#export-path", Input).value.strip()
        if not raw_path:
            self.notify("path required", severity="warning", timeout=3)
            return
        out_path = Path(raw_path).expanduser()
        fmt = str(self.query_one("#export-format", Select).value)
        include_archived = self.query_one("#export-archived", Checkbox).value

        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8", newline="") as fh:
                with db.session(self.db_path) as conn:
                    if fmt == "csv":
                        _export.write_csv(conn, fh, include_archived, None)
                    else:
                        _export.write_json(conn, fh, include_archived, None)
        except OSError as e:
            self.notify(f"write failed: {e}", severity="error", timeout=4)
            return

        self.notify(f"wrote {out_path}", timeout=3)
        self.dismiss(out_path)

    def action_cancel(self) -> None:
        self.dismiss(None)
