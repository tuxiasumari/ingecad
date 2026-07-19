# Changelog

## v0.1.0 — 2026-07-18

First usable release: the free "AutoCAD LT" workflow for Linux, end to end.

### Highlights
- Faithful rendering of real-world DWG/DXF (nested blocks, MTEXT, hatch
  patterns, linetypes, dimensions, OCS, paperspace layouts) on a GPU
  viewport that stays fluid on 90k+ entity drawings.
- Transparent DWG open/save through GNU LibreDWG (r2000 write, with a
  verified-save round-trip check) and optional ODA File Converter export
  (r2018). Numerous LibreDWG encoder/decoder fixes were developed against
  a 1,385-file corpus (99% now convert) and are being upstreamed.
- Classic pre-ribbon UI with a real command line and `acad.pgp`-compatible
  aliases; Model/Layout tabs; Layers, Properties and Styles panels.
- Full 2D drafting set: draw (line, circle, arc, polyline, rectangle,
  polygon, text, hatch, dimensions), edit (erase, move, copy, rotate,
  scale, mirror, offset, trim, extend, fillet, explode), grips,
  window/crossing selection, object snaps with AutoSnap markers,
  ORTHO/POLAR, coordinate input, blocks, undo/redo.
- Print / PDF / PNG export at exact scale.

### Performance (large drawings)
- Incremental snap/pick caches: drawing, pasting and moving never rebuild
  the whole index (was seconds of freeze per click on big files).
- Ghost previews tessellate once, in the background; big paste/move
  commits reuse them as "stamps" (a 3000-entity paste: 45 s → 0.13 s).
- Vectorized selection (window/crossing ~20 ms on 1.35M segments), cached
  highlight/grips, background cache warm-up at open, 1 ms GIL slicing so
  background regens never stutter the crosshair.

### Fidelity fixes
- Patched an ezdxf 1.4.4 bug where any transform re-rotated hatch
  patterns cumulatively (report to upstream in progress).
- UTF-8 → codepage conversion, MTEXT sizing, handle-collision and many
  more DWG round-trip fixes in the bundled LibreDWG patch set.
