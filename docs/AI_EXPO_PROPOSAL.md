# Artie: AI-Powered Four-Cable Drawing Robot
## IEEE AI Expo — Project Proposal

---

## 1. Executive Summary

**Artie** is an autonomous four-cable parallel robot capable of converting digital input (text, SVG vector graphics, and raster sketches) into physical drawings on a large 2.4 × 1.8 meter board. The system combines classical computer-vision techniques with modern AI-derived path-planning algorithms to translate any image — from a child's sketch to professional anime line-art — into smooth, optimized robot trajectories that minimize wear, drawing time, and pen-lift count.

The novelty of Artie lies not in the mechanical platform alone, but in the **end-to-end intelligent pipeline** that decides how a human-readable picture becomes a sequence of safe, optimal pen movements on a non-trivial cable kinematics system. Every stage of this pipeline — from binarization to curve fitting to stroke ordering — has been measured, tuned, and validated against real-world drawings.

**Measured performance improvements over a baseline implementation:**
- Pen-lift count reduced by **41%**
- Pen-up travel distance reduced by **71%**
- Curve fitting accuracy improved by **5-10×** (Schneider 1990 algorithm)
- Wobble on long thin strokes attenuated by **76%** (skeleton smoothing)

---

## 2. Problem Statement

Existing drawing robots fall into two categories:

1. **Toy / hobbyist plotters** — Limited to small canvases (< A3), use simple G-code translation, produce visible jitter on curves and inefficient pen-lifts on complex sketches.
2. **Industrial CNC plotters** — Expensive, fixed-installation, require pre-vectorized input (SVG only), no AI processing.

Neither serves the gap between **expressive drawing on large vertical surfaces** (walls, whiteboards, classroom boards) and **autonomous interpretation of arbitrary input** (raster images uploaded by non-technical users). A teacher uploading a JPEG should not need to convert it to SVG manually. A child should be able to draw on paper and have a 2-meter robot reproduce it on a wall.

**Artie targets this exact gap.**

---

## 3. Proposed Solution

A complete drawing system with three integrated subsystems:

### 3.1 Mechanical Platform — Four-Cable Parallel Kinematics
- 4 anchored corner motors, each driving a steel cable
- Cables converge at a moving carriage holding the pen
- Carriage position derived by solving 4 simultaneous cable-length equations
- Workspace: 2.4 m × 1.8 m vertical board
- Pen: pneumatic vertical actuator with contact engagement gap of 6 mm

### 3.2 AI Image Processing Pipeline
A 12-stage pipeline that turns raw images into board-space stroke trajectories:

1. **Decode & Resize** — adaptive resolution control (700 / 1000 / 1300 / 1500 px)
2. **Background Normalization** — median-blur flattening removes uneven scan lighting
3. **Contrast Enhancement** — CLAHE (Contrast-Limited Adaptive Histogram Equalization)
4. **Bilateral Filtering** — edge-preserving denoising
5. **Unsharp Masking** — recovers sharpness lost to JPEG compression
6. **Adaptive Thresholding** — three selectable methods: Adaptive Gaussian, Otsu, Hysteresis Ink
7. **Filled-Region Detection** — compactness ratio (4πA/P²) classifies each component as outline-traced (filled disks, donuts, character interiors) or skeleton-traced (line strokes)
8. **Skeletonization** — Zhang-Suen thinning (skimage / OpenCV ximgproc fallback)
9. **Spur Pruning** — multi-pass branch removal at 6-pixel threshold; 8-connected neighbour walk preserves diagonal strokes
10. **Skeleton Smoothing** — 5-tap binomial kernel (1, 4, 6, 4, 1)/16 attenuates raster jitter while preserving junction endpoints
11. **RDP Simplification** — Ramer-Douglas-Peucker polyline reduction
12. **Curve Fitting** — Schneider 1990 cubic Bezier fitting with bbox safety guard and Newton-Raphson reparameterization

### 3.3 Path Optimization
- **Stroke merging** — gap-based merge (2 px) with 20° angle gate, ambiguity-protected against bridging T-junctions
- **2-opt stroke reorder** with reversal to minimize pen-up travel
- **Cluster reorder** at 0.26 m grid for spatial locality
- **Travel-move merging** removes redundant pen-up segments
- **Dedupe** removes accidentally-duplicated strokes from the curve fitter

### 3.4 Multi-Modal Input
- **Text mode** — Built-in Hershey-style vector font; supports multi-line writing with automatic line wrapping and word spacing
- **SVG mode** — Direct curve import for designer-supplied vector files
- **Sketch mode** — Raster image (PNG/JPG) processed through the full AI pipeline above

---

## 4. Technical Innovation

### 4.1 Schneider Bezier Curve Fitting (Graphics Gems I, 1990)
Most drawing robots emit polylines (sequences of line segments). This produces visible faceting on curves. Artie ports the Schneider algorithm with three modern enhancements:
- Both tangent magnitudes solved explicitly via Cramer's rule
- Newton-Raphson reparameterization (4 iterations) for chord-length refinement
- Bounding-box safety check at 7 intermediate t values prevents control points from pulling the sampled curve outside the carriage-safe area

**Result:** 5-10× fewer line primitives at the same fitting tolerance, smoother visible output.

### 4.2 Filled vs. Thin Region Classification
A naive skeletonizer collapses a solid black disk to a single point. Artie classifies each connected component using compactness ratio `4πA/P²`:
- Compactness ≥ 0.55 → "filled" → trace **outer + inner contours** (so donuts and character interiors retain their hole)
- Compactness < 0.55 → "thin" → standard skeleton processing

### 4.3 Skeleton Smoothing
Sub-pixel wobble in long thin strokes (hair, anime line-art) survives RDP simplification. Artie applies a 5-tap binomial kernel along each stroke's pixel ordering, preserving endpoint coordinates exactly so adjacent strokes still meet at junctions. Sub-pixel float coordinates are carried through to board-space scaling rather than rounded back to integer pixels.

### 4.4 Adaptive Resolution Slider
Live-measured processing-time curve identifies the "knee" at 1500 px max image dimension. The UI exposes 4 user-selectable presets (700, 1000, 1300, 1500 px) with measured time/quality tradeoffs, replacing hidden technical parameters with intuitive choices.

---

## 5. System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Web UI (HTML / JavaScript)                                  │
│   - File upload, text input, SVG paste                       │
│   - Live pen-pose visualization                              │
│   - Resolution slider, optimization presets                  │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP + WebSocket (rosbridge)
┌────────────────────────▼────────────────────────────────────┐
│  FastAPI Backend (Python)                                    │
│   - /api/preview, /api/draw                                  │
│   - Image pipeline (12 stages)                               │
│   - Path optimization                                        │
│   - Canonical plan generation                                │
└────────────────────────┬────────────────────────────────────┘
                         │ ROS 2 topics & services
┌────────────────────────▼────────────────────────────────────┐
│  ROS 2 (Humble) Nodes                                        │
│   - Cable Robot Plugin: forward/inverse kinematics           │
│   - Cable Supervisor: pen-tip tracking, contact detection    │
│   - Draw Executor: trajectory streaming                      │
└────────────────────────┬────────────────────────────────────┘
                         │ Webots IPC
┌────────────────────────▼────────────────────────────────────┐
│  Webots Simulation                                           │
│   - Physical accuracy (gravity, cable tension)               │
│   - Real-time visualization                                  │
└─────────────────────────────────────────────────────────────┘
```

**Tech stack:** Python 3.10, ROS 2 Humble, Webots R2025a, FastAPI, OpenCV 4.x, scikit-image, NumPy, Uvicorn, JavaScript ES2020.

---

## 6. Expected Outcomes

By the AI Expo presentation date, Artie will demonstrate:

1. **Live drawing of an audience-supplied image** — Audience members upload a phone photo of a sketch; the robot draws it on the board within 60 seconds.
2. **Live writing of audience-supplied text** — Multi-line text input is converted to vector strokes and drawn at human-readable size (~15 cm character height).
3. **Quantitative quality metrics** displayed in real time:
   - Pen-lift count
   - Total travel distance (pen-down + pen-up)
   - Processing time per stage
   - Path length reduction vs. naive baseline
4. **Side-by-side comparison** — User-supplied original image vs. robot output with quality metrics.

---

## 7. Project Timeline

| Phase | Duration | Status | Deliverables |
|-------|----------|--------|--------------|
| Mechanical design + Webots simulation | 4 weeks | ✅ Complete | URDF, four-cable kinematics |
| ROS 2 nodes (controller, supervisor, draw executor) | 3 weeks | ✅ Complete | All nodes operational |
| Image processing pipeline (basic) | 4 weeks | ✅ Complete | End-to-end raster→drawing |
| AI pipeline enhancements (Phase 3) | 3 weeks | ✅ Complete | Schneider Bezier, skeleton smoothing, filled regions |
| Path optimization | 2 weeks | ✅ Complete | -41% pen-lifts, -71% travel |
| Multi-modal input (text, SVG) | 2 weeks | ✅ Complete | Hershey vector font, SVG ingestion |
| Web UI polish + resolution slider | 1 week | ✅ Complete | Production-ready interface |
| Testing & documentation | 2 weeks | 🟡 In progress | Test suite (111 tests passing) |
| Demo preparation | 2 weeks | ⏳ Upcoming | Live demo script, fallback paths |

**Total project duration: ~23 weeks** (~5.5 months).

---

## 8. Team Roles

| Role | Responsibilities |
|------|------------------|
| **Team Leader** | System architecture, ROS 2 nodes, path planning, integration |
| **Computer Vision Lead** | Image pipeline, vectorization, curve fitting |
| **Mechanical / Simulation** | Webots scene, URDF, cable kinematics tuning |
| **UI / Frontend** | Web interface, real-time visualization, user experience |
| **QA & Testing** | Test suite maintenance, regression testing, demo reliability |

*Adjust team size and role assignments to match actual team composition.*

---

## 9. Budget Estimate

| Item | Estimated Cost (USD) |
|------|---------------------|
| 4× stepper motors with drivers | $400 |
| Aluminum extrusion frame (2.4 × 1.8 m) | $300 |
| Steel cables (4 × 5 m) + tensioners + pulleys | $150 |
| Pen-mounting carriage (custom 3D-printed) | $80 |
| Pneumatic pen-lift actuator + solenoid | $120 |
| Microcontroller (Raspberry Pi 5 + STM32) | $150 |
| Wiring, connectors, safety enclosure | $100 |
| Workshop tools, fasteners, contingency | $200 |
| **Total hardware estimate** | **$1,500** |

Software stack is fully open-source (ROS 2, Webots, OpenCV) with no licensing costs.

---

## 10. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Cable slack causing position drift | Medium | High | Cable-tension feedback loop in supervisor node |
| Pen contact loss on uneven boards | Medium | Medium | Adaptive engage-gap (currently 6 mm) with hysteresis |
| Image processing fails on low-quality scans | Low | Low | 4-level resolution slider lets user trade speed for quality |
| Live demo network failure | Low | High | Pre-loaded demo images cached locally, no internet dependency |
| Audience uploads unsupported file format | Medium | Low | Backend rejects with clear error message; only PNG/JPG/SVG accepted |

---

## 11. Why This Matters

Artie demonstrates that **non-trivial AI / computer-vision pipelines can run on commodity hardware in real time**, producing tangible physical output that audiences can see and verify. The project shows:

- **AI is not just deep learning** — Classical algorithms (Schneider 1990, Zhang-Suen, RDP, 2-opt) combined thoughtfully outperform many neural-net approaches for this task, with deterministic behavior and zero training cost.
- **End-to-end matters** — Most published research focuses on a single stage (e.g., "best skeleton extraction"). Artie shows that the *integration* between stages — pipeline calibration, error budgets, fallback paths — is where real-world quality comes from.
- **Accessibility** — Anyone with a phone can upload an image and see a robot reproduce it physically. No specialized software, no SVG conversion, no manual tracing.

---

## 12. Repository & Documentation

- **GitHub:** [your-repo-url] (to be added)
- **Architecture document:** `docs/ARCHITECTURE_AUDIT.md`
- **Pipeline design:** `docs/IMAGE_PIPELINE_PLAN.md`
- **Centerline pipeline:** `docs/SKETCH_CENTERLINE_PIPELINE.md`
- **Cable kinematics:** `docs/FOUR_CABLE_KINEMATIC_PLUGIN.md`
- **Test suite:** 111 tests passing, covering all pipeline stages

---

## Appendix A — Quick Technical Glossary

- **CLAHE** — Contrast-Limited Adaptive Histogram Equalization. Local contrast enhancement that doesn't blow out bright areas.
- **RDP** — Ramer-Douglas-Peucker. Recursive polyline simplification that drops points whose perpendicular distance to the chord is below a threshold.
- **Zhang-Suen** — Iterative thinning algorithm that erodes a binary shape until only its 1-pixel-wide skeleton remains.
- **Schneider Bezier Fit** — Algorithm from *Graphics Gems I* (1990) that fits cubic Bezier curves to a sequence of points by solving for both tangent magnitudes and recursively splitting at the maximum-error point.
- **2-opt** — Local-search heuristic for the Traveling Salesman Problem. Swaps pairs of edges to reduce total tour length.
- **Compactness Ratio** — `4πA/P²`. Equals 1 for a perfect circle, approaches 0 for thin shapes. Used to classify image components.
- **Four-Cable Parallel Kinematics** — Mechanical configuration where four cables, anchored at four corners, jointly determine the position of a moving carriage by their lengths.

---

*Prepared for IEEE AI Expo Registration · 2026*
