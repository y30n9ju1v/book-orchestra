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
import os
import re
import datetime
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Dict

try:
    from pydantic import BaseModel
except ImportError:

    class BaseModel:
        def __init__(self, **kwargs):
            annotations = {}
            for cls in reversed(self.__class__.__mro__):
                annotations.update(getattr(cls, "__annotations__", {}))
            for field_name in annotations:
                if field_name in kwargs:
                    setattr(self, field_name, kwargs[field_name])
                elif hasattr(self.__class__, field_name):
                    setattr(self, field_name, getattr(self.__class__, field_name))
                else:
                    setattr(self, field_name, None)
            for k, v in kwargs.items():
                setattr(self, k, v)

        def model_dump(self) -> Dict:
            annotations = {}
            for cls in reversed(self.__class__.__mro__):
                annotations.update(getattr(cls, "__annotations__", {}))
            return {k: getattr(self, k, None) for k in annotations}

        @classmethod
        def model_json_schema(cls) -> Dict:
            annotations = getattr(cls, "__annotations__", {})
            return {
                "title": cls.__name__,
                "type": "object",
                "properties": {k: {"type": "string"} for k in annotations},
            }


from methods.prompts.literature_review_agent import (
    literature_review_agent_writter_prompt,
)
from utils.gemini_utils import (
    call_gemini_with_contents,
    parse_gemini_latex_results,
    types,
)
from utils.reference_verifier import (
    is_reference_before_cutoff,
    has_verifiable_identifier_or_grounding,
    format_bibtex_entry,
    audit_and_clean_citations,
)


class CandidatePaper(BaseModel):
    title: str
    year: int
    reason: str
    authors: Optional[List[str]] = None
    venue: Optional[str] = None
    summary: Optional[str] = None
    source_type: Optional[str] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    rfc_number: Optional[str] = None
    isbn: Optional[str] = None
    url: Optional[str] = None
    publication_date: Optional[str] = None


class DiscoveryResult(BaseModel):
    section_name: str
    candidates: List[CandidatePaper]


class PaperData(BaseModel):
    citation_key: str
    title: str
    authors: List[str]
    venue: str
    year: int
    abstract: str
    citation_count: int
    found_in_section: str
    reason: str
    source_type: Optional[str] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    rfc_number: Optional[str] = None
    isbn: Optional[str] = None
    url: Optional[str] = None
    verification_source: Optional[str] = None
    journal: Optional[str] = None
    volume: Optional[str] = None
    pages: Optional[str] = None
    publication_date: Optional[str] = None


class WriterOutput(BaseModel):
    sections_written: List[str]
    latex_code: str


class HybridLiteratureAgent:
    def __init__(
        self,
        idea_path: str,
        experimental_log_path: str,
        latex_template_path: str,
        conference_guidelines_path: str,
        output_dir: str,
        model_name: str = "gemini-3.1-pro-preview",
        max_workers: int = 5,
    ):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        self.model_name = model_name
        self.max_workers = max_workers
        self.context_paths = {
            "idea.md": idea_path,
            "experimental_log.md": experimental_log_path,
            "template.tex": latex_template_path,
            "conference_guidelines.md": conference_guidelines_path,
        }
        self.context_files = self._load_context_files()
        self.google_search_tool = types.Tool(google_search=types.GoogleSearch())

        self.registry_lock = threading.Lock()
        self.print_lock = threading.Lock()

    def _safe_print(self, message: str):
        """Thread-safe printing to prevent garbled output."""
        with self.print_lock:
            print(message)

    def run(
        self,
        outline_path: str,
        cutoff_date: Optional[str] = None,
    ):
        if not cutoff_date:
            cutoff_date = datetime.datetime.now().strftime("%Y-%m")

        self._safe_print(
            f"🚀 Starting Technical Ecosystem & Literature Agent (Cutoff: {cutoff_date}, Threads: {self.max_workers})..."
        )

        with open(outline_path, "r", encoding="utf-8") as f:
            outline = json.load(f)

        outline_output_path = f"{self.output_dir}/outline_v1.json"
        unique_papers_registry: Dict[str, PaperData] = {}
        processed_tasks = set()

        # --- PHASE 1: DISCOVERY & RETRIEVAL ---
        self._safe_print(f"📚 Phase 1: Analyzing Blueprint & Executing Reference Search Tasks...")

        search_tasks = self._collect_search_tasks(outline)
        self._safe_print(
            f"    -> Identified {len(search_tasks)} distinct search tasks."
        )

        self._execute_tasks_in_parallel(
            search_tasks, outline, unique_papers_registry, processed_tasks, cutoff_date
        )

        self._safe_print(
            f"\n✨ Final Count: {len(unique_papers_registry)} unique technical references retrieved."
        )

        # --- PHASE 2: ASSET GENERATION ---
        final_papers_list = list(unique_papers_registry.values())
        bibtex_str = self._generate_bibtex(final_papers_list)
        citation_map = self._generate_citation_map(final_papers_list)

        self._safe_print("🔄 Injecting citations back into Book Blueprint...")
        updated_outline = self._inject_citations_into_outline(
            outline, unique_papers_registry
        )

        # --- PHASE 3: WRITING ---
        self._safe_print("✍️  Phase 3: Writing Preface & Foundations/Ecosystem Chapter...")
        latex_content = self._synthesize_content(
            updated_outline, final_papers_list, cutoff_date
        )

        # --- PHASE 4: SAVING ---
        self._save_outputs(
            latex_content,
            bibtex_str,
            citation_map,
            updated_outline,
            outline_output_path,
        )
        self._safe_print(
            f"✅ Done! Technical Reference & Foundations Outputs saved to {self.output_dir}/"
        )

    def _execute_tasks_in_parallel(
        self, tasks, outline, registry, processed_tasks_set, cutoff_date
    ):
        """Run a batch of search tasks concurrently."""
        active_tasks = []
        for task in tasks:
            task_id = f"{task['section']}:{task['focus']}"
            if task_id in processed_tasks_set:
                continue
            active_tasks.append(task)
            processed_tasks_set.add(task_id)

        if not active_tasks:
            return

        self._safe_print(f"   🔎 Phase 1a: Reference Candidates Discovery (MaxPool=10)...")
        all_candidates_with_tasks = []

        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_task = {
                executor.submit(
                    self._process_search_task_google, task, outline, cutoff_date
                ): task
                for task in active_tasks
            }
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    cands = future.result()
                    if cands:
                        for cand_item in cands:
                            all_candidates_with_tasks.append(
                                {"cand": cand_item, "task": task}
                            )
                except Exception as exc:
                    self._safe_print(
                        f"      ❌ Reference Search failed for '{task['focus'][:20]}...': {exc}"
                    )

        self._safe_print(
            f"   📊 Phase 1b: Semantic Scholar & Metadata Enrichment..."
        )

        for item in all_candidates_with_tasks:
            time.sleep(0.5)
            self._enrich_and_register_paper(
                item["cand"], item["task"], registry, cutoff_date
            )

    def _collect_search_tasks(self, outline: Dict) -> List[Dict]:
        tasks = []
        plan = outline.get("intro_related_work_plan") or outline.get(
            "reference_research_plan", {}
        )

        # 1. Foundations / Introduction Macro-Tasks
        intro_strat = plan.get("introduction_strategy", {})
        for direction in intro_strat.get("search_directions", []):
            tasks.append(
                {
                    "section": "Introduction",
                    "focus": direction,
                    "context": f"Hook: {intro_strat.get('hook_hypothesis', '')}. Gap: {intro_strat.get('problem_gap_hypothesis', '')}",
                    "search_type": "exploration",
                }
            )

        # 2. Ecosystem & Comparative Architecture Micro-Tasks
        rw_strat = plan.get("related_work_strategy", {})
        for sub in rw_strat.get("subsections", []):
            queries = sub.get("limitation_search_queries", [])
            if not queries:
                queries = [
                    f"{sub.get('subsection_title', '')} {sub.get('methodology_cluster', '')}"
                ]

            for q in queries:
                tasks.append(
                    {
                        "section": f"Related Work: {sub.get('subsection_title', 'General')}",
                        "focus": q,
                        "context": f"Mission: {sub.get('sota_investigation_mission', '')}. Hypothesis: {sub.get('limitation_hypothesis', '')}",
                        "search_type": "exploration",
                    }
                )

        # 3. Targeted Tasks (Must-Haves from Chapter/Section Plan)
        blueprint = outline.get("section_plan") or outline.get("chapter_plan", [])
        for section in blueprint:
            section_title = section.get("section_title", "Unknown Chapter")
            for subsection in section.get("subsections", []):
                sub_title = subsection.get("subsection_title", "General")
                bullets = subsection.get("content_bullets", [])
                context_str = " ".join(bullets)

                hints = subsection.get("citation_hints", [])
                for hint in hints:
                    tasks.append(
                        {
                            "section": f"{section_title} - {sub_title}",
                            "focus": hint,
                            "context": f"Must-have reference for section covering: {context_str}",
                            "search_type": "targeted",
                        }
                    )

        return tasks

    def _inject_citations_into_outline(
        self, outline: Dict, registry: Dict[str, PaperData]
    ) -> Dict:
        section_to_keys = {}
        for p in registry.values():
            sec = p.found_in_section
            if sec not in section_to_keys:
                section_to_keys[sec] = []
            section_to_keys[sec].append(p.citation_key)

        plan = outline.get("intro_related_work_plan") or outline.get(
            "reference_research_plan", {}
        )

        # Introduction / Foundations
        intro_strat = plan.get("introduction_strategy", {})
        intro_keys = section_to_keys.get("Introduction", [])
        intro_strat["citation_candidates"] = list(set(intro_keys))

        # Ecosystem & Related Work
        rw_strat = plan.get("related_work_strategy", {})
        for sub in rw_strat.get("subsections", []):
            key = f"Related Work: {sub.get('subsection_title', 'General')}"
            sub["citation_candidates"] = section_to_keys.get(key, [])

        # Remaining Chapters / Sections
        for plan_key in ("section_plan", "chapter_plan"):
            blueprint = outline.get(plan_key, [])
            for section in blueprint:
                section_title = section.get("section_title", "Unknown Chapter")
                for sub in section.get("subsections", []):
                    sub_title = sub.get("subsection_title", "General")
                    key = f"{section_title} - {sub_title}"
                    sub["citation_candidates"] = section_to_keys.get(key, [])
                    if "citation_hints" in sub:
                        del sub["citation_hints"]

        return outline

    def _process_search_task_google(self, task, outline, cutoff_date) -> list:
        if task.get("search_type") == "targeted":
            self._safe_print(
                f"   🎯 Targeted Search [{task['section']}]: '{task['focus']}'"
            )
        else:
            self._safe_print(
                f"   🔎 Exploration [{task['section']}]: '{task['focus']}'"
            )

        retry_count = 0
        while retry_count < 3:
            try:
                candidates = self._discover_candidates(task, outline, cutoff_date)
                if candidates:
                    return candidates
                retry_count += 1
            except Exception:
                retry_count += 1
        return []

    def _enrich_and_register_paper(self, cand, task, registry, cutoff_date):
        cand_dict = cand.model_dump() if hasattr(cand, "model_dump") else (dict(cand) if isinstance(cand, dict) else {})
        cand_title = cand_dict.get("title")
        cand_year = cand_dict.get("year")
        cand_reason = cand_dict.get("reason", "")
        cand_authors = cand_dict.get("authors")
        cand_venue = cand_dict.get("venue")
        cand_summary = cand_dict.get("summary")

        if not cand_title:
            return

        if not is_reference_before_cutoff(cand_dict, cutoff_date):
            self._safe_print(
                f"      -> ⏭️ Skipped reference after cutoff ({cutoff_date}): {cand_title} ({cand_year})"
            )
            return

        grounding_blob = "\n".join(
            [
                self.context_files.get("idea.md", ""),
                self.context_files.get("experimental_log.md", ""),
                str(task.get("focus", "")),
                str(task.get("context", "")),
            ]
        )
        is_verified, ver_src = has_verifiable_identifier_or_grounding(
            cand_dict, grounding_text=grounding_blob
        )
        if not is_verified:
            self._safe_print(
                f"      -> ⏭️ Rejected unverified reference (missing DOI/URL/RFC/ISBN or source grounding): {cand_title}"
            )
            return

        norm_title = self._normalize_title(cand_title)

        with self.registry_lock:
            if norm_title in registry:
                return

        try:
            year_val = int(cand_year)
            authors_list = cand_authors if cand_authors else ["Technical Authors"]
            abstract_text = cand_summary or cand_reason or cand_title
            full_obj = PaperData(
                citation_key=self._generate_key(
                    authors_list, year_val, cand_title
                ),
                title=cand_title,
                authors=authors_list,
                venue=cand_venue or "Technical Report / Systems Literature",
                year=year_val,
                abstract=abstract_text,
                citation_count=0,
                found_in_section=task["section"],
                reason=cand_reason,
                source_type=cand_dict.get("source_type"),
                doi=cand_dict.get("doi"),
                arxiv_id=cand_dict.get("arxiv_id"),
                rfc_number=cand_dict.get("rfc_number"),
                isbn=cand_dict.get("isbn"),
                url=cand_dict.get("url"),
                verification_source=ver_src,
                publication_date=cand_dict.get("publication_date"),
            )

            final_norm_title = self._normalize_title(full_obj.title)

            with self.registry_lock:
                if final_norm_title not in registry:
                    registry[final_norm_title] = full_obj
                    self._safe_print(
                        f"      -> 📚 Added verified reference ({ver_src}): {full_obj.title}"
                    )
        except Exception:
            pass

    def _is_paper_allowed(self, s2_data: Dict, cutoff_date_str: str) -> bool:
        if not cutoff_date_str:
            return True

        c_parts = [int(x) for x in cutoff_date_str.split("-")]
        c_y = c_parts[0]
        c_m = c_parts[1] if len(c_parts) > 1 else 12
        c_d = c_parts[2] if len(c_parts) > 2 else 1

        p_y, p_m, p_d = None, None, None
        if s2_data.get("publicationDate"):
            try:
                p_parts = [int(x) for x in str(s2_data["publicationDate"]).split("-")]
                p_y = p_parts[0]
                p_m = p_parts[1] if len(p_parts) > 1 else None
                p_d = p_parts[2] if len(p_parts) > 2 else None
            except Exception:
                pass

        if p_y is None and s2_data.get("year"):
            p_y = int(s2_data["year"])

        if p_y is None:
            return True
        if p_y > c_y:
            return False
        if p_y < c_y:
            return True
        if p_m is None:
            return True
        if p_m > c_m:
            return False
        if p_m < c_m:
            return True
        if p_d is None:
            return True
        if p_d > c_d:
            return False

        return True

    def _discover_candidates(self, task, outline, cutoff_date) -> List[CandidatePaper]:
        intro_plan = (
            outline.get("intro_related_work_plan")
            or outline.get("reference_research_plan", {})
        ).get("introduction_strategy", {})
        core_problem = intro_plan.get("problem_gap_hypothesis", "N/A")

        if task.get("search_type") == "targeted":
            prompt = f"""
            Identify the SPECIFIC foundational paper, systems publication, RFC, or official technical report described by this hint: "{task['focus']}".
            
            CONTEXT: {task['context']}
            CUTOFF DATE: Published BEFORE {cutoff_date}
            
            INSTRUCTIONS:
            1. Use Google Search to find the EXACT title, authors, venue/publisher, and year of this reference.
            2. If the hint refers to an open-source system, framework, protocol, or benchmark, find the canonical paper, technical report, or specification introducing it.
            3. Return EXACTLY 1 candidate reference that matches best.

            YOU MUST return the results in json format (```json content```) following this schema:
            {DiscoveryResult.model_json_schema()}
            """
        else:
            prompt = f"""
            Find 8-12 authoritative systems/engineering papers, technical reports, classic books, or standards matching the search task. Only return strictly relevant references.
            
            SEARCH TASK: {task['focus']}
            CONTEXT FOR TASK: {task['context']}
            BROADER ENGINEERING PROBLEM: {core_problem}
            CUTOFF DATE: Published BEFORE {cutoff_date}
            
            INSTRUCTIONS:
            1. Use Google Search to find REAL systems/engineering references (OSDI, SOSP, NSDI, SIGMOD, VLDB, NeurIPS, ICML, ICLR, RFCs, O'Reilly/Addison-Wesley books, official engineering whitepapers).
            2. Prioritize foundational, high-impact, and production-relevant works.

            YOU MUST return the results in json format (```json content```) following this schema:
            {DiscoveryResult.model_json_schema()}
            """

        response_dict = call_gemini_with_contents(
            model_name="gemini-3-flash-preview",
            contents=[prompt],
            generation_configs={
                "tools": [self.google_search_tool],
                "temperature": 0.1 if task.get("search_type") == "targeted" else 0.4,
            },
        )

        if response_dict["parsed_response"]:
            return response_dict["parsed_response"].get("candidates", [])
        return []

    def _synthesize_content(self, outline, papers: List[PaperData], cutoff_date: str):
        paper_keys = [p.citation_key for p in papers]
        papers_payload = [p.model_dump() for p in papers]

        context_payload = {
            "template.tex": self.context_files.get("template.tex", ""),
            "book_metadata": outline.get("book_metadata", {}),
            "intro_related_work_plan": outline.get("intro_related_work_plan", {}),
            "project_idea": self.context_files.get("idea.md", ""),
            "project_experimental_log": self.context_files.get(
                "experimental_log.md", ""
            ),
            "citation_checklist": paper_keys,
            "collected_papers": papers_payload,
        }

        writer_system_prompt = literature_review_agent_writter_prompt.format(
            paper_count=len(papers),
            min_cite_paper_count=max(1, int(len(papers) * 0.8)) if papers else 0,
            cutoff_date=cutoff_date,
        )

        user_prompt = (
            "Generate the LaTeX for the Preface/Introduction and Foundations/Ecosystem Chapter "
            "in template.tex while keeping all downstream chapters untouched."
        )
        response_dict = call_gemini_with_contents(
            model_name=self.model_name,
            contents=[user_prompt, json.dumps(context_payload)],
            generation_configs={"system_instruction": writer_system_prompt},
            result_parsing_func=parse_gemini_latex_results,
        )
        cleaned_latex, _ = audit_and_clean_citations(
            response_dict["parsed_response"], set(paper_keys)
        )
        return cleaned_latex

    def _generate_key(self, authors_raw, year, title):
        if not authors_raw:
            first_author = "Unknown"
        elif isinstance(authors_raw[0], dict):
            first_author = authors_raw[0]["name"].split()[-1]
        else:
            first_author = str(authors_raw[0]).split()[-1]

        clean_author = re.sub(r"[^a-zA-Z]", "", first_author).capitalize() or "Ref"
        year_str = str(year) if year else "2024"
        clean_title_str = re.sub(r"[^a-zA-Z0-9\s]", "", title.lower())
        words = clean_title_str.split()
        stopwords = {
            "the",
            "a",
            "an",
            "in",
            "on",
            "at",
            "for",
            "to",
            "of",
            "and",
            "is",
            "are",
            "with",
            "by",
            "study",
        }
        meaningful = [w.capitalize() for w in words if w not in stopwords]
        title_part = "".join(meaningful[:2]) if meaningful else "Work"
        return f"{clean_author}{year_str}{title_part}"

    def _generate_bibtex(self, papers: List[PaperData]) -> str:
        entries = []
        seen_keys = set()
        for p in papers:
            base_key = p.citation_key
            unique_key = base_key
            suffix = "a"
            while unique_key in seen_keys:
                unique_key = f"{base_key}{suffix}"
                suffix = chr(ord(suffix) + 1)
            p.citation_key = unique_key
            seen_keys.add(unique_key)
            entries.append(format_bibtex_entry(p.model_dump()))
        return "\n\n".join(entries)

    def _generate_citation_map(self, papers: List[PaperData]) -> Dict[str, Dict]:
        return {
            p.citation_key: {
                "citation_key": p.citation_key,
                "title": p.title,
                "authors": p.authors,
                "venue": p.venue,
                "year": p.year,
                "abstract": p.abstract.strip(),
                "source_type": p.source_type,
                "doi": p.doi,
                "arxiv_id": p.arxiv_id,
                "rfc_number": p.rfc_number,
                "isbn": p.isbn,
                "url": p.url,
                "verification_source": p.verification_source,
            }
            for p in papers
            if p.abstract
        }

    def _save_outputs(self, latex, bibtex, citation_map, outline, outline_path):
        with open(
            f"{self.output_dir}/updated_template.tex", "w", encoding="utf-8"
        ) as f:
            f.write(latex)
        with open(f"{self.output_dir}/references.bib", "w", encoding="utf-8") as f:
            f.write(bibtex)
        with open(f"{self.output_dir}/citation_map.json", "w", encoding="utf-8") as f:
            json.dump(citation_map, f, indent=4, ensure_ascii=False)
        with open(outline_path, "w", encoding="utf-8") as f:
            json.dump(outline, f, indent=4, ensure_ascii=False)

    def _load_context_files(self) -> Dict[str, str]:
        loaded = {}
        for name, path in self.context_paths.items():
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    loaded[name] = f.read()
            else:
                loaded[name] = ""
        return loaded

    def _normalize_title(self, title):
        return re.sub(r"[^a-z0-9]", "", title.lower())


# Book-oriented alias
TechnicalResearchAgent = HybridLiteratureAgent
