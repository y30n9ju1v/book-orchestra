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

"""
Chapter & Section Writing Agent for BookOrchestra.
Completes multi-chapter technical books in LaTeX while maintaining a Continuity Ledger
(glossary, cross-reference labels, running case study) and exporting modular chapter files.
"""

import json
import os
import re
import shutil
from typing import List, Dict

from methods.prompts.section_writing_agent import section_writing_agent_prompt
from utils.agent_runtime_utils import parse_agent_latex_response
from utils.llm_backend_utils import call_llm_with_images
from utils.reference_verifier import audit_and_clean_citations


class SectionWritingAgent:
    def __init__(
        self,
        outline_path: str,
        template_path: str,
        idea_path: str,
        experimental_log_path: str,
        citation_map_path: str,
        figures_info_path: str,
        guidelines_path: str,
        model_name: str = "inherit",
    ):
        self.model_name = model_name
        self.system_prompt = section_writing_agent_prompt

        self.outline_path = outline_path
        self.template_path = template_path
        self.idea_path = idea_path
        self.experimental_log_path = experimental_log_path
        self.citation_map_path = citation_map_path
        self.figures_info_path = figures_info_path
        self.guidelines_path = guidelines_path
        self.continuity_ledger: Dict = {}

    def _read_file(self, file_path: str) -> str:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return f"[Error: File {file_path} not found]"

    def _read_json(self, file_path: str) -> Dict:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _format_figures_context(self, figures_info: List[Dict]) -> str:
        if not figures_info:
            return "No figures provided."

        context = "### AVAILABLE ARCHITECTURE DIAGRAMS & BENCHMARK FIGURES\n"
        for idx, fig in enumerate(figures_info, 1):
            name = fig.get("name", f"figure_{idx}.jpg")
            if (
                name
                and not name.endswith(".png")
                and not name.endswith(".jpg")
                and not name.endswith(".pdf")
            ):
                name += ".png"
            caption = fig.get("caption", "No caption provided.")

            context += f"Item {idx}:\n"
            context += f"  - Filename: {name}\n"
            context += f"  - Caption: {caption}\n"
            context += "--------------------------------------------------\n"
        return context

    def _format_citation_library(self, citation_map: Dict) -> str:
        if not citation_map:
            return "No citation data provided."

        context = "### VERIFIED REFERENCE LIBRARY (Use these keys for \\cite{})\n"
        for key, data in citation_map.items():
            title = data.get("title", "Unknown Title")
            authors_list = data.get("authors", [])
            authors = ", ".join(authors_list[:3])
            if len(authors_list) > 3:
                authors += " et al."
            year = data.get("year", "N/A")
            abstract = data.get("abstract", "No abstract available.")

            context += f"--- Key: {key} ---\n"
            context += f"Title: {title}\n"
            context += f"Authors: {authors} ({year})\n"
            context += f"Abstract: {abstract}\n\n"
        return context

    def _build_continuity_ledger(self, outline_json: Dict, template_content: str) -> Dict:
        """Builds a continuity ledger tracking glossary terms, chapters, and labels."""
        book_meta = outline_json.get("book_metadata", {})
        existing_labels = re.findall(r"\\label\{([^}]+)\}", template_content or "")
        existing_chapters = re.findall(
            r"\\chapter\*?\{([^}]+)\}", template_content or ""
        )
        existing_sections = re.findall(
            r"\\section\*?\{([^}]+)\}", template_content or ""
        )

        ledger = {
            "book_title": book_meta.get("title", ""),
            "target_audience": book_meta.get(
                "target_audience", "Senior Software & Systems Engineers"
            ),
            "running_case_study": book_meta.get("running_case_study", ""),
            "terminology_glossary": book_meta.get("terminology_glossary", {}),
            "existing_chapters_or_sections": existing_chapters or existing_sections,
            "defined_labels": existing_labels,
            "planned_chapters": [
                item.get("section_title", "")
                for item in (
                    outline_json.get("chapter_plan")
                    or outline_json.get("section_plan")
                    or []
                )
            ],
        }
        return ledger

    @staticmethod
    def _slugify(title: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", title.lower()).strip("_")
        return slug[:48] or "chapter"

    def _export_modular_chapters(self, latex_text: str, output_dir: str) -> List[str]:
        """Splits a compiled book LaTeX manuscript into modular chapter files under chapters/."""
        chapters_dir = os.path.join(output_dir, "chapters")
        if os.path.exists(chapters_dir):
            shutil.rmtree(chapters_dir, ignore_errors=True)
        os.makedirs(chapters_dir, exist_ok=True)
        exported_paths = []

        # Split by \chapter{...} or \chapter*{...}
        matches = list(re.finditer(r"\\chapter\*?\{([^}]+)\}", latex_text))
        if not matches:
            # Fallback to \section{...} for single-article templates
            matches = list(re.finditer(r"\\section\*?\{([^}]+)\}", latex_text))

        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(latex_text)
            chunk = latex_text[start:end].strip()
            title = match.group(1).strip()
            slug = self._slugify(title)
            filename = f"ch{idx + 1:02d}_{slug}.tex"
            chapter_path = os.path.join(chapters_dir, filename)
            with open(chapter_path, "w", encoding="utf-8") as f:
                f.write(chunk + "\n")
            exported_paths.append(chapter_path)

        return exported_paths

    def _update_continuity_ledger_after_write(
        self, latex_text: str, exported_paths: List[str]
    ):
        all_labels = re.findall(r"\\label\{([^}]+)\}", latex_text)
        all_chapters = re.findall(r"\\chapter\*?\{([^}]+)\}", latex_text)
        all_listings = re.findall(r"caption\s*=\s*\{([^}]+)\}", latex_text)
        self.continuity_ledger["defined_labels"] = sorted(set(all_labels))
        self.continuity_ledger["authored_chapters"] = all_chapters
        self.continuity_ledger["code_listings"] = all_listings
        self.continuity_ledger["exported_chapter_files"] = [
            os.path.basename(p) for p in exported_paths
        ]

    def run(self, output_path: str) -> str:
        print(f" > Agent {self.__class__.__name__} starting...")

        try:
            outline_content = self._read_file(self.outline_path)
            outline_json = self._read_json(self.outline_path)
            template_content = self._read_file(self.template_path)
            idea_content = self._read_file(self.idea_path)
            log_content = self._read_file(self.experimental_log_path)
            guidelines_content = self._read_file(self.guidelines_path)

            citation_map = self._read_json(self.citation_map_path)
            citation_library_content = self._format_citation_library(citation_map)

            figures_info = self._read_json(self.figures_info_path)
            figures_content = self._format_figures_context(figures_info)

            self.continuity_ledger = self._build_continuity_ledger(
                outline_json, template_content
            )

            figures_dir = os.path.dirname(self.figures_info_path)
            image_paths = []
            if figures_info:
                for fig in figures_info:
                    name = fig.get("name", "")
                    if name:
                        if (
                            not name.endswith(".png")
                            and not name.endswith(".jpg")
                            and not name.endswith(".pdf")
                        ):
                            name += ".png"
                        img_path = os.path.join(figures_dir, name)
                        if os.path.exists(img_path) and not name.endswith(".pdf"):
                            image_paths.append(img_path)

        except Exception as e:
            return f"Error reading input files: {str(e)}"

        user_prompt = f"""
Here are the input materials for authoring the technical book manuscript.

--- INPUT: book_continuity_ledger.json (Terminology & Cross-Reference State) ---
{json.dumps(self.continuity_ledger, indent=2, ensure_ascii=False)}

--- INPUT: outline.json (Book Blueprint, Chapters & Citation Candidates) ---
{outline_content}

--- INPUT: citation_map.json (Verified Reference Library) ---
{citation_library_content}

--- INPUT: book_proposal.md / idea.md (Book Vision & Architecture) ---
{idea_content}

--- INPUT: technical_notes.md / experimental_log.md (Benchmarks, Code Specs & Case Studies) ---
{log_content}

--- INPUT: figures_list (Available Architecture Diagrams & Plots) ---
{figures_content}

--- INPUT: guidelines.md (Technical Publisher Editorial & Typesetting Guidelines) ---
{guidelines_content}

--- INPUT: template.tex (PRESERVE EXISTING CHAPTERS. AUTHOR MISSING CHAPTERS/SECTIONS.) ---
{template_content}

**INSTRUCTION**:
1. Identify placeholder/empty chapters or sections in `template.tex`.
2. Author comprehensive, senior-engineer-caliber content for each missing chapter following `outline.json` and `guidelines.md`:
   - Include `objectivesbox`, real-world motivation, deep architectural mechanics, annotated `lstlisting` code blocks, `booktabs` trade-off/benchmark tables, callout boxes (`tipbox`, `warningbox`, `pitfallbox`, `notebox`, `architecturebox`), and `exercisesbox`.
3. Insert figures using the exact filenames from `figures_list` (`figures/<filename>`).
4. Use verified citation keys from `citation_map.json`.
5. Keep terminology consistent with `book_continuity_ledger.json`.
6. Return the **FULL** compilable LaTeX manuscript inside ```latex ... ```.
"""

        print(" > Authoring multi-chapter book manuscript with Multimodal Diagrams...")
        response_dict = call_llm_with_images(
            prompt=user_prompt,
            images=image_paths,
            generation_configs={
                "system_instruction": self.system_prompt,
                "temperature": 0.7,
            },
            model_name=self.model_name,
            result_parsing_func=parse_agent_latex_response,
            check_parsed_response_not_none=True,
        )
        cleaned_latex = response_dict["parsed_response"]
        cleaned_latex, _ = audit_and_clean_citations(
            cleaned_latex, set(citation_map.keys()) if isinstance(citation_map, dict) else set()
        )

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(cleaned_latex)

        output_dir = os.path.dirname(os.path.abspath(output_path))
        exported_paths = self._export_modular_chapters(cleaned_latex, output_dir)
        self._update_continuity_ledger_after_write(cleaned_latex, exported_paths)

        ledger_path = os.path.join(
            os.path.dirname(self.outline_path), "book_continuity_ledger.json"
        )
        try:
            with open(ledger_path, "w", encoding="utf-8") as f:
                json.dump(self.continuity_ledger, f, indent=4, ensure_ascii=False)
        except Exception:
            pass

        print(
            f" > Book manuscript saved to {output_path} ({len(exported_paths)} chapter modules exported)"
        )
        return cleaned_latex


# Book-oriented alias
ChapterWritingAgent = SectionWritingAgent
