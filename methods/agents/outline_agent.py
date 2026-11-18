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

import json
from utils.common_utils import load_md_file
from utils.gemini_utils import call_gemini_with_contents, types

from methods.prompts.outline_agent import outline_agent_system_prompt


class OutlineAgent:
    def __init__(
        self,
        model_name: str = "gemini-3.1-pro-preview",
        cutoff_date: str = "2024-10",
        target_audience: str = "Senior Software & Systems Engineers",
        book_style: str = "Practical Architecture & Production Engineering",
    ):
        self.model_name = model_name
        self.cutoff_date = cutoff_date
        self.target_audience = target_audience
        self.book_style = book_style

    @staticmethod
    def _normalize_book_blueprint(json_result: dict) -> dict:
        """Ensures both book-native keys and backward-compatible keys exist."""
        if not isinstance(json_result, dict):
            return {}

        if "section_plan" not in json_result and "chapter_plan" in json_result:
            json_result["section_plan"] = json_result["chapter_plan"]
        if "chapter_plan" not in json_result and "section_plan" in json_result:
            json_result["chapter_plan"] = json_result["section_plan"]

        if (
            "intro_related_work_plan" not in json_result
            and "reference_research_plan" in json_result
        ):
            json_result["intro_related_work_plan"] = json_result[
                "reference_research_plan"
            ]
        if (
            "reference_research_plan" not in json_result
            and "intro_related_work_plan" in json_result
        ):
            json_result["reference_research_plan"] = json_result[
                "intro_related_work_plan"
            ]

        return json_result

    def run(
        self,
        idea_file: str,
        experimental_log_file: str,
        latex_template_file: str,
        conference_guidelines_file: str,
        output_filepath: str = None,
        target_audience: str = None,
        book_style: str = None,
    ):
        audience = target_audience or self.target_audience
        style = book_style or self.book_style

        idea_text = load_md_file(idea_file) or ""
        notes_text = load_md_file(experimental_log_file) or ""
        template_text = load_md_file(latex_template_file) or ""
        guidelines_text = load_md_file(conference_guidelines_file) or ""

        contents = [
            types.Part.from_text(
                text=outline_agent_system_prompt.format(cutoff_date=self.cutoff_date)
            ),
            types.Part.from_text(
                text=(
                    f"Publishing Profile:\n"
                    f"- Target Audience: {audience}\n"
                    f"- Book Style: {style}\n"
                )
            ),
            types.Part.from_text(
                text="'book_proposal.md' (idea.md):\n" + idea_text
            ),
            types.Part.from_text(
                text="'technical_notes.md' (experimental_log.md):\n" + notes_text
            ),
            types.Part.from_text(text="'template.tex':\n" + template_text),
            types.Part.from_text(text="'guidelines.md':\n" + guidelines_text),
        ]
        response_dict = call_gemini_with_contents(
            contents=contents,
            model_name=self.model_name,
        )
        json_result = self._normalize_book_blueprint(response_dict["parsed_response"])
        if output_filepath is not None:
            with open(output_filepath, "w", encoding="utf-8") as f:
                json.dump(json_result, f, indent=4, ensure_ascii=False)
                print(f"outline.json output written to {output_filepath}")
        return json_result


# Book-oriented alias
BookBlueprintAgent = OutlineAgent
