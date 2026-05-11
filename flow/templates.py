"""Bundled habit templates — used by `flow init` and `flow add --template`.

Templates are curated starters for the empty-DB problem. Keep the catalog
short and broadly applicable; long-tail habits should be added directly via
`flow add`. Each template is a frozen dataclass so the registry can be passed
around without accidental mutation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    frequency: str
    unit: str | None = None
    target: float | None = None
    description: str | None = None


TEMPLATES: dict[str, Template] = {
    t.key: t
    for t in (
        Template(
            key="reading",
            name="Read",
            frequency="daily",
            unit="pages",
            target=20,
            description="Daily reading habit",
        ),
        Template(
            key="meditation",
            name="Meditate",
            frequency="daily",
            unit="minutes",
            target=10,
            description="Morning meditation",
        ),
        Template(
            key="writing",
            name="Write",
            frequency="daily",
            unit="words",
            target=500,
            description="Daily writing practice",
        ),
        Template(
            key="walking",
            name="Walk",
            frequency="daily",
            unit="minutes",
            target=30,
            description="Daily walk",
        ),
        Template(
            key="water",
            name="Water",
            frequency="daily",
            unit="glasses",
            target=8,
            description="Hydration tracker",
        ),
        Template(
            key="journal",
            name="Journal",
            frequency="daily",
            description="End-of-day reflection",
        ),
        Template(
            key="workout",
            name="Workout",
            frequency="mon,wed,fri",
            unit="minutes",
            target=45,
            description="Strength / cardio session",
        ),
        Template(
            key="gratitude",
            name="Gratitude",
            frequency="daily",
            unit="items",
            target=3,
            description="Three things you're grateful for",
        ),
    )
}


def list_templates() -> list[Template]:
    """Sorted-by-key view of the catalog. Stable order makes the CLI listing
    deterministic regardless of dict insertion order."""
    return [TEMPLATES[k] for k in sorted(TEMPLATES)]


def get_template(key: str) -> Template | None:
    return TEMPLATES.get(key.lower().strip())
