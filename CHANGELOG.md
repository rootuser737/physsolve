# Changelog

## 2.0.0 - Lab redesign, live teacher, saving
* New design system shared with VoxCut: cards, top tabs, themes (6) + accent colours, themed plots and chat, responsive plot layout,
  verdict chips and metric tiles, new icon.
* **Live teacher**: offline explanations at 3 levels, live captions + speech, watch mode, guided lessons, convergence experiment,
  quizzes from your own results, plain-language chat; AI teacher optional with automatic offline fallback.
* **Saving**: images (PNG/SVG/PDF, chat PDF, AI visuals), videos (MP4/GIF, narrated), documents (DOCX/PDF/HTML/Markdown), sessions, Library tab.
* **Rendering you can rely on**: compatible (software) Chromium mode by default, automatic health check with fallback view,
  one-click diagnostics, blitted animation with adaptive frame skipping for slow PCs.
* Physics core moved to `systems.py` (unchanged maths). New tests; CI now tests and then self-tests every packaged build.

## 1.x
* SciSketch: trust-checked simulations, AI tutor dock, offline renderer (KaTeX, Mermaid, SVG, p5.js, three.js).
