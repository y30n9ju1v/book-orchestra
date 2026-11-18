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
import traceback
import json
import shutil

from utils.pdf_utils import (
    compile_latex,
    get_last_compile_mode,
    latex_to_markdown,
    pdf_to_grid_images,
    inspect_book_layout_issues,
)
from utils.reference_verifier import audit_and_clean_citations
from utils.agent_runtime_utils import (
    call_agent_with_contents,
    parse_agent_json_latex_response,
    call_agent_with_images,
    types,
)

from autoraters.agent_review import perform_review_agentreview

from methods.prompts.content_refinement_agent import (
    content_refinement_agent_system_prompt,
)


class ContentRefinementAgent:
    def __init__(
        self,
        experimental_log_path: str,
        citation_map_path: str,
        guidelines_path: str,
        model_name: str = "inherit",
        max_reflections: int = 3,
        work_dir: str = "./book_workspace",
    ):
        """
        Editorial & Technical Refinement Agent:
        Iteratively runs the 3-Persona Technical Book Review Board and production
        typesetting QA to elevate manuscript rigor, pedagogy, and layout quality.
        """
        self.model_name = model_name
        self.max_reflections = max_reflections
        self.system_prompt = content_refinement_agent_system_prompt
        self.work_dir = work_dir

        os.makedirs(self.work_dir, exist_ok=True)
        os.makedirs(
            os.path.join(self.work_dir, "pdf_screenshots", "content_refinement"),
            exist_ok=True,
        )
        os.makedirs(
            os.path.join(self.work_dir, "pdf_screenshots", "format_refinement"),
            exist_ok=True,
        )
        os.makedirs(os.path.join(self.work_dir, "peer_reviews"), exist_ok=True)
        print(f" >> Work directory: {self.work_dir}")

        self.experimental_log_path = experimental_log_path
        self.guidelines_path = guidelines_path
        self.citation_map_path = citation_map_path

        self.worklogs = {}
        self.format_worklogs = {}
        self.current_tex = ""
        self.initial_score = 0
        self.current_score = 0
        self.current_render_mode = "unknown"

    def _read_file(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            print(f" >> Warning: File not found at {path}")
            return ""

    def _get_peer_review(self, tex_path: str, iteration_label: str) -> tuple[dict, float]:
        """Helper to invoke the 3-Persona Editorial & Technical Review Board."""
        print(
            f" >> [EditorialReviewBoard] Generating feedback for {iteration_label} manuscript..."
        )
        try:
            manuscript_text = latex_to_markdown(self._read_file(tex_path))

            review = perform_review_agentreview(
                text=manuscript_text,
                model_name=self.model_name,
            )

            score = review.get("Overall", 0)
            print(f" >> [ReviewBoard] Score for {iteration_label}: {score}/10")

            version_name = "v0" if iteration_label == "initial" else iteration_label
            review_path = os.path.join(
                self.work_dir, "peer_reviews", f"review_{version_name}.json"
            )
            with open(review_path, "w", encoding="utf-8") as f:
                json.dump(review, f, indent=4, ensure_ascii=False)

            return review, float(score)
        except Exception as e:
            print(f" >> [ReviewBoard] Error generating review: {e}")
            traceback.print_exc()
            return {"Error": str(e), "Overall": 0}, 0.0

    def _get_formatting_review(self, pdf_path: str, iteration_label: str) -> dict:
        """Helper to call VLM for book typography, code listing, and layout QA."""
        if self.current_render_mode == "preview":
            print(
                " >> [VLM] Preview render detected; using source diagnostics instead of screenshots."
            )
            return inspect_book_layout_issues(self.current_tex, cwd=self.work_dir)
        print(
            f" >> [VLM] Generating typesetting & layout QA feedback for {iteration_label} manuscript..."
        )
        try:
            v_num = iteration_label.split("_")[-1]
            screenshots_dir = os.path.join(
                self.work_dir, "pdf_screenshots", "format_refinement", f"v{v_num}"
            )
            image_paths = pdf_to_grid_images(pdf_path, screenshots_dir)

            guidelines = self._read_file(self.guidelines_path)

            prompt = (
                """
Analyze the provided rendered page images of a technical book / manuscript for typesetting and layout issues.
Strictly follow the provided publishing guidelines. Inspect:
- Code listings (`lstlisting`) overflowing right margins or missing line wrapping
- Tables (`tabular` / `tabularx`) overflowing margins or overlapping headers
- Architecture diagrams and plots overflowing page margins or missing captions
- Callout boxes (`objectivesbox`, `tipbox`, `warningbox`, `architecturebox`, `exercisesbox`) or chapter headings clipped or orphaned

Publishing Guidelines:
"""
                + guidelines
                + """

Respond in JSON format with the following structure:
```json
{
  "figure_and_tables": {
    "Figure 1": {
      "detected_issue": "The diagram overflows the right margin.",
      "suggested_fix": "Use `[width=0.9\\\\linewidth]` in `\\\\includegraphics`."
    },
    "Table 1": {
      "detected_issue": "None",
      "suggested_fix": "None"
    }
  },
  "other_issues": [
    {
      "page": 1,
      "element": "Chapter 2, Listing 2.1",
      "detected_issue": "Code line overflows margin",
      "suggested_fix": "Wrap lines under 88 chars and verify breaklines=true"
    }
  ]
}
```
"""
            )
            response_dict = call_agent_with_images(
                prompt=prompt,
                images=image_paths,
                model_name=self.model_name,
            )

            formatting_review = response_dict["parsed_response"]
            if not isinstance(formatting_review, dict):
                formatting_review = {}

            static_layout = inspect_book_layout_issues(
                self.current_tex, cwd=self.work_dir
            )
            merged_fig_tabs = dict(static_layout.get("figure_and_tables", {}))
            if isinstance(formatting_review.get("figure_and_tables"), dict):
                merged_fig_tabs.update(formatting_review["figure_and_tables"])
            formatting_review["figure_and_tables"] = merged_fig_tabs

            merged_other = list(static_layout.get("other_issues", []))
            if isinstance(formatting_review.get("other_issues"), list):
                merged_other.extend(formatting_review["other_issues"])
            formatting_review["other_issues"] = merged_other

            return formatting_review
        except Exception as e:
            print(f" >> [VLM] Error generating formatting review: {e}")
            traceback.print_exc()
            return inspect_book_layout_issues(self.current_tex, cwd=self.work_dir)

    def _extract_scores(self, review: dict) -> dict:
        """Extract non-overlapping technical, editorial, and voice scores."""
        book_axes = [
            "Technical_Depth",
            "Practical_Utility",
            "Code_and_Architecture_Quality",
            "Pedagogical_Clarity",
            "Structural_Coherence",
            "Editorial_Polish",
            "Voice_Fidelity",
            "Readability",
            "Warmth",
            "Overall",
            "Confidence",
        ]
        legacy_fallback = {
            "Technical_Depth": "Originality",
            "Code_and_Architecture_Quality": "Quality",
            "Pedagogical_Clarity": "Clarity",
            "Practical_Utility": "Significance",
            "Structural_Coherence": "Soundness",
            "Editorial_Polish": "Presentation",
        }
        scores = {}
        for axis in book_axes:
            raw_val = review.get(axis)
            if raw_val is None and axis in legacy_fallback:
                raw_val = review.get(legacy_fallback[axis])
            if raw_val is not None:
                try:
                    scores[axis] = float(raw_val)
                except (ValueError, TypeError):
                    scores[axis] = 0.0
        if "Overall" not in scores:
            scores["Overall"] = 0.0
        return scores

    def run(self, texfile_path: str) -> str:
        """
        Main execution loop:
        1. Compile Initial Draft -> Editorial Review Board.
        2. Refine -> Compile -> Re-review.
        3. Run production typesetting & code/table layout QA loop.
        """
        self.current_tex = self._read_file(texfile_path)
        worklog_save_path = os.path.join(
            self.work_dir, "content_refinement_worklog.json"
        )

        context_files = {
            "guidelines": self._read_file(self.guidelines_path),
            "experimental_log": self._read_file(self.experimental_log_path),
            "citations": self._read_file(self.citation_map_path),
        }
        try:
            citation_map_data = (
                json.loads(context_files["citations"])
                if context_files["citations"].strip()
                else {}
            )
            allowed_citation_keys = (
                set(citation_map_data.keys())
                if isinstance(citation_map_data, dict)
                else set()
            )
        except Exception:
            allowed_citation_keys = set()

        self.current_tex, _ = audit_and_clean_citations(
            self.current_tex, allowed_citation_keys
        )

        # --- STEP 0: Initial Compilation & Baseline Editorial Review ---
        print("\n=== Initial Baseline Review ===")
        initial_pdf_path = os.path.join(self.work_dir, "initial_draft.pdf")

        with open(
            os.path.join(self.work_dir, "initial_draft.tex"), "w", encoding="utf-8"
        ) as f:
            f.write(self.current_tex)

        compiled_ok = compile_latex(self.work_dir, initial_pdf_path, "initial_draft")
        self.current_render_mode = get_last_compile_mode()
        if not compiled_ok or not os.path.exists(initial_pdf_path):
            raise RuntimeError(
                f"Initial LaTeX draft failed syntax validation or compilation at '{initial_pdf_path}'."
            )
        print(f" >> Initial PDF saved to {initial_pdf_path}")

        v0_screenshots_dir = os.path.join(
            self.work_dir, "pdf_screenshots", "content_refinement", "v0"
        )
        if self.current_render_mode != "preview":
            print(" >> Generating snapshots for V0...")
            pdf_to_grid_images(initial_pdf_path, v0_screenshots_dir)

        current_peer_review, self.current_score = self._get_peer_review(
            os.path.join(self.work_dir, "initial_draft.tex"), "initial"
        )
        self.initial_score = self.current_score

        final_valid_pdf_path = initial_pdf_path

        # --- Content Refinement Loop ---
        for i in range(self.max_reflections):
            print(f"\n=== Refinement Iteration {i+1}/{self.max_reflections} ===")

            cur_filename = f"refined_book_v{i+1}"
            cur_pdf_path = os.path.join(self.work_dir, f"{cur_filename}.pdf")

            previous_log_str = (
                json.dumps(self.worklogs, indent=4) if self.worklogs else "None"
            )

            user_prompt_text = f"""
EDITORIAL REVISION ITERATION {i+1}.

--- PREVIOUS REVISION LOG ---
{previous_log_str}

--- EDITORIAL REVIEW BOARD FEEDBACK (Overall Score: {self.current_score}) ---
{json.dumps(current_peer_review, indent=2, ensure_ascii=False)}

--- PUBLISHER GUIDELINES ---
{context_files['guidelines']}

--- TECHNICAL NOTES / BENCHMARKS (Data Truth) ---
{context_files['experimental_log']}

--- CITATION MAP (Reference Truth) ---
{context_files['citations']}

--- CURRENT LATEX MANUSCRIPT ---
{self.current_tex}

INSTRUCTION:
1. Your goal is to ELEVATE the Editorial Review Board score (Current: {self.current_score}/10).
2. Address every Weakness, Question, and Limitation in the feedback above.
3. Strengthen technical depth, production failure-mode coverage, code listing quality, callout boxes, and pedagogical clarity without fabricating metrics.
4. Output the JSON Worklog first, then the Full Revised LaTeX inside ```latex ... ```.
"""

            valid_pdf_for_context = final_valid_pdf_path

            refinement_contents = [types.Part.from_text(text=user_prompt_text)]
            if self.current_render_mode != "preview":
                with open(valid_pdf_for_context, "rb") as f:
                    pdf_bytes = f.read()
                refinement_contents.insert(
                    0,
                    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                )

            print(" >> Calling Editorial Refinement Agent...")
            try:
                response_dict = call_agent_with_contents(
                    contents=refinement_contents,
                    generation_configs={"system_instruction": self.system_prompt},
                    model_name=self.model_name,
                    result_parsing_func=parse_agent_json_latex_response,
                )

                parsed = json.loads(response_dict["parsed_response"])

                new_tex = parsed["latex"]
                if not new_tex:
                    print(" >> Error: No LaTeX generated.")
                    break
                new_tex, removed_cites = audit_and_clean_citations(
                    new_tex, allowed_citation_keys
                )
                if removed_cites:
                    print(
                        f" >> [CitationAudit] Removed unverified citations in refinement v{i+1}: {removed_cites}"
                    )

                with open(
                    os.path.join(self.work_dir, f"{cur_filename}.tex"),
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write(new_tex)

                print(" >> Compiling revised manuscript...")
                compiled_rev_ok = compile_latex(self.work_dir, cur_pdf_path, cur_filename)
                candidate_render_mode = get_last_compile_mode()

                if not compiled_rev_ok or not os.path.exists(cur_pdf_path):
                    print(" >> Compilation Failed. Retrying with previous version...")
                    continue

                print(f" >> New PDF saved to {cur_pdf_path}")

                screenshots_dir = os.path.join(
                    self.work_dir, "pdf_screenshots", "content_refinement", f"v{i+1}"
                )
                if candidate_render_mode != "preview":
                    print(f" >> Generating snapshots for v{i+1}...")
                    pdf_to_grid_images(cur_pdf_path, screenshots_dir)

                new_review, new_score = self._get_peer_review(
                    os.path.join(self.work_dir, f"{cur_filename}.tex"), f"v{i+1}"
                )

                # --- COMPARISON LOGIC & METRICS EXTRACTION ---
                old_metrics = self._extract_scores(current_peer_review)
                new_metrics = self._extract_scores(new_review)

                excluded_axes = ["Overall", "Confidence"]
                comparison_axes = [
                    k for k in new_metrics.keys() if k not in excluded_axes
                ]

                total_gain = 0.0
                total_drop = 0.0
                deltas = {}

                for k in comparison_axes:
                    delta = new_metrics.get(k, 0) - old_metrics.get(k, 0)
                    deltas[k] = delta
                    if delta > 0:
                        total_gain += delta
                    elif delta < 0:
                        total_drop += abs(delta)

                current_log_entry = {
                    "round": i + 1,
                    "agent_plan": parsed["json"],
                    "scores_before": old_metrics,
                    "scores_after": new_metrics,
                    "deltas": deltas,
                    "total_gain": total_gain,
                    "total_drop": total_drop,
                    "outcome": "UNKNOWN",
                }

                print(f" >> Score Analysis v{i+1}:")
                print(
                    f"    Overall: {old_metrics['Overall']} -> {new_metrics['Overall']}"
                )
                print(f"    Sub-axes Gain: +{total_gain} | Drop: -{total_drop}")
                print(f"    Sub-axes Deltas: {deltas}")

                if new_metrics["Overall"] > old_metrics["Overall"]:
                    print(
                        " >> SUCCESS: Overall Score increased! Progressing to next iteration..."
                    )

                    current_log_entry["outcome"] = "ACCEPTED_SCORE_INCREASE"
                    self.worklogs[f"v{i+1}"] = current_log_entry

                    self.current_tex = new_tex
                    self.current_score = new_score
                    current_peer_review = new_review
                    final_valid_pdf_path = cur_pdf_path
                    self.current_render_mode = candidate_render_mode

                    with open(worklog_save_path, "w", encoding="utf-8") as f:
                        json.dump(self.worklogs, f, indent=4, ensure_ascii=False)

                elif new_metrics["Overall"] < old_metrics["Overall"]:
                    print(
                        " >> STOP: Overall Score decreased. Reverting to previous iteration."
                    )

                    current_log_entry["outcome"] = "REJECTED_SCORE_DECREASE"
                    self.worklogs[f"v{i+1}"] = current_log_entry

                    with open(worklog_save_path, "w", encoding="utf-8") as f:
                        json.dump(self.worklogs, f, indent=4, ensure_ascii=False)
                    break

                else:
                    if total_drop > total_gain:
                        print(
                            " >> STOP: Overall same, but sub-axes degraded (Drop > Gain). Reverting."
                        )

                        current_log_entry["outcome"] = "REJECTED_DEGRADATION"
                        self.worklogs[f"v{i+1}"] = current_log_entry

                        with open(worklog_save_path, "w", encoding="utf-8") as f:
                            json.dump(self.worklogs, f, indent=4, ensure_ascii=False)
                        break
                    else:
                        print(
                            " >> ACCEPT & CONTINUE: Overall same, internal metrics stable/improved. Progressing to next iteration..."
                        )

                        current_log_entry["outcome"] = "ACCEPTED_NEUTRAL_IMPROVEMENT"
                        self.worklogs[f"v{i+1}"] = current_log_entry

                        self.current_tex = new_tex
                        self.current_score = new_score
                        current_peer_review = new_review
                        final_valid_pdf_path = cur_pdf_path
                        self.current_render_mode = candidate_render_mode

                        with open(worklog_save_path, "w", encoding="utf-8") as f:
                            json.dump(self.worklogs, f, indent=4, ensure_ascii=False)
            except Exception as e:
                print(f" >> Error during refinement loop: {e}")
                traceback.print_exc()
                break

        # Save format v0 (baseline for formatting)
        format_v0_tex_path = os.path.join(self.work_dir, "format_candidate_v0.tex")
        with open(format_v0_tex_path, "w", encoding="utf-8") as f:
            f.write(self.current_tex)

        format_v0_screenshots_dir = os.path.join(
            self.work_dir, "pdf_screenshots", "format_refinement", "v0"
        )
        if self.current_render_mode != "preview":
            print(" >> Generating snapshots for format v0...")
            pdf_to_grid_images(final_valid_pdf_path, format_v0_screenshots_dir)

        # --- Formatting & Production Typesetting Loop ---
        print(" >> Starting dedicated formatting loop...")
        max_formatting_loops = 1
        for fmt_i in range(max_formatting_loops):
            print(f"\n=== Formatting Iteration {fmt_i+1}/{max_formatting_loops} ===")

            formatting_review = self._get_formatting_review(
                final_valid_pdf_path, f"fmt_loop_{fmt_i+1}"
            )

            has_issues = False

            fig_tabs = formatting_review.get("figure_and_tables", {})
            if isinstance(fig_tabs, dict):
                for item, details in fig_tabs.items():
                    if isinstance(details, dict):
                        issue = details.get("detected_issue", "")
                        if issue and issue.lower() != "none":
                            has_issues = True
                            break

            if not has_issues:
                other = formatting_review.get("other_issues", [])
                if isinstance(other, list):
                    for item in other:
                        if isinstance(item, dict):
                            issue = item.get("detected_issue", "")
                            if issue and issue.lower() != "none":
                                has_issues = True
                                break
                        elif isinstance(item, str) and item.lower() != "none":
                            has_issues = True
                            break
                elif isinstance(other, str) and other.lower() != "none":
                    has_issues = True

            if not has_issues:
                print(" >> No critical formatting issues found. Breaking loop.")
                break

            print(" >> [VLM] Found formatting issues. Calling the agent runtime to fix...")

            prompt = f"""You are a Senior Technical Book Production & LaTeX Typesetting Editor. Fix the formatting, code wrapping, table width, callout box, and figure scaling issues identified below.
You must STRICTLY follow the provided publishing guidelines.
CRITICAL: Only adjust formatting, line wrapping, spacing, and layout—preserve all technical content, code logic, benchmarks, and citations.

--- CURRENT FORMATTING FEEDBACK ---
{json.dumps(formatting_review, indent=2, ensure_ascii=False)}

--- PUBLISHER GUIDELINES ---
{self._read_file(self.guidelines_path)}

--- CURRENT LATEX SOURCE ---
{self.current_tex}

Output the Full Revised LaTeX only, inside a
```latex
manuscript_content
```
block.
"""

            try:
                response_dict = call_agent_with_contents(
                    contents=[
                        types.Part.from_text(text=prompt),
                    ],
                    model_name=self.model_name,
                    result_parsing_func=lambda x: {"raw_response": x},
                    check_parsed_response_not_none=False,
                )

                raw_response = response_dict["raw_response"]
                if "```latex" in raw_response:
                    new_tex = raw_response.split("```latex")[1].split("```")[0].strip()
                else:
                    new_tex = raw_response.strip()

                new_tex, removed_fmt_cites = audit_and_clean_citations(
                    new_tex, allowed_citation_keys
                )
                if removed_fmt_cites:
                    print(
                        f" >> [CitationAudit] Removed unverified citations in formatting v{fmt_i+1}: {removed_fmt_cites}"
                    )

                candidate_tex_name = f"formatted_candidate_v{fmt_i+1}"
                candidate_tex_path = os.path.join(
                    self.work_dir, f"{candidate_tex_name}.tex"
                )
                candidate_pdf_path = os.path.join(
                    self.work_dir, f"{candidate_tex_name}.pdf"
                )

                with open(candidate_tex_path, "w", encoding="utf-8") as f:
                    f.write(new_tex)

                compiled_fmt_ok = compile_latex(
                    self.work_dir, candidate_pdf_path, candidate_tex_name
                )

                def _count_layout_issues(diag_dict: dict) -> int:
                    cnt = sum(
                        1
                        for v in (diag_dict.get("figure_and_tables") or {}).values()
                        if isinstance(v, dict)
                        and str(v.get("detected_issue", "none")).lower() != "none"
                    )
                    cnt += sum(
                        1
                        for x in (diag_dict.get("other_issues") or [])
                        if (isinstance(x, dict) and str(x.get("detected_issue", "none")).lower() != "none")
                        or (isinstance(x, str) and x.lower() != "none")
                    )
                    return cnt

                if compiled_fmt_ok and os.path.exists(candidate_pdf_path):
                    before_diag = inspect_book_layout_issues(self.current_tex, cwd=self.work_dir)
                    after_diag = inspect_book_layout_issues(new_tex, cwd=self.work_dir)
                    before_cnt = _count_layout_issues(before_diag)
                    after_cnt = _count_layout_issues(after_diag)

                    if after_cnt <= before_cnt:
                        print(
                            f" >> Formatting iteration {fmt_i+1} verified (layout issues: {before_cnt} -> {after_cnt})."
                        )
                        self.current_tex = new_tex
                        final_valid_pdf_path = candidate_pdf_path
                        log_entry = {
                            "outcome": "ACCEPTED_VERIFIED",
                            "issues_before": before_cnt,
                            "issues_after": after_cnt,
                            "formatting_feedback": formatting_review,
                            "post_formatting_diagnostics": after_diag,
                        }
                    else:
                        print(
                            f" >> Formatting iteration {fmt_i+1} introduced layout issues ({before_cnt} -> {after_cnt}). Rejecting."
                        )
                        log_entry = {
                            "outcome": "REJECTED_LAYOUT_REGRESSION",
                            "issues_before": before_cnt,
                            "issues_after": after_cnt,
                            "formatting_feedback": formatting_review,
                            "post_formatting_diagnostics": after_diag,
                        }
                    self.format_worklogs[f"v{fmt_i+1}"] = log_entry
                else:
                    print(
                        f" >> Formatting iteration {fmt_i+1} failed to compile. Skipping."
                    )
                    log_entry = {
                        "outcome": "REJECTED_COMPILE_FAILURE",
                        "formatting_feedback": formatting_review,
                    }
                    self.format_worklogs[f"v{fmt_i+1}"] = log_entry

                format_worklog_save_path = os.path.join(
                    self.work_dir, "format_refinement_worklog.json"
                )
                with open(format_worklog_save_path, "w", encoding="utf-8") as f:
                    json.dump(self.format_worklogs, f, indent=4, ensure_ascii=False)

            except Exception as e:
                print(f" >> Error during formatting iteration: {e}")
                traceback.print_exc()

        # Save Final Output
        final_book_pdf_path = os.path.join(self.work_dir, "final_refined_book.pdf")
        final_book_tex_path = os.path.join(self.work_dir, "final_refined_book.tex")

        with open(final_book_tex_path, "w", encoding="utf-8") as f:
            f.write(self.current_tex)

        if os.path.exists(final_valid_pdf_path):
            shutil.copy(final_valid_pdf_path, final_book_pdf_path)
        else:
            compile_latex(self.work_dir, final_book_pdf_path, "final_refined_book")

        print(
            f" >> Process Complete. Final Score: {self.current_score}, Final PDF at: {final_book_pdf_path}"
        )
        return final_book_pdf_path


# Book-oriented alias
EditorialRefinementAgent = ContentRefinementAgent
