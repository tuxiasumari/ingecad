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

## Status

Early development (Phase 0: project skeleton — see `CLAUDE.md` for the full
roadmap and phase gates).

## Running from source

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python main.py
```

## License

GPL-3.0-or-later. Copyright (C) 2026 Marco Sumari Tellez and IngeCAD
contributors. See `LICENSE` and `AUTHORS`.
