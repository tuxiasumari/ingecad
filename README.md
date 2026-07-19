# IngeCAD

**Free 2D CAD for Linux, in the spirit of classic AutoCAD.**

IngeCAD is a lightweight 2D drafting program for civil engineers and architects
migrating from AutoCAD: same command aliases (`M` ⏎ moves, `TR` ⏎ trims), the
classic pre-ribbon interface (menus + toolbars + a real command line), and
faithful round-tripping of the DWG/DXF files your colleagues send you.

Part of the **Inge** ecosystem, alongside
[IngeTrazo](https://github.com/tuxiasumari/ingetrazo) (free 3D modeling / BIM)
and IngePresupuestos (construction budgeting).

## Design pillars

- **Faithful files first.** The document model *is* the DXF database (via
  [ezdxf](https://ezdxf.mozman.at/)); everything IngeCAD does not understand is
  preserved untouched when saving. DWG is handled by external converters
  (GNU LibreDWG bundled; ODA File Converter optional) — never parsed in-app.
- **AutoCAD muscle memory.** Command line at the bottom, `acad.pgp`-compatible
  aliases, window/crossing selection, object snaps with the classic markers.
- **Linux/Wayland first.** Native, fast, no ribbon — ever.
- **Deliberately small.** Lines, circles, polylines, blocks, layers, hatches,
  trim/offset/extend, survey points with elevations, and printing to scale.
  Not a feature-for-feature AutoCAD clone.

## Status — v0.1

First usable release. What works today:

- **Faithful viewer** for real-world DWG/DXF: nested blocks, MTEXT, hatches
  (patterns + solids), linetypes, dimensions, OCS, paperspace layouts —
  smooth pan/zoom even on cadastre-scale drawings (90k+ entities).
- **DWG in and out**: open `.dwg` transparently via GNU LibreDWG; save as
  DWG r2000 (LibreDWG) or r2018 (ODA File Converter, if installed), with a
  silent verified-save check.
- **Classic interface**: command line at the bottom with AutoCAD aliases
  (`L`, `C`, `M`, `TR`, `Z`+`E` …), dark model space, Model/Layout tabs,
  dockable Layers / Properties / Styles panels.
- **Drawing & editing**: lines, circles, arcs, polylines, rectangles,
  polygons, text, hatches, dimensions; ERASE / MOVE / COPY / ROTATE /
  SCALE / MIRROR / OFFSET / TRIM / EXTEND / FILLET / EXPLODE, grips,
  window/crossing selection, clipboard copy/paste.
- **The AutoCAD feel**: object snaps with AutoSnap markers (END, MID, CEN,
  NOD, INT, PER, NEA), ORTHO / POLAR, absolute / relative / polar
  coordinate input, blocks (`B` / `I`), undo/redo of everything.
- **Output**: print / export PDF and PNG to exact scale.

Planned next (v0.2): survey-point import with elevations, coordinate
tables, elevation profiles, paper-space editing. See `CLAUDE.md` for the
roadmap.

## Running from source

```bash
git clone https://github.com/tuxiasumari/ingecad.git
cd ingecad
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python main.py
```

**DWG support** needs the LibreDWG converters (`dwg2dxf` / `dxf2dwg`) on
your `PATH` (most distros package `libredwg-tools`), or their binaries
placed in `vendor/libredwg/bin/`. DXF works out of the box. Installing the
freeware ODA File Converter additionally enables DWG r2018 export.

To get the launcher entry, app icon and `.dwg`/`.dxf` double-click
association on Linux:

```bash
./scripts/install-desktop.sh   # then log out/in once
```

## License

GPL-3.0-or-later. Copyright (C) 2026 Marco Sumari Tellez and IngeCAD
contributors. See `LICENSE` and `AUTHORS`.
