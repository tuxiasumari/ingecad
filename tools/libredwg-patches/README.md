# LibreDWG patches (Track L)

IngeCAD embeds LibreDWG's `dwg2dxf`/`dxf2dwg` as satellite converters
(`vendor/libredwg/bin`, gitignored). These patches fix crashes found through
the real-file bench and are applied on top of the **0.14** release tarball.

| Patch | Status upstream |
|---|---|
| `0001-dxf-fix-null-deref-PROXY_ENTITY.patch` | Backport of upstream `af67061c` (fixed after 0.14) — SIGSEGV writing partially decoded PROXY_ENTITYs |
| `0002-dxf-hex-encode-binary-TF-chunks.patch` | Ours — `proxy_data` (group 310) written as raw bytes instead of hex; submitted as [LibreDWG#1311](https://github.com/LibreDWG/libredwg/pull/1311) |
| `0003-dxf-import-dynamic-block-objects.patch` | Ours — four dxf2dwg import fixes (EVALUATION_GRAPH, BLOCKSTRETCHACTION SEGV, EVAL_Edge heap overflow, FIELD childval); submitted as [LibreDWG#1312](https://github.com/LibreDWG/libredwg/pull/1312) |
| `0004-dxf-hatch-boundary-handles-per-path.patch` | Ours — associative multi-path HATCHes lost pattern scale/def lines through the roundtrip (hdl_idx never reset per path); submitted as [LibreDWG#1313](https://github.com/LibreDWG/libredwg/pull/1313) |
| `0005-dxf-string-emission.patch` | Ours — caret-encode C0 controls, embed-before-quote, escape-preserving chunk splits, dxf+2 continuation codes; corpus success 83%→96.6% (1385 real DWGs); submitted as [LibreDWG#1314](https://github.com/LibreDWG/libredwg/pull/1314) |

0001/0002 were found with a 27 MB r2013 cadastre DWG whose
ACAD_PROXY_ENTITYs decode partially (AcDs segments): the conversion
segfaulted mid-write; with the patches the drawing opens fully (92k
entities). 0003 came from a 4.5 MB AutoCAD 2018 pavement plan with dynamic
blocks that dxf2dwg could not import at all; with it, "save as DWG r2000"
of that plan works end-to-end.

## Rebuilding vendor/libredwg

```sh
curl -LO https://github.com/LibreDWG/libredwg/releases/download/0.14/libredwg-0.14.tar.xz
tar xf libredwg-0.14.tar.xz && cd libredwg-0.14
for p in ../tools/libredwg-patches/0*.patch; do patch -p1 < "$p"; done
./configure --disable-shared --disable-bindings --disable-python \
            --prefix="$PWD/../vendor/libredwg"   # PKG_CONFIG=/bin/true if pkg-config is missing
make -j"$(nproc)" && make install-strip
```
