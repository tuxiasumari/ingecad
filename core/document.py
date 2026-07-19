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

from core import ezdxf_patches

ezdxf_patches.apply()


class DocumentError(Exception):
    """A DXF file could not be loaded."""


def _repair_material_dict(doc) -> None:
    """Heal ACAD_MATERIAL entries that are dead handle strings.

    LibreDWG-converted files can carry ByBlock/ByLayer/Global entries whose
    MATERIAL objects never made it across; ezdxf then crashes on save
    (``materials.get("ByLayer").dxf``). Dropping the dead strings and
    recreating the required defaults is exactly what AutoCAD's own audit
    does — the rest of the file stays untouched.
    """
    try:
        materials = doc.materials
        broken = [key for key, value in list(materials.object_dict.items())
                  if isinstance(value, str)]
        for key in broken:
            materials.object_dict.discard(key)
        if broken:
            materials.create_required_entries()
    except Exception:
        pass    # never let a repair pass break an open


class Document:
    """An open drawing: the ezdxf doc plus its filesystem identity."""

    def __init__(self, doc: Drawing, path: Optional[Path] = None) -> None:
        self.doc = doc
        self.path = path
        self._dirty = False
        # Monotonic edit counter: every mutation bumps it (all Commands set
        # dirty=True). Lets a background regen detect that the document
        # changed under it and that its result is stale.
        self.revision = 0
        _repair_material_dict(doc)

    @property
    def dirty(self) -> bool:
        return self._dirty

    @dirty.setter
    def dirty(self, value: bool) -> None:
        if value:
            self.revision += 1
        self._dirty = value

    @classmethod
    def new(cls) -> "Document":
        # Load the standard linetypes (needed for linetype rendering) but not
        # ezdxf's full style/dimstyle setup — a new AutoCAD drawing carries
        # only a couple of established styles, not dozens of OpenSans/EZ_*
        # entries. install_default_styles seeds the metric ISO-25 dim style.
        from core import styles as _styles

        document = cls(ezdxf.new("R2018", setup=["linetypes"]))
        _styles.install_default_styles(document)
        return document

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

    def save_as(self, path: Path) -> str:
        """Save as DXF directly, or as DWG via the bundled LibreDWG.

        Returns ``(engine, warnings)``: engine is "dxf" or "libredwg" (r2000);
        warnings is a list of human-readable strings from the verified save
        (empty when the DWG checked out clean). DXF saves never warn.
        """
        path = Path(path)
        warnings: list[str] = []
        if path.suffix.lower() == ".dwg":
            from formats.dwg_bridge import write_dwg

            with tempfile.TemporaryDirectory(prefix="ingecad-save-") as tmp:
                tmp_dxf = Path(tmp) / "out.dxf"
                self.doc.saveas(tmp_dxf)
                warnings = write_dwg(tmp_dxf, path)
            engine = "libredwg"
        else:
            self.doc.saveas(path)
            engine = "dxf"
        self.path = path
        self.dirty = False
        return engine, warnings
