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

import os
import json

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

from utils.gemini_utils import call_gemini_with_text_prompt

# --- TECHNICAL BOOK EDITORIAL & ENGINEERING REVIEW BOARD RUBRICS ---

SCORE_CALCULATION = {
    10: "Flagship publication quality (top 2% of technical books, e.g., DDIA / Systems Performance caliber). Deep systems internals, production-hardened code, crystal-clear mental models, and authoritative trade-off analysis.",
    8: "Strong publication-ready manuscript (top 10%). Technically accurate, well-structured pedagogy, solid code listings and benchmarks; only minor polish or clarifying examples needed.",
    6: "Solid draft with good technical substance, but some chapters lack production failure-mode depth, annotated code commentary, or cohesive pedagogical transitions.",
    5: "Major structural, pedagogical, or technical gaps: superficial explanations, fragile/incomplete code examples, missing trade-off analysis, or inconsistent terminology across chapters.",
    3: "Fragmentary or shallow draft resembling blog summaries rather than an authoritative engineering book.",
    1: "Not ready for publication; severe technical inaccuracies, ungrounded claims, or broken structure.",
}

AGENT_REVIEW_RUBRICS = (
    f"* 10: {SCORE_CALCULATION[10]};\n"
    f"* 8: {SCORE_CALCULATION[8]};\n"
    f"* 6: {SCORE_CALCULATION[6]};\n"
    f"* 5: {SCORE_CALCULATION[5]};\n"
    f"* 3: {SCORE_CALCULATION[3]};\n"
    f"* 1: {SCORE_CALCULATION[1]}. "
)

REVIEW_BOARD_PERSONAS = [
    (
        "principal_engineer",
        "Persona 1 — Principal Systems & Software Engineer (Technical Reviewer): "
        "Scrutinize architectural invariants, concurrency/memory/network trade-offs, capacity math, "
        "production failure modes, security/observability gaps, and whether code listings are idiomatic, "
        "robust (timeouts, error handling, resource bounds), and grounded in the benchmark notes.",
    ),
    (
        "developmental_editor",
        "Persona 2 — Executive Acquisitions & Developmental Editor (O'Reilly / Manning Style): "
        "Scrutinize reader progression, audience calibration for senior engineers, chapter openings "
        "(concrete production hooks + learning objectives), mental-model scaffolding, comparative trade-off tables, "
        "callout box utility, and chapter-ending summaries/exercises.",
    ),
    (
        "production_editor",
        "Persona 3 — Senior Technical Production & Copy Editor: "
        "Scrutinize terminology consistency across chapters, precision and clarity of prose, "
        "avoidance of marketing hype or hand-wavy filler, cross-reference coherence, figure/table integration, "
        "and code/caption readability.",
    ),
]


def _mean(values):
    if not values:
        return 0.0
    if np is not None:
        return float(np.mean(values))
    return float(sum(values)) / len(values)


def get_agentreview_system_prompt(
    is_knowledgeable=True,
    is_responsible=True,
    is_benign=True,
    persona: str = None,
):
    """
    Constructs the system prompt for a Technical Book Editorial & Engineering Review Board member.
    """
    bio = (
        "You are a senior member of a Technical Book Editorial & Engineering Review Board "
        "(O'Reilly, Manning, Addison-Wesley standard) evaluating an IT/Engineering book manuscript.\n\n"
    )

    if persona:
        for pid, pdesc in REVIEW_BOARD_PERSONAS:
            if pid == persona:
                bio += f"Editorial Role:\n{pdesc}\n\n"
                break

    if is_knowledgeable:
        bio += (
            "Technical Mastery: You are a Principal/Staff engineer and veteran technical editor "
            "with deep expertise in distributed systems, software architecture, cloud infrastructure, "
            "and AI/ML engineering systems.\n\n"
        )
    else:
        bio += "Technical Mastery: You are an external generalist reviewer unfamiliar with specialized systems internals.\n\n"

    if is_responsible:
        bio += (
            "Review Rigor: You read every chapter, code listing, table, and callout meticulously. "
            "You identify hand-wavy explanations, missing edge cases, ungrounded claims, pedagogical leaps, "
            "and structural inconsistencies.\n\n"
        )
    else:
        bio += "Review Rigor: You skim superficially and give brief high-level impressions.\n\n"

    if is_benign:
        bio += (
            "Editorial Intention: Your goal is to help the author elevate this manuscript to flagship "
            "publishing quality through specific, concrete, actionable critiques.\n\n"
        )
    else:
        bio += "Editorial Intention: You are an unforgiving critic focused primarily on flaws.\n\n"

    bio += f"## Rubrics for Overall Rating\n{AGENT_REVIEW_RUBRICS}"

    return bio


AGENTREVIEW_INSTRUCTIONS = """
Respond in the following format:

THOUGHT:
<THOUGHT>

REVIEW JSON:
```json
<JSON>
```

In <THOUGHT>, first analyze the manuscript chapter by chapter from the perspectives of a Principal Systems Engineer and a Senior Technical Book Editor.
Be concrete and specific to this manuscript (cite chapter names, code listings, tables, or architectural claims).

In <JSON>, provide the review in JSON format with the following fields:
- "Summary": A summary of the book manuscript's scope, target audience, and technical execution.
- "Strengths": A list of concrete engineering and pedagogical strengths.
- "Weaknesses": A list of concrete technical, architectural, code, or pedagogical weaknesses.
- "Technical_Depth": A rating from 1 to 4 (shallow, adequate, deep, authoritative systems rigor).
- "Practical_Utility": A rating from 1 to 4 (theoretical only, helpful, production-ready, indispensable field guide).
- "Code_and_Architecture_Quality": A rating from 1 to 4 (poor, fair, strong, exemplary).
- "Pedagogical_Clarity": A rating from 1 to 4 (disjointed, readable, well-scaffolded, masterclass).
- "Structural_Coherence": A rating from 1 to 4 (fragmented, acceptable, cohesive, seamless progression).
- "Editorial_Polish": A rating from 1 to 4 (rough draft, decent, clean, print-ready).
- "Originality": A rating from 1 to 4 (mirrors Technical_Depth / fresh synthesis).
- "Quality": A rating from 1 to 4 (mirrors Code_and_Architecture_Quality).
- "Clarity": A rating from 1 to 4 (mirrors Pedagogical_Clarity).
- "Significance": A rating from 1 to 4 (mirrors Practical_Utility).
- "Soundness": A rating from 1 to 4 (engineering accuracy and grounding, 1 to 4).
- "Presentation": A rating from 1 to 4 (mirrors Editorial_Polish).
- "Contribution": A rating from 1 to 4 (industry value of the book, 1 to 4).
- "Questions": A list of specific technical or editorial questions the author should resolve directly in the chapters.
- "Limitations": A list of scope gaps, missing failure modes, or operational caveats that should be transparently discussed.
- "Ethical Concerns": A boolean value indicating whether there are security, safety, licensing, or ethical concerns.
- "Overall": A rating from 1 to 10 (rough incomplete draft to flagship technical book).
- "Confidence": A rating from 1 to 5 (low, medium, high, very high, absolute).
- "Decision": Exactly one of: "Accept" (ready/near-ready for publication) or "Reject" (needs major revision).

Ensure valid JSON formatting so it can be parsed automatically."""


def _populate_score_aliases(review: dict) -> dict:
    """Ensures both Book-native axes and legacy compatibility axes exist on review dict."""
    if not isinstance(review, dict):
        return review

    alias_pairs = [
        ("Technical_Depth", "Originality"),
        ("Code_and_Architecture_Quality", "Quality"),
        ("Pedagogical_Clarity", "Clarity"),
        ("Practical_Utility", "Significance"),
        ("Structural_Coherence", "Soundness"),
        ("Editorial_Polish", "Presentation"),
        ("Practical_Utility", "Contribution"),
    ]
    for book_key, legacy_key in alias_pairs:
        if book_key not in review and legacy_key in review:
            review[book_key] = review[legacy_key]
        elif legacy_key not in review and book_key in review:
            review[legacy_key] = review[book_key]
    return review


def perform_review_agentreview(
    text,
    model_name,
    num_review_ensemble=3,
    temperature=0.75,
    reviewer_system_prompt=None,
    review_instruction_form=AGENTREVIEW_INSTRUCTIONS,
):
    print(f"Start Technical Book Editorial Board review using model: `{model_name}`")
    base_prompt = review_instruction_form
    base_prompt += f"""
Here is the technical book manuscript you are asked to review:
```
{text}
```"""

    if num_review_ensemble > 1:
        parsed_reviews = []
        for i in range(num_review_ensemble):
            persona_id = REVIEW_BOARD_PERSONAS[i % len(REVIEW_BOARD_PERSONAS)][0]
            sys_prompt = reviewer_system_prompt or get_agentreview_system_prompt(
                is_knowledgeable=True,
                is_responsible=True,
                is_benign=True,
                persona=persona_id,
            )
            print(
                f"Generating Editorial Review Board assessment {i + 1}/{num_review_ensemble} ({persona_id})..."
            )
            response = call_gemini_with_text_prompt(
                prompt=base_prompt,
                model_name=model_name,
                generation_configs={
                    "temperature": temperature,
                    "system_instruction": sys_prompt,
                },
            )
            parsed = _populate_score_aliases(response["parsed_response"])
            parsed_reviews.append(parsed)

        review = get_meta_review(model_name, temperature, parsed_reviews)
        if review is None:
            review = parsed_reviews[0]
        review = _populate_score_aliases(review)

        score_specs = [
            ("Technical_Depth", (1, 4)),
            ("Practical_Utility", (1, 4)),
            ("Code_and_Architecture_Quality", (1, 4)),
            ("Pedagogical_Clarity", (1, 4)),
            ("Structural_Coherence", (1, 4)),
            ("Editorial_Polish", (1, 4)),
            ("Originality", (1, 4)),
            ("Quality", (1, 4)),
            ("Clarity", (1, 4)),
            ("Significance", (1, 4)),
            ("Soundness", (1, 4)),
            ("Presentation", (1, 4)),
            ("Contribution", (1, 4)),
            ("Overall", (1, 10)),
            ("Confidence", (1, 5)),
        ]
        for score, limits in score_specs:
            scores = []
            for r in parsed_reviews:
                if isinstance(r, dict) and score in r:
                    try:
                        val = int(round(float(r[score])))
                        if limits[0] <= val <= limits[1]:
                            scores.append(val)
                    except (ValueError, TypeError):
                        pass
            if scores:
                review[score] = int(round(_mean(scores)))
        review = _populate_score_aliases(review)
    else:
        sys_prompt = reviewer_system_prompt or get_agentreview_system_prompt(
            is_knowledgeable=True,
            is_responsible=True,
            is_benign=True,
            persona="principal_engineer",
        )
        response = call_gemini_with_text_prompt(
            prompt=base_prompt,
            model_name=model_name,
            generation_configs={
                "temperature": temperature,
                "system_instruction": sys_prompt,
            },
        )
        review = _populate_score_aliases(response["parsed_response"])
    return review


# Book-oriented alias
perform_book_editorial_review = perform_review_agentreview

meta_reviewer_system_prompt = """You are the Editor-in-Chief and Principal Systems Director at a premier technical publisher (O'Reilly / Manning / Addison-Wesley).
You are synthesizing {reviewer_count} independent assessments from the Technical Book Editorial Review Board (Principal Engineer, Developmental Editor, and Production/Copy Editor).
Aggregate all feedback into one balanced, actionable meta-review JSON in the exact requested schema, highlighting high-leverage engineering and pedagogical improvements."""


def get_meta_review(model, temperature, reviews):
    review_text = ""
    for i, r in enumerate(reviews):
        persona_name = REVIEW_BOARD_PERSONAS[i % len(REVIEW_BOARD_PERSONAS)][0]
        review_text += f"""
Review {i + 1}/{len(reviews)} ({persona_name}):
```
{json.dumps(r)}
```
"""
    base_prompt = AGENTREVIEW_INSTRUCTIONS + review_text
    response = call_gemini_with_text_prompt(
        prompt=base_prompt,
        model_name=model,
        generation_configs={
            "temperature": temperature,
            "system_instruction": meta_reviewer_system_prompt.format(
                reviewer_count=len(reviews)
            ),
        },
    )
    meta_review = response["parsed_response"]
    return _populate_score_aliases(meta_review)
