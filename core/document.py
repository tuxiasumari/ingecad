# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Thin wrapper around the ezdxf document.

The ezdxf document IS the model (architectural principle #1): entities are
edited in place through Commands and saved back with ezdxf, so everything
IngeCAD does not understand (XDATA, proxies, 3DSOLID, dictionaries) survives
the round-trip untouched.
"""
from __future__ import annotations

import tempfile
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
        except OSError as exc:
            raise DocumentError(str(exc)) from exc
        except Exception:
            # Strict parsing rejects a lot of real-world output (wrong
            # encodings, unordered sections, LibreDWG's handle-0 entities...);
            # recover mode rebuilds what it can.
            try:
                doc, _auditor = recover.readfile(path)
            except (DXFStructureError, ValueError) as exc:
                raise DocumentError(f"not a readable DXF file: {exc}") from exc
        return cls(doc, path)

    @property
    def name(self) -> str:
        return self.path.name if self.path else "Untitled"

    def modelspace(self):
        return self.doc.modelspace()

    def save_as(self, path: Path) -> None:
        """Save as DXF directly, or as DWG r2000 through the LibreDWG bridge."""
        path = Path(path)
        if path.suffix.lower() == ".dwg":
            from formats.dwg_bridge import dxf_to_dwg

            with tempfile.TemporaryDirectory(prefix="ingecad-save-") as tmp:
                tmp_dxf = Path(tmp) / "out.dxf"
                self.doc.saveas(tmp_dxf)
                dxf_to_dwg(tmp_dxf, path)
        else:
            self.doc.saveas(path)
        self.path = path
        self.dirty = False
