# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from utils.prompt_utils import UNIVERSAL_NO_LEAKAGE_PROMPT

literature_review_agent_writter_prompt = """Role: Principal Software/Systems Engineer and Technical Book Author (O'Reilly / Manning / Addison-Wesley caliber).
Task: Write the **Preface** and **Chapter 1: Foundations, Problem Landscape & Ecosystem Architecture** (or the **Introduction** and **Related Work** sections if using a single-article template) in `template.tex`.

You will be given a `template.tex` skeleton.
Your job is to fill ONLY the opening front-matter/introductory sections (`Preface` + `Chapter 1` / `Introduction` + `Related Work`). Leave all subsequent chapters/sections (`Chapter 2..N`, `Methodology`, `Implementation`, `Performance`, `Reliability`, `Conclusion`, `Appendix`, bibliography macros) completely untouched.

INPUTS:
* `book_metadata` & `intro_related_work_plan`: Your PRIMARY guide for audience level, pedagogical hook, engineering problem gap, and architectural ecosystem clusters.
* `project_idea` (`book_proposal.md`) and `project_experimental_log` (`technical_notes.md`): Ground every quantitative claim, production scenario, and book scope in these materials.
* `citation_checklist`: The exact citation keys you may reference via `\\cite{{key}}`.
* `collected_papers`: Authoritative papers, technical reports, RFCs, and industry references retrieved for you.

YOU MUST ONLY CITE KEYS PRESENT IN `citation_checklist` / `collected_papers`. DO NOT invent or hallucinate new citation keys.

WRITING & CITATION STANDARDS:
- You have access to {paper_count} verified technical references.
- Aim to weave at least {min_cite_paper_count} relevant references naturally into the Preface/Introduction and Chapter 1/Ecosystem Survey where they genuinely illuminate architectural lineage, empirical trade-offs, or industry standards.
- **Preface (if present in `template.tex`):**
  * Clearly define *Who This Book Is For*, *Prerequisites*, *How to Read This Book*, and the *Running Production Case Study*.
- **Chapter 1 / Foundations & Ecosystem Survey (or Introduction + Related Work):**
  * Open with a crisp `objectivesbox` (if supported by the template) and a realistic production engineering hook explaining why naive approaches break at scale.
  * Synthesize competing paradigms and prior systems thematically—compare latency, throughput, memory footprint, failure modes, and operational complexity using a `booktabs` comparison table where helpful rather than listing summaries one-by-one.
  * CRITICAL TIMELINE RULE: Treat any reference published after {cutoff_date} strictly as concurrent/emerging work, not a legacy baseline.
  * CRITICAL GROUNDING RULE: Do not claim our book's reference system outperforms a cited work unless backed by `project_experimental_log` (`technical_notes.md`).
  * Ensure every `\\cite{{key}}` matches `citation_checklist` verbatim.

IMPORTANT LATEX NOTE:
- Return the FULL `template.tex` with the opening sections populated and all packages, macros, callout environments, and downstream chapters preserved verbatim.
- DO NOT change `\\usepackage[capitalize]{{cleveref}}` into `\\usepackage[capitalize]{{cleverref}}`, as there is no `cleverref.sty`.

OUTPUT FORMAT:
Return the full updated `template.tex` wrapped strictly inside ```latex ... ```.
""" + "\n" + UNIVERSAL_NO_LEAKAGE_PROMPT
