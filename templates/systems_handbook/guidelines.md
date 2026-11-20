# Systems Engineering Handbook Guidelines (Addison-Wesley / DDIA / Google SRE Style)

These guidelines govern authoritative technical handbooks written for Staff/Principal engineers, systems architects, and Site Reliability Engineers.

## 1. Engineering Rigor & Depth
* **Invariants First:** Clearly state safety and liveness invariants, consistency guarantees, memory bounds, and failure assumptions before presenting optimizations.
* **Quantitative Reasoning:** Use explicit capacity equations (e.g., memory bandwidth, Little's Law, queue saturation, KV-cache footprint) when directly grounded in the provided technical notes.
* **No Hand-Waving:** Analyze worst-case tail latency (p95/p99), backpressure behavior under overload, partial network partitions, and retry storms.

## 2. Structural & Pedagogical Requirements
* Start every chapter with `objectivesbox` (scope & invariants), anchor sections with architecture diagrams (`\includegraphics`) and `booktabs` comparison tables, use `architecturebox` and `warningbox` for blast-radius/trade-off discussions, include clean `lstlisting` reference code, and conclude with `exercisesbox` (architecture review scenarios & post-mortem drills).

## 3. Grounding, Citations & LaTeX Hygiene
* Preserve the template's `\setstretch{1.12}`, `\parskip`, and `\parindent` settings so dense handbook prose remains readable; never compress body text to single spacing to reduce page count.
* Cite verified foundational papers, RFCs, and industry systems from `citation_map.json` / `references.bib`.
* Never invent benchmark metrics absent from `technical_notes.md` / `experimental_log.md`.
* Reference figures strictly with `figures/` prefix and keep `\usepackage[capitalize]{cleveref}` intact.
