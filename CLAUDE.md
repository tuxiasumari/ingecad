# IngeCAD — CAD 2D libre estilo AutoCAD clásico

**Autor:** Marco Sumari Tellez · **Licencia:** GPL-3.0-or-later · **Repo destino:** `github.com/ingelibre/ingecad`
**Hermanos:** [IngeTrazo](../ingetrazo/) (modelador 3D/BIM) · [IngePresupuestos](../ingepresupuestos-pyside6/) (presupuestos)

> Plan fundacional definido el 2026-07-16 (conversación estratégica completa en memoria de Claude: `[[project-ingecad-nuevo-hermano-2d]]`). Este archivo guarda el **rumbo** (visión, principios, fases + DoD); el registro de lo hecho vive en los commits de git. No duplicar acá lo que git ya registra.

---

## 🧭 Visión de producto

**Qué es:** el "AutoCAD LT libre" para Linux — visor/editor 2D de DWG/DXF para el ingeniero que viene de AutoCAD: dibujo rápido con comandos de teclado idénticos a AutoCAD, interfaz clásica pre-ribbon, y apertura **fiel** de los DWG que mandan los colegas. Con capacidades de elevación para el oficio civil (puntos topográficos con cota, terrenos con pendiente, carreteras) — datos de elevación, NO modelado 3D.

**Qué NO es:** no es un clon de AutoCAD feature-por-feature (esa es la receta para nunca shippear — lección de IngeTrazo). AutoCAD tiene cientos de funciones que ni Marco usa. El scope es SU flujo real: **línea, círculo, polilínea, polígono, bloques, capas, hatch, trim, offset, extend, move/copy/rotate, zoom, capas, puntos topográficos, área, imprimir a escala.** Nada más hasta que duela.

**El filtro maestro (heredado del ecosistema):** *"¿le sirve al ingeniero que abre el plano de un colega y dibuja rápido con el teclado?"* Si una feature no pasa ese filtro, no entra.

**La tesis de adopción:** la migración desde AutoCAD debe ser **cero fricción de memoria muscular** — mismos aliases (`M`+Enter = MOVE), misma command line, misma selección ventana/crossing, mismos osnaps. El usuario tipea lo de siempre y funciona.

**Reparto con IngeTrazo (no competir contra el hermano):** IngeCAD = el plano 2D que se firma e imprime (lindero, cuadro de coordenadas, planta). IngeTrazo = el 3D (terreno, modelo, BIM → metrado → IngePresupuestos). Mismo CSV topográfico entra a ambos. Puente entre ellos: DXF.

---

## 📐 Principios arquitectónicos (NO negociables)

1. **El documento ezdxf ES el modelo.** No inventar un modelo de datos propio: se editan las entidades ezdxf directamente (envueltas en Commands) y se guarda con ezdxf. Esto garantiza la propiedad más valiosa del producto: **round-trip conservador** — todo lo que IngeCAD no entiende (proxies de Civil 3D, XDATA, diccionarios, 3DSOLID) se preserva **intacto** al reescribir. "Le devolví el plano sano al colega" es la promesa central.
2. **DWG jamás se parsea dentro del app.** Dos satélites como procesos externos (patrón skp2dae de IngeTrazo): **LibreDWG** (GPL-3, embebible y EMBEBIDO — lectura hasta r2018 de fábrica, escritura r2000) y **ODA File Converter** (freeware propietario, instalación opcional de un clic, NUNCA bundlear — da export r2013/r2018). El usuario abre `.dwg` con doble clic y nunca ve el DXF intermedio.
3. **Coordenadas verdaderas float64 en el modelo; float32 solo en el render.** Los planos reales vienen en UTM (~500 000 Este). DXF/ezdxf guardan doubles — el archivo nunca pierde precisión. El viewport resta un **origen de vista** (centro del dibujo) antes de subir a GPU y lo suma al leer el mouse. El gotcha ya se sufrió en IngeTrazo (`SceneDatum`); acá el fix vive solo en el render.
4. **Toda mutación pasa por Command** (undo/redo exacto) y **todo comando es una acción headless** (`actions.move(...)`, no lógica pegada al evento de teclado/mouse). Es el invariante AI-native del ecosistema, y de paso da macros/scripts gratis — a los usuarios de AutoCAD (LISP) les importa.
5. **2D con Z latente.** DXF es 3D nativo: toda entidad tiene Z y OCS (que un visor correcto debe manejar igual — círculos con extrusión invertida existen en planos reales). El modelo conserva Z siempre; la cámara es ortográfica en planta. Agregar vista isométrica después = solo display (la `OrbitCamera` de IngeTrazo está a un copy de distancia). **3DSOLID (ACIS) jamás se interpreta** — se preserva intacto en el round-trip.
6. **Linux/Wayland first.** Heredar los gotchas resueltos de IngeTrazo: `glClear` explícito en `paintGL`, FBO propio si hace falta, DPR físico vs lógico, re-establecer estado GL tras QPainter, MSAA en el FBO de escena. Windows después, con el pipeline CI ya probado (spec PyInstaller + Inno) — pero ninguna decisión puede ROMPER Windows, solo diferirlo.
7. **Interfaz clásica pre-ribbon, para siempre.** Barra de menús (Archivo/Edición/Ver/Insertar/Formato/Herramientas/Dibujo/Acotar/Modificar) + toolbars acoplables (Draw a la izquierda, Modify a la derecha) + **ventana de comandos abajo** (historial + prompt) + status bar con toggles (FORZC/REJILLA/ORTO/POLAR/REFENT). Fondo de modelo oscuro por defecto. El ribbon no existe ni existirá.
8. **Idioma:** código, comentarios, docstrings y commits en **inglés** (contributors); UI bilingüe con el motor `tr()` + `es.json` portado de IngeTrazo (`core/i18n.py`). Los nombres de comando aceptan el inglés de AutoCAD (`LINE`, `TRIM`) — es lo que la memoria muscular del usuario ya sabe — y los menús se traducen.

---

## 🛠 Stack

| Capa | Elección |
|---|---|
| Lenguaje | Python 3.12 (versión de referencia CI, igual que IngeTrazo) |
| UI | PySide6 (Qt 6) |
| Render | QOpenGLWidget + VBOs batcheados por capa/color (patrón IngeTrazo, versión 2D) |
| Kernel de documento | **ezdxf** (MIT) — parsing, modelo, escritura DXF |
| Motor de "regen" | **`ezdxf.addons.drawing`** frontend (resuelve bloques/MTEXT/linetypes/hatches/cotas) → backend GL propio que emite arrays de vértices |
| DWG | LibreDWG (`dwg2dxf`/`dxf2dwg`, embebido) + ODA File Converter (satélite opcional) |
| Math/lotes | NumPy |
| Tests | pytest + banco de DWG reales |

Sin deps pesadas nuevas. Nada de OpenCascade, nada de kernels BRep.

---

## 📁 Layout del repo (espejo de IngeTrazo)

```
ingecad/
├── main.py                    ← entry point Qt (abre argv[1] — asociación .dwg/.dxf)
├── CLAUDE.md                  ← este archivo
├── LICENSE (GPL-3) / README.md / AUTHORS / CONTRIBUTING.md
├── core/
│   ├── document.py            ← wrapper fino del ezdxf doc + versioning + dirty
│   ├── actions.py             ← capa de acciones headless (move, trim, offset…)
│   ├── commands.py            ← Command ABC + History (portado de IngeTrazo)
│   ├── aliases.py             ← tabla de aliases AutoCAD (compatible acad.pgp)
│   ├── snap.py                ← osnaps (END/MID/CEN/NOD/INT/PER/TAN/NEA) + polar/orto
│   ├── i18n.py                ← portado de IngeTrazo
│   └── geo.py                 ← puntos topográficos, área, cuadro de coordenadas, perfil
├── views/
│   ├── main_window.py         ← menús + toolbars clásicas + status bar
│   ├── viewport.py            ← canvas GL 2D (pan/zoom, origen de vista, pick)
│   ├── command_line.py        ← ventana de comandos (prompt + historial + autocompletado)
│   └── layers_panel.py        ← administrador de capas
├── render/
│   ├── backend.py             ← backend GL para ezdxf.addons.drawing (emite vértices)
│   └── batches.py             ← VBOs por capa/color, culling por rect de vista
├── formats/
│   ├── dwg_bridge.py          ← satélites LibreDWG / ODA (detección, conversión, instalador)
│   └── pdf_out.py             ← imprimir/PDF a escala
├── tools/                     ← tools interactivas (line, circle, trim…) sobre actions
├── resources/ (shaders, iconos, linetypes, patrones de hatch)
├── i18n/ (en.json identidad, es.json)
└── tests/
```

---

## 🥇 Regla de oro (idéntica a IngeTrazo, no negociable)

Una fase NO está terminada hasta cumplir las 3: **(1)** su DoD pasa, **(2)** está commiteada y la app arranca sin regresiones, **(3)** cero "lo dejo para después" dentro de la fase. No se abre la siguiente hasta esas tres.

**Banco de pruebas vivo (el "plano del colega" — equivalente a la casita de IngeTrazo):** coleccionar DWG reales que mandan los colegas (con permiso, sin trackear al repo público) y arrancar cada sesión preguntando *"¿qué parte del plano del colega todavía se ve/edita mal?"*. Los gaps aparecen solos dogfoodeando, no desde la lista abstracta.

---

## 🚧 Fases hacia v0.1

**FASE 0 — Esqueleto** *(≈1 sesión)*
Repo + GPL-3 + layout + venv + ventana con viewport GL vacío: pan (botón medio), zoom a la rueda (al cursor), fondo oscuro, ejes/UCS icon. CI mínima (pytest).
- **DoD:** arranca en Wayland nativo sin glitches; pan/zoom suave; `pytest` verde en CI.

**FASE 1 — Visor fiel (el go/no-go del proyecto)** *(la incógnita — atacarla primero)*
`ezdxf.addons.drawing` frontend → backend GL propio: VBOs por capa/color, culling por rect de vista, origen de vista float64→float32. Render fiel de: LINE/PLINE/CIRCLE/ARC/ELLIPSE, bloques (INSERT anidados), TEXT/MTEXT con formato, linetypes a escala, HATCH (patrones + solid), cotas (como las guarda el archivo), colores ByLayer/ByBlock, capas on/off, OCS. Zoom extents / window / previo.
- 📌 El pick va detrás de una abstracción (índice NumPy después, como IngeTrazo — no construirlo aún).
- **DoD:** 10 DWG reales de colegas (convertidos con `dwg2dxf` a mano por ahora) se ven **idénticos** a AutoCAD/DWG FastView lado a lado; un plano de ~200k entidades hace pan/zoom fluido en Wayland. Si esto pasa, el proyecto es viable; todo lo demás es trabajo conocido.

**FASE 2 — DWG de fábrica** *(cierra el caso de uso #1: "me mandan un DWG")*
`formats/dwg_bridge.py`: LibreDWG embebido (binario `dwg2dxf` empaquetado — GPL con GPL, sin conflicto) → abrir `.dwg` transparente (conversión a temp, el usuario nunca ve el DXF). Guardar como `.dwg` r2000 vía `dxf2dwg`. Detector + **instalador de un clic** del ODA File Converter (patrón skp2dae validado) → export r2013/r2018. Asociación de archivos `.dwg`/`.dxf` en el `.desktop`.
- **DoD:** doble clic en un `.dwg` → abre; "Guardar como DWG" → el colega lo abre en AutoCAD (aviso TrustedDWG documentado en README como esperado e inofensivo). Rutas con acentos (gotcha ya cazado en skp2dae).

**FASE 3 — Command line + aliases AutoCAD** *(la tesis de migración)*
`views/command_line.py` (prompt abajo, historial, autocompletado) + `core/aliases.py` con los aliases exactos de `acad.pgp`: `L`=LINE, `C`=CIRCLE, `A`=ARC, `PL`=PLINE, `REC`=RECTANG, `POL`=POLYGON, `E`=ERASE, `M`=MOVE, `CO`/`CP`=COPY, `RO`=ROTATE, `O`=OFFSET, `TR`=TRIM, `EX`=EXTEND, `MI`=MIRROR, `SC`=SCALE, `B`=BLOCK, `I`=INSERT, `H`=HATCH, `LA`=LAYER, `Z`=ZOOM (con `Z`→`E`/`W`/`P`), `U`, `DI`=DIST, `AA`=AREA, `LI`=LIST, `X`=EXPLODE, `F`=FILLET. Semántica AutoCAD: Espacio/Enter ejecutan, Enter en vacío repite el último comando, Esc cancela, tipear con selección previa opera sobre ella (noun-verb) o pide selección (verb-noun). Soporte de archivo PGP del usuario para aliases custom. Todo comando despacha a `core/actions.py` (headless).
- **DoD:** un usuario de AutoCAD ejecuta `L`, `C`, `Z`+`E`, `E` sin leer documentación y se siente en casa. Tests headless de la tabla de aliases y del parser del prompt.

**FASE 4 — Dibujo con snap (el "feel")**
Osnaps AutoCAD con sus marcadores AutoSnap (cuadrado END, triángulo MID, círculo CEN, X NOD, cruz INT, PER, TAN, NEA) + toggle F3 + ORTO (F8) + POLAR (F10) + entrada por coordenadas: absolutas `10,5`, relativas `@10,5`, polares `@10<45`, y distancia directa (apuntar + tipear número). Tools: LINE, CIRCLE (centro-radio/2P/3P), ARC (3P), PLINE, RECTANG, POLYGON. Undo/redo integrado.
- Reusar la maquinaria conceptual del snap de IngeTrazo (threshold px, prioridades) simplificada a 2D.
- **DoD:** dibujar una planta simple solo con teclado + mouse, snaps exactos, coordenadas por prompt; undo limpio de cada paso.

**FASE 5 — Edición (el scope del usuario, completo)**
ERASE, MOVE, COPY (múltiple), ROTATE (con referencia), SCALE, MIRROR, OFFSET (distancia + través), **TRIM/EXTEND** (con selección de bordes y modo rápido Shift-alterna como AutoCAD moderno), FILLET (radio 0 = esquina). Selección: click, **ventana (izq→der, azul) / crossing (der→izq, verde)** con los colores de AutoCAD, Shift quita de la selección, grips básicos (mover vértice/estirar) si el costo es razonable — si no, a v0.2.
- **DoD:** flujo completo de edición de un plano real sin tocar menús; TRIM/EXTEND se sienten como AutoCAD (el listón más alto de la fase).

**FASE 6 — Capas, propiedades, bloques y hatch**
Panel de capas (`LA`): crear/renombrar, color, linetype, on/off/freeze/lock, capa actual. Propiedades de entidad (panel lateral estilo bandeja IngeTrazo): color/capa/linetype ByLayer. Bloques: `I` (insertar con escala/rotación), `B` (crear desde selección), explode. `H`: SOLID + patrones ANSI básicos + escala/ángulo.
- **DoD:** el scope declarado del usuario ("bloques, capas, hatch") operativo end-to-end y round-trip al DWG.

**FASE 7 — Topografía + elevación (el diferencial civil)** ⭐
`core/geo.py`: **import CSV de puntos** (reusar/portar `parse_points_csv` de IngeTrazo — P,N,E,Z,desc, dialectos de estación total) → entidades `POINT` en (E,N,Z) + `TEXT` (número/cota/desc) en capas `PUNTOS`/`COTAS`/`DESC`; snap NOD cae bit-exacto. `AA` sobre polilínea cerrada. **Cuadro de datos técnicos automático** (seleccionar polilínea → tabla de vértices con Este/Norte, lados, distancias, rumbos/azimuts, área y perímetro, como entidades en el plano — lo que en AutoCAD todos arman a mano o con LISPs). **Perfil de elevación** de una polilínea cuyos vértices tienen Z (carreteras/pendientes): panel inferior estación/cota con pendientes, export CSV/PNG (portar el concepto de `ProfileDock` de IngeTrazo).
- **DoD:** caso municipal completo sin AutoCAD: CSV del topógrafo → plano de lindero snapeado a los puntos → cuadro de coordenadas + área → perfil del eje con pendientes → DWG al colega.

**FASE 8 — Salida** 🏁 *(cierra v0.1)*
Imprimir / **exportar PDF a escala** (1:100, 1:500…, tamaño de papel, área por ventana), export PNG hi-res (patrón `render_image` de IngeTrazo). Layouts/espacio papel completo se difiere a v0.2 — escala directa desde modelo cubre el 80%.
- **DoD:** un plano imprimible a escala exacta verificable con regla. **= v0.1 usable real.**

**Después de v0.1 (candidatos v0.2, no abrir antes):** grips completos, DIMENSION propias (crear cotas), espacio papel/layouts, MATCHPROP, PURGE, arrays, empaquetado Windows (pipeline IngeTrazo), AppImage/Flatpak, más patrones de hatch, LISP-like scripting sobre `actions`.

---

## 🔩 Track L — LibreDWG (paralelo, NO bloquea ninguna fase)

Objetivo de largo plazo: que el ecosistema libre no dependa del conversor de ODA. Rampa por confianza creciente:

1. **L1 — Usar y reportar:** IngeCAD usa LibreDWG desde F2; cada DWG real que falle → minimizar + issue upstream con repro.
2. **L2 — Harness de fuzzing round-trip:** generar miles de DXF con ezdxf → `dxf2dwg` → `dwg2dxf` → comparar entidad a entidad (la metodología del fuzz bench de IngeTrazo aplicada a otro dominio; es lo que LibreDWG no tiene y el aporte de más valor por esfuerzo).
3. **L3 — Patches quirúrgicos** asistidos por IA sobre los fallos que L1/L2 destapen.
4. **L4 — El writer r2013/r2018** (el hueco histórico, spec ODA pública como guía). Aporte mayor; solo encararlo cuando L1-L3 hayan construido confianza con el mantenedor.

Si upstream tarda o rechaza: **fork amistoso** (`tuxiasumari/libredwg`) — IngeCAD empaqueta el fork, los PRs se siguen ofreciendo upstream, divergencia mínima. Verificar antes de contribuir grande si GNU exige cesión de copyright a la FSF.

---

## 🧪 Tests (desde el día uno)

- **Round-trip conservador (el invariante sagrado):** abrir → tocar UNA entidad → guardar → re-abrir → todo lo NO tocado es byte/valor-idéntico (incl. entidades desconocidas y XDATA). Corre sobre el banco de DWG reales.
- **Fidelidad de render:** para cada archivo del banco, snapshot del render rasterizado vs referencia aprobada (regresión visual).
- **Aliases/acciones headless:** cada comando testeable sin GUI (la capa `actions` lo garantiza).
- **Fuzz de comandos** (más adelante, patrón IngeTrazo): secuencias aleatorias seeded de dibujar/editar/undo con invariantes (documento válido, undo→redo reproduce fingerprint).

---

## ⚠️ Gotchas heredados de IngeTrazo (releer antes de tocar el render)

- Wayland exige `glClear` explícito en `paintGL`; QPainter contamina el estado GL (re-establecer todo por frame); FBO propio si el depth/formato miente; tamaños en píxeles físicos (`devicePixelRatioF`); MSAA en el FBO de escena, no en el widget; `QMatrix4x4 * QVector4D` no bindea (usar `.map()`); Wayland puede intercalar frames viejos bajo ráfagas (cosmético, escape `QT_QPA_PLATFORM=xcb`).
- Satélites: Wine re-encodea argv (rutas con acentos → ruta temp ASCII) — aplica si algún satélite fuera .exe; LibreDWG es nativo Linux así que este gotcha probablemente no aplica, pero el patrón de sanitización ya existe en IngeTrazo.
- QSettings necesita `setOrganizationName/setApplicationName` fijados para persistir donde corresponde.
- **Íconos de tipo de archivo (.dwg/.dxf) — la búsqueda de íconos es TEMA-MAYOR (gotcha caro, 2026-07-20).** Instalar el ícono de mimetype SOLO en `hicolor` NO alcanza cuando el tipo tiene un genérico que el tema activo provee: freedesktop recorre **tema por tema** (Yaru antes que hicolor) y prueba TODOS los nombres de fallback dentro de cada tema. Como `.dwg`/`.dxf` son `image/vnd.*`, su fallback incluye `image-x-generic`, que **Yaru sí tiene** → lo elige antes de llegar a nuestro `image-vnd.dwg` en hicolor (último). Fix: `install-desktop.sh` instala los PNGs de mimetype en el **tema activo (`gsettings ... icon-theme`) y sus padres** (`Inherits` del `index.theme`), no solo en hicolor. Además: MIME propio con `weight/priority="90"` para ganarle a otro paquete CAD que reclame la extensión/magic (un BricsCAD instalado toma `*.dwg` + magic `AC10` con prioridad 80). Diagnóstico definitivo: `Gtk.IconTheme.lookup_by_gicon` con GTK **4.0** (Nautilus 50 es GTK4) sobre el GIcon real del archivo — dice exactamente qué PNG se elige. Y limpiar `~/.cache/thumbnails` + `nautilus -q` porque `image/*` intenta miniatura y cachea la fallida.

---

## 🗓 Sesión 2026-07-20 — v0.1.1 (integración con el escritorio)

Release `v0.1.1` (tag + release en `ingelibre/ingecad`; sin binarios Windows aún — solo `tests.yml`). Instalado y verificado en la PC del usuario. Lo hecho (detalle en commits `503d85c`/`22b176a`):
- **Ícono de app renovado** (el usuario mejoró `resources/ingecad.svg`) + PNG/ICO rasterizados regenerados a `resources/icons/`.
- **Íconos de documento branded para `.dwg`/`.dxf`** (`scripts/gen_doc_icons.py`, patrón IngeTrazo/IngePresupuestos: hoja + etiqueta DWG/DXF + insignia de IngeCAD → hicolor mimetypes + `.ico`) + paquete MIME `resources/mime/ingecad.xml`. Ver el gotcha "tema-mayor" arriba — fue el bug real por el que "no se veían".

**Pendiente estratégico anotado: publicar a Flathub** (IngeCAD + IngeTrazo). Media: capturas PNG (1ª estática) + videos opcionales WebM/MKV VP9/AV1 sin audio <10 MiB (⇒ ~10-30s). Difícil: empaquetar PySide6+Qt6+GL **y compilar LibreDWG vendorizado** dentro del manifest Flatpak. App-ID candidato `io.github.ingelibre.IngeCAD`. Debe pasar `appstreamcli validate` (warnings fatales).

---

## 📊 Estimación honesta

F0 ≈ 1 sesión · **F1 ≈ 2-4 semanas** (la incógnita; ezdxf.drawing elimina el grueso del riesgo) · F2 ≈ 1 semana · F3-F6 ≈ 4-6 semanas con foco · F7 ≈ 1-2 semanas (mucho se porta de IngeTrazo) · F8 ≈ 1 semana. **v0.1 ≈ 2-3 meses** a ritmo IngeTrazo. Lejos de "años" porque el motor duro (parsear/renderizar fiel) lo aportan ezdxf y LibreDWG — IngeCAD es integración + UX, que es donde Marco ya demostró velocidad.
