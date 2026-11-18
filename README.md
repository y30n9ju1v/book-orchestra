# <div align="center">📚 BookOrchestra: Antigravity-Native Multi-Agent Orchestration for IT & Engineering Book Publishing</div>

**BookOrchestra** transforms the PaperOrchestra multi-agent academic writing pipeline into an **Antigravity-native IT, Software Engineering, Systems, and AI Engineering Book Authoring & Publishing Framework** designed around the perspectives of:

1. **Senior / Principal Software & Systems Engineers**: Enforces mental models, capacity math, production-grade code listings (`lstlisting` with error handling, backpressure, and timeouts), honest architectural trade-offs (latency vs. throughput, consistency vs. availability, cost vs. reliability), and real outage post-mortem lessons.
2. **Professional Technical Publishers (O'Reilly, Manning, Addison-Wesley, Pragmatic Bookshelf)**: Enforces target-audience calibration, progressive chapter scaffolding, publisher callout boxes (`objectivesbox`, `tipbox`, `warningbox`, `pitfallbox`, `notebox`, `architecturebox`, `exercisesbox`), terminology consistency across chapters via a Continuity Ledger, modular LaTeX chapter splits, Markdown manuscript export, and a **3-Persona Editorial & Technical Review Board**.

---

## 한국어 개요 (Antigravity Native `run_workflow` 아키텍처)

외부 `google-genai` / `openai` SDK 및 `.env` API 키(`GEMINI_API_KEY`, `VERTEX_AI_PROJECT`, `OPENAI_API_KEY`) 호출을 제거하고, **Antigravity 내장 `run_workflow` 엔진**(`phase`, `agent`, `parallel`, `pipeline`)으로 직접 동작하도록 개편했습니다.

### 핵심 특징
1. **외부 API 키 불필요 — Antigravity `run_workflow` 네이티브 실행**
   - [`workflows/book_orchestra_workflow.py`](workflows/book_orchestra_workflow.py) 및 [`methods/agy_workflow_runner.py`](methods/agy_workflow_runner.py): Antigravity의 `agent()`, `parallel()`, `pipeline()`, `phase()` 프리미티브를 사용해 현재 로그인된 Antigravity 세션 모델과 내장 도구(`research` 웹 검색, `image-generator` 다이어그램 생성)만으로 전체 집필·검수·조판 파이프라인을 실행합니다.
   - 워크스페이스 스킬 [`.agents/skills/book-orchestra/SKILL.md`](.agents/skills/book-orchestra/SKILL.md)가 등록되어 있어, Antigravity 채팅창에서 바로 기술 서적 집필을 요청하면 자동으로 `run_workflow`를 호출합니다.
   - [`utils/agent_runtime_utils.py`](utils/agent_runtime_utils.py): 외부 REST API 클라이언트(`google.genai`, `openai`)를 제거하고 Antigravity 에이전트 브리지(`set_antigravity_agent_runner`, `enable_agy_cli_runner`)로 대체했습니다.
2. **이중 블라인드 논문 제약 제거 → 엔지니어링 & 출판 무결성(`TECHNICAL_BOOK_INTEGRITY_PROMPT`) 적용**
   - [`utils/prompt_utils.py`](utils/prompt_utils.py): 익명성·8페이지 학회 제한 대신 **벤치마크/성능 수치의 원천 데이터 근거(Grounding)**, **실행 가능하고 에러 처리가 포함된 프로덕션 코드 작성**, **과장 없는 트레이드오프 및 장애 모드 분석**을 강제합니다.
3. **전문 출판사 기반 Book Templates (`templates/`)**
   - [`templates/oreilly_style_book/`](templates/oreilly_style_book/template.tex): O'Reilly 스타일 실무 아키텍처 & 엔지니어링 가이드 (`\part`, `\chapter`, 코드 하이라이팅, 학습 목표·팁·경고·함정·아키텍처 트레이드오프·연습문제 박스 완비)
   - [`templates/manning_in_action_book/`](templates/manning_in_action_book/template.tex): Manning "In Action" 스타일 프로젝트·코드 중심 점진적 구현 서적
   - [`templates/systems_handbook/`](templates/systems_handbook/template.tex): Addison-Wesley / DDIA / Google SRE 스타일 Staff/Principal 엔지니어용 시스템 핸드북
4. **4단계 `run_workflow` 멀티 에이전트 파이프라인**
   - **Phase 1 (`1. Book Blueprint & Architecture Plan`)**: `Book Blueprint Architect` 에이전트가 `book_metadata`, `terminology_glossary`, `plotting_plan`, `chapter_plan` 생성 (`outline.json`)
   - **Phase 2 (`2. Technical Ecosystem Research & Architecture Visuals`)**: `parallel()`로 `research` 에이전트들이 검증 식별자(DOI, arXiv ID, RFC, ISBN, URL)와 cutoff 날짜를 검증한 레퍼런스(`references.bib`, `citation_map.json`)를 병렬 수집해 Preface 및 1장 집필 + `image-generator` 및 AST/프로세스 샌드박스 격리된 `matplotlib` 플로터가 아키텍처 다이어그램/벤치마크 차트 병렬 생성
   - **Phase 3 (`3. Multi-Chapter Authoring & Continuity Ledger`)**: `book_continuity_ledger.json`으로 용어·라벨 일관성을 유지하며 전체 챕터 집필 및 모듈별 파일(`latex_writeup/chapters/chXX_*.tex`) 분할 저장
   - **Phase 4 (`4. 3-Persona Editorial Review Board & Typesetting QA`)**: `parallel()`로 **Principal Systems Engineer**, **Executive Developmental Editor**, **Production & Copy Editor** 3인 편집위원회가 동시 감수 → Editor-in-Chief 메타 리뷰 및 원고 개선 → 렌더링된 페이지 스냅샷 및 정적 레이아웃 진단 기반 조판 QA 후 `final_book.pdf`, `final_book.tex`, `references.bib`, `figures/`, `final_book.md` 생성

---

## How to Run in Antigravity (Zero External API Keys)

### 1. Directly in Antigravity Chat / `run_workflow`
Ask Antigravity in chat:
> *"Run BookOrchestra workflow on `examples/distributed_llm_systems_book/raw_materials` with the `oreilly_style_book` template."*

Or invoke `run_workflow` with:
- **`ScriptPath`**: `workflows/book_orchestra_workflow.py`
- **`Args`**:
  ```json
  {
    "raw_materials_dir": "examples/distributed_llm_systems_book/raw_materials",
    "latex_template_dir": "templates/oreilly_style_book",
    "output_dir": "./book_output_agy",
    "target_audience": "Senior Backend, Platform, and ML Systems Engineers",
    "book_style": "Practical Architecture & Production Engineering",
    "use_plotting": true,
    "max_reflections": 2
  }
  ```

### 2. Generate a `run_workflow` Payload from CLI
```bash
python3 book_writing_cli.py \
  --raw_materials_dir examples/distributed_llm_systems_book/raw_materials \
  --latex_template_dir templates/oreilly_style_book \
  --output_dir ./book_output_llm_systems \
  --emit_workflow_only
```

---

## Generated Outputs
Inside `output_dir`:
- `final_book.pdf`: Compiled book manuscript PDF (`pdflatex` if installed, or built-in pure-Python multi-page PDF fallback)
- `final_book.tex`: Complete compilable LaTeX book manuscript
- `final_book.md`: Clean Markdown book manuscript for Markdown/Pandoc/EPUB workflows
- `latex_writeup/chapters/ch01_*.tex`, `ch02_*.tex`, ...: Modular chapter-by-chapter LaTeX files
- `book_continuity_ledger.json`: Tracked glossary terms, chapter titles, code listings, and cross-reference labels
- `outline.json`: Complete Book Blueprint & TOC
- `literature_agent_output/citation_map.json` & `references.bib`: Verified references
- `content_refinement_workdir/peer_reviews/`: 3-Persona Editorial Review Board evaluations

---

## Project Structure

- `workflows/book_orchestra_workflow.py` & `workflows/workflow.json`: Primary Antigravity `run_workflow` script and argument schema
- `.agents/skills/book-orchestra/SKILL.md`: Workspace skill auto-discovered by Antigravity
- `methods/agy_workflow_runner.py`: Core async workflow orchestration runner for `run_workflow` primitives
- `methods/`: Modular agent classes (`agents/`) and publisher prompts (`prompts/`)
- `templates/`: Publisher LaTeX templates (`oreilly_style_book/`, `manning_in_action_book/`, `systems_handbook/`)
- `autoraters/`: 3-Persona Editorial Review Board (`agent_review.py`) and SxS book evaluators (`sxs_book_quality.py`)
- `examples/distributed_llm_systems_book/`: Complete IT/Engineering book proposal and technical benchmark notes
- `utils/`: Antigravity agent bridge (`agent_runtime_utils.py`), diagram/plot helpers (`diagram_plot_utils.py`), PDF/LaTeX/Markdown utilities (`pdf_utils.py`)
- `tests/`: Automated unit & end-to-end workflow tests (`test_book_orchestra.py`)
