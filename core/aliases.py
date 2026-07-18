# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""AutoCAD command aliases — the muscle-memory table.

The defaults replicate the stock ``acad.pgp`` entries for the commands in
IngeCAD's scope: an AutoCAD user types ``M`` + Enter and gets MOVE without
reading any documentation. Users can override or extend them with their own
PGP file (``~/.config/IngeCAD/acad.pgp``), same syntax as AutoCAD's:

    ; comment
    CO,       *COPY
"""
from __future__ import annotations

from pathlib import Path

# Stock acad.pgp aliases for the product scope (v0.1).
DEFAULT_ALIASES: dict[str, str] = {
    "L": "LINE",
    "C": "CIRCLE",
    "A": "ARC",
    "PL": "PLINE",
    "REC": "RECTANG",
    "POL": "POLYGON",
    "EL": "ELLIPSE",
    "PO": "POINT",
    "DT": "TEXT",
    "T": "MTEXT",
    "MT": "MTEXT",
    "E": "ERASE",
    "M": "MOVE",
    "CO": "COPY",
    "CP": "COPY",
    "RO": "ROTATE",
    "O": "OFFSET",
    "TR": "TRIM",
    "EX": "EXTEND",
    "MI": "MIRROR",
    "SC": "SCALE",
    "B": "BLOCK",
    "I": "INSERT",
    "H": "HATCH",
    "LA": "LAYER",
    "ST": "STYLE",
    "D": "DIMSTYLE",
    "DDIM": "DIMSTYLE",
    "Z": "ZOOM",
    "P": "PAN",
    "DI": "DIST",
    "AA": "AREA",
    "LI": "LIST",
    "LS": "LIST",
    "X": "EXPLODE",
    "F": "FILLET",
    "RE": "REGEN",
    "PU": "PURGE",
    "MA": "MATCHPROP",
}


def parse_pgp(text: str) -> dict[str, str]:
    """Parse AutoCAD PGP alias lines: ``ALIAS, *COMMAND``.

    Lines without the ``*`` marker (external command definitions) and
    comments (``;``) are ignored, like AutoCAD does for aliases.
    """
    aliases: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line or "," not in line:
            continue
        alias, _, command = line.partition(",")
        command = command.strip()
        if not command.startswith("*"):
            continue
        alias = alias.strip().upper()
        command = command[1:].strip().upper()
        if alias and command:
            aliases[alias] = command
    return aliases


def user_pgp_path() -> Path:
    return Path.home() / ".config" / "IngeCAD" / "acad.pgp"


def load_aliases(pgp_path: Path | None = None) -> dict[str, str]:
    """Stock aliases overlaid with the user's PGP file, if present."""
    aliases = dict(DEFAULT_ALIASES)
    path = pgp_path or user_pgp_path()
    try:
        aliases.update(parse_pgp(path.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        pass
    return aliases


def resolve(token: str, aliases: dict[str, str]) -> str:
    """Alias or full command name -> canonical command name (uppercase)."""
    name = token.strip().upper()
    return aliases.get(name, name)
