# Manning "In Action" Technical Book Publishing Guidelines

These guidelines structure project-based, hands-on engineering books where readers build and harden a complete real-world system across progressive chapters.

## 1. Pedagogical Philosophy ("Build -> Break -> Measure -> Harden")
* **Concrete Before Abstract:** Introduce a tangible engineering scenario and working code before generalizing into formal architectural patterns.
* **Annotated Listings:** Every major `lstlisting` block must be accompanied by numbered or step-by-step callout commentary explaining concurrency invariants, resource lifetimes, failure paths, and configuration knobs.
* **Running Case Study Continuity:** Carry forward the same domain models, service names, API contracts, and benchmarks across chapters so the book reads as one cohesive engineering journey rather than disconnected blog posts.

## 2. Mandatory Chapter Elements
1. **`objectivesbox` ("This Chapter Covers"):** Concise bulleted preview of concepts and build milestones.
2. **Code Evolution:** Show clearly how each chapter extends or hardens the system built in prior chapters.
3. **Callouts:** Use `notebox` ("Under the Hood"), `tipbox` ("Developer Tip"), `pitfallbox` ("Common Pitfall"), `warningbox` ("Production Warning"), and `architecturebox` ("Design Decision & Trade-off").
4. **Hands-On Lab (`exercisesbox`):** End every chapter with a concise summary and 2–3 practical engineering extensions or failure-injection exercises.

## 3. Grounding, Visuals & Typesetting
* **Reading Rhythm:** Preserve the template's `\setstretch{1.12}`, `\parskip`, and `\parindent` settings so body prose has comfortable vertical spacing. Do not compress body text to single spacing to reduce page count.
* **Grounded Data:** Use only metrics and measurements present in `technical_notes.md` / `experimental_log.md`.
* **Figures:** Store under `figures/` and reference as `figures/<filename>`. Never put `"Figure X:"` inside `\caption{...}`.
* **Code Width:** Wrap lines cleanly (`breaklines=true`, <= 88 chars/line) inside `lstlisting`.
* **Packages:** Keep `\usepackage[capitalize]{cleveref}` and preamble intact.
