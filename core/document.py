# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Thin wrapper around the ezdxf document.

The ezdxf document IS the model (architectural principle #1): entities are
edited in place through Commands and saved back with ezdxf, so everything
IngeCAD does not understand (XDATA, proxies, 3DSOLID, dictionaries) survives
the round-trip untouched.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import ezdxf
from ezdxf import recover
from ezdxf.document import Drawing
from ezdxf.lldxf.const import DXFStructureError


class DocumentError(Exception):
    """A DXF file could not be loaded."""


class Document:
    """An open drawing: the ezdxf doc plus its filesystem identity."""

    def __init__(self, doc: Drawing, path: Optional[Path] = None) -> None:
        self.doc = doc
        self.path = path
        self.dirty = False

    @classmethod
    def new(cls) -> "Document":
        return cls(ezdxf.new("R2018", setup=True))

    @classmethod
    def load(cls, path: Path | str) -> "Document":
        """Open a DXF file; real-world files get the ezdxf recover treatment.

        ``recover.readfile`` handles the malformed output of many exporters
        (wrong encodings, unordered sections) that plain ``readfile`` rejects —
        exactly the kind of file a colleague sends.
        """
        path = Path(path)
        try:
            doc = ezdxf.readfile(path)
        except (DXFStructureError, UnicodeDecodeError):
            try:
                doc, _auditor = recover.readfile(path)
            except DXFStructureError as exc:
                raise DocumentError(f"not a readable DXF file: {exc}") from exc
        except OSError as exc:
            raise DocumentError(str(exc)) from exc
        return cls(doc, path)

    @property
    def name(self) -> str:
        return self.path.name if self.path else "Untitled"

    def modelspace(self):
        return self.doc.modelspace()
