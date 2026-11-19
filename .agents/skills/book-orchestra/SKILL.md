---
name: book-orchestra
description: Orchestrate multi-agent IT and engineering book authoring, diagram generation, and 3-persona editorial review natively inside Antigravity using run_workflow without external model-provider API keys.
---

# BookOrchestra — Antigravity Native Technical Book Publishing Skill

Use this skill whenever the user asks to plan, research, write, illustrate, review, or typeset an **IT, Software Engineering, Systems, or AI Engineering Book** in this workspace.

## Zero External API Keys Required
BookOrchestra runs natively on Antigravity's built-in **`run_workflow`** engine via `workflows/book_orchestra_workflow.py`. Do **NOT** ask the user for a `GEMINI_API_KEY`, `VERTEX_AI_PROJECT`, `OPENAI_API_KEY`, or `SEMANTIC_SCHOLAR_API_KEY`.

## How to Run BookOrchestra via `run_workflow`

Call the `run_workflow` tool with `ScriptPath` pointing to `workflows/book_orchestra_workflow.py` in the current workspace and `Args` configured for the target book materials and publisher template:

```json
{
  "ScriptPath": "workflows/book_orchestra_workflow.py",
  "Description": "Run BookOrchestra multi-agent technical book publishing pipeline",
  "Args": {
    "raw_materials_dir": "examples/distributed_llm_systems_book/raw_materials",
    "latex_template_dir": "templates/oreilly_style_book",
    "output_dir": "./book_output_agy",
    "proposal_filename": "book_proposal.md",
    "technical_notes_filename": "technical_notes.md",
    "target_audience": "Senior Software & Systems Engineers",
    "book_style": "Practical Architecture & Production Engineering",
    "author_voice": "Warm, exploratory, reader-facing, and rich in concrete examples",
    "use_plotting": true,
    "visual_density": "high",
    "max_reflections": 2
  }
}
```

## Available Publisher Templates (`templates/`)
- `templates/oreilly_style_book`: O'Reilly-style hands-on architecture and production engineering guide (`objectivesbox`, `tipbox`, `warningbox`, `pitfallbox`, `notebox`, `architecturebox`, `exercisesbox`, `lstlisting`).
- `templates/manning_in_action_book`: Manning "In Action" project-driven progressive build template.
- `templates/systems_handbook`: Addison-Wesley / DDIA / Google SRE style deep systems engineering handbook.

## Generated Outputs in `output_dir`
- `final_book.pdf` (when compiled by XeLaTeX/LuaLaTeX/Tectonic/pdfLaTeX) or `preview_book.pdf` (when no TeX engine is installed and `strict_latex=false`): Generated book PDF
- `build_manifest.json`: Build metadata recording `render_mode` (`pdflatex`, `tectonic`, or `preview`) and `is_publication_grade`
- `final_book.tex`: Full compilable LaTeX book manuscript (with `references.bib` and `figures/` in `output_dir`)
- `final_book.md`: Clean Markdown book manuscript for Markdown/EPUB/Pandoc publishing toolchains
- `latex_writeup/chapters/ch01_*.tex`, `ch02_*.tex`, ...: Modular per-chapter LaTeX files.
- `book_continuity_ledger.json`: Tracked glossary terms, chapter titles, and cross-reference labels.
- `outline.json`: Book Blueprint & Table of Contents.
- `content_refinement_workdir/peer_reviews/`: 3-Persona Editorial Review Board JSON assessments (Principal Systems Engineer, Executive Developmental Editor, Production & Copy Editor).
