"""Resolve a complete Rank Hunter theme before Streamlit starts."""
from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from rank42.settings_registry import setting_value
from rank42.theme_loader import RELEASE_THEME_ID, Theme, resolve_release_theme


@dataclass(frozen=True)
class ActiveTheme:
    theme: Theme | None
    requested_id: str | None
    appearance: str = "light"

    @property
    def id(self) -> str:
        return self.theme.id if self.theme is not None else ""

    @property
    def streamlit_theme_path(self) -> Path | None:
        if self.theme is None:
            return None
        return self.theme.streamlit_theme_for(self.appearance)


def _persisted_theme_id(db_path: Path) -> str | None:
    if not db_path.is_file():
        return None
    try:
        db = sqlite3.connect(str(db_path), timeout=2)
        db.row_factory = sqlite3.Row
        try:
            value = setting_value(db, "ui_theme", None)
        finally:
            db.close()
    except sqlite3.Error:
        return None
    value = str(value or "").strip()
    return value or None


def _persisted_appearance(db_path: Path) -> str:
    if not db_path.is_file():
        return "light"
    try:
        db = sqlite3.connect(str(db_path), timeout=2)
        db.row_factory = sqlite3.Row
        try:
            value = setting_value(db, "ui_appearance", "light")
        finally:
            db.close()
    except sqlite3.Error:
        return "light"
    value = str(value or "light").strip().lower()
    return value if value in {"light", "dark"} else "light"


def resolve_active_theme(project_root: str | Path, db_path: str | Path) -> ActiveTheme:
    """Resolve the release-pinned Axiom theme before Streamlit starts.

    The persisted theme reader remains in this module for future reactivation,
    but Rank Hunter 0.9.1 intentionally ignores user theme selection.
    """

    root = Path(project_root).resolve()
    database = Path(db_path)
    if not database.is_absolute():
        database = (root / database).resolve()
    appearance = _persisted_appearance(database)
    theme, _warning = resolve_release_theme(root, RELEASE_THEME_ID)
    return ActiveTheme(theme, RELEASE_THEME_ID, appearance)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--db", required=True)
    args = ap.parse_args()
    active = resolve_active_theme(args.project_root, args.db)
    native = str(active.streamlit_theme_path or "")
    print(f"{active.id}\t{native}")


if __name__ == "__main__":
    main()
