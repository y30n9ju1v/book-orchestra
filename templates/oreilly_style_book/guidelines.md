# O'Reilly-Style Technical & Engineering Book Publishing Guidelines

These guidelines govern manuscript structure, engineering depth, pedagogical clarity, code quality, and typesetting for IT and systems engineering books.

## 1. Audience & Editorial Voice
* **Primary Audience:** Senior Software Engineers, Platform/Infrastructure Engineers, Staff Architects, and ML/AI Systems Engineers.
* **Tone:** Authoritative, pragmatic, opinionated where backed by evidence, yet honest about trade-offs. Write like a Principal Engineer mentoring peers—skip elementary syntax tutorials unless directly illuminating a systems subtlety, and never hand-wave operational complexity.
* **Clarity over Jargon:** Define domain-specific acronyms on first use and keep canonical terminology consistent across every chapter.

## 2. Book & Chapter Pedagogical Architecture
Every main chapter should follow a clear progressive-disclosure flow:
1. **Chapter Learning Objectives (`objectivesbox`):** 3–5 concrete architectural mental models or engineering skills the reader will master.
2. **Real-World Motivation / Production Hook:** Open with a concrete engineering bottleneck, scaling cliff, or production incident scenario that motivates the chapter.
3. **Mental Models & Deep Architectural Mechanics:** Explain *how things work under the hood* (memory layout, scheduling, network hops, state machines, capacity equations) supported by clean system diagrams (`\includegraphics`).
4. **Production-Grade Code Listings (`lstlisting`):** Provide complete, idiomatic, runnable implementations with type annotations, error handling, timeouts/backpressure, and detailed architectural commentary explaining *why* each decision matters.
5. **Comparative Trade-off Tables (`booktabs` / `tabularx`):** Compare competing designs, protocols, or benchmarks across latency, throughput, memory, failure modes, and operational burden.
6. **Publisher Callout Boxes:** Use `tipbox` (engineering best practices), `warningbox` / `pitfallbox` (production traps and failure modes), `notebox` (under-the-hood mechanics), and `architecturebox` (key design trade-offs) judiciously—1 to 3 per major section.
7. **Chapter Summary & Engineering Exercises (`exercisesbox`):** Distill key takeaways and include 2–3 senior-level architecture/design review exercises.

## 3. Code & Quantitative Integrity Rules
* **No Placeholder Logic:** Never omit core algorithmic or systems logic with `# TODO` or `pass`.
* **Margins & Wrapping:** Always keep `breaklines=true` in `lstlisting` and avoid overly wide lines (>88 characters) so code never clips the right margin in print (7in × 9.25in trade book trim).
* **Grounded Benchmarks:** Every quantitative table and performance plot must trace directly to `technical_notes.md` (or `experimental_log.md`). Never fabricate latency, throughput, or cost numbers.

## 4. Visuals, Tables, and Typesetting
* **Figures:** Store all figures in `figures/` and reference with the `figures/` prefix (`\includegraphics[width=0.9\linewidth]{figures/...}`). Keep captions informative and free of `"Figure X:"` prefixes.
* **Tables:** Use `\toprule`, `\midrule`, `\bottomrule` from `booktabs` and `\small` or `tabularx` for multi-column comparisons. Place tables near their first `\cref{tab:...}` reference.
* **Cross-References:** Use `\cref{ch:...}`, `\cref{sec:...}`, `\cref{fig:...}`, `\cref{tab:...}`, `\cref{lst:...}` consistently.
* **Citations:** Cite only verified keys in `references.bib` / `citation_map.json` via `\cite{key}`.
* **Packages:** Do NOT change `\usepackage[capitalize]{cleveref}` to `cleverref`, and keep preamble packages intact.
