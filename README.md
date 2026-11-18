# <div align="center">📚 BookOrchestra: Multi-Agent Orchestration for IT & Engineering Book Publishing</div>

**BookOrchestra** transforms the PaperOrchestra multi-agent academic writing pipeline into a production-oriented **IT, Software Engineering, Systems, and AI Engineering Book Authoring & Publishing Framework** designed around the perspectives of:

1. **Senior / Principal Software & Systems Engineers**: Enforces mental models, capacity math, production-grade code listings (`lstlisting` with error handling, backpressure, and timeouts), honest architectural trade-offs (latency vs. throughput, consistency vs. availability, cost vs. reliability), and real outage post-mortem lessons.
2. **Professional Technical Publishers (O'Reilly, Manning, Addison-Wesley, Pragmatic Bookshelf)**: Enforces target-audience calibration, progressive chapter scaffolding, publisher callout boxes (`objectivesbox`, `tipbox`, `warningbox`, `pitfallbox`, `notebox`, `architecturebox`, `exercisesbox`), terminology consistency across chapters via a Continuity Ledger, modular LaTeX chapter splits, Markdown manuscript export, and a **3-Persona Editorial & Technical Review Board**.

---

## 한국어 개요 (Overview for Korean Engineers & Authors)

기존 논문 작성용 `PaperOrchestra` 파이프라인을 **IT / 소프트웨어 / 시스템 / AI 엔지니어링 전문 서적 집필 및 출판 오케스트레이션**으로 전면 개편했습니다.

### 핵심 변경 사항
1. **이중 블라인드 논문 제약 제거 → 엔지니어링 & 출판 무결성(`TECHNICAL_BOOK_INTEGRITY_PROMPT`) 적용**
   - [utils/prompt_utils.py](file:///Users/yeongjun/Develops/book-orchestra/utils/prompt_utils.py): 익명성·8페이지 학회 제한 대신 **벤치마크/성능 수치의 원천 데이터 근거(Grounding)**, **실행 가능하고 에러 처리가 포함된 프로덕션 코드 작성**, **과장 없는 트레이드오프 및 장애 모드 분석**을 강제합니다.
2. **전문 출판사 기반 Book Templates (`templates/`) 추가**
   - [templates/oreilly_style_book/](file:///Users/yeongjun/Develops/book-orchestra/templates/oreilly_style_book/template.tex): O'Reilly 스타일 실무 아키텍처 & 엔지니어링 가이드 (`\part`, `\chapter`, 코드 하이라이팅, 학습 목표·팁·경고·함정·아키텍처 트레이드오프·연습문제 박스 완비)
   - [templates/manning_in_action_book/](file:///Users/yeongjun/Develops/book-orchestra/templates/manning_in_action_book/template.tex): Manning "In Action" 스타일 프로젝트·코드 중심 점진적 구현 서적
   - [templates/systems_handbook/](file:///Users/yeongjun/Develops/book-orchestra/templates/systems_handbook/template.tex): Addison-Wesley / DDIA / Google SRE 스타일 Staff/Principal 엔지니어용 시스템 핸드북
3. **5개 전문 에이전트 업그레이드 (`methods/agents/` & `methods/prompts/`)**
   - **Book Blueprint Agent (`OutlineAgent` / `BookBlueprintAgent`)**: 책 메타데이터(`book_metadata`), 용어집(`terminology_glossary`), 러닝 케이스 스터디, 챕터별 학습 목표·코드 리스팅 계획·콜아웃 박스·연습문제 청사진 생성
   - **Technical Reference & Ecosystem Agent (`HybridLiteratureAgent` / `TechnicalResearchAgent`)**: 시스템 논문(OSDI/SOSP/NSDI/NeurIPS), RFC, 공식 기술 리포트·고전 서적까지 탐색하여 Preface 및 1장(생태계 비교 & 트레이드오프) 집필
   - **Technical Illustration & Benchmark Agent (`PlottingAgent` / `TechnicalIllustrationAgent`)**: 아키텍처 토폴로지·데이터 플로우 다이어그램 및 p50/p95/p99 레이턴시·처리량 벤치마크 차트 생성
   - **Chapter Writing Agent (`SectionWritingAgent` / `ChapterWritingAgent`)**: `book_continuity_ledger.json` 기반 용어·라벨 일관성 유지, 전체 LaTeX 원고 및 챕터별 분할 파일(`latex_writeup/chapters/chXX_*.tex`), Markdown 원고(`final_book.md`) 생성
   - **3-Persona Editorial Review Board (`autoraters/agent_review.py` & `ContentRefinementAgent` / `EditorialRefinementAgent`)**:
     - Persona 1: **Principal Systems Engineer** (아키텍처 불변량, 장애 모드, 코드 견고성 검증)
     - Persona 2: **Executive Developmental Editor** (독자 난이도 곡선, 학습 목표, 실무 Hook 검증)
     - Persona 3: **Production & Copy Editor** (용어 통일성, 코드 마진 줄바꿈, 표·그림 레이아웃 검증)
4. **로컬 환경 친화적 PDF/Markdown 폴백 (`utils/pdf_utils.py`)**
   - `pdflatex`가 설치되어 있으면 정식 LaTeX + BibTeX 빌드를 수행하고, 설치되어 있지 않은 환경에서도 `final_book.md`와 함께 미리보기용 다중 페이지 `final_book.pdf`를 생성하여 파이프라인과 UI가 끊기지 않습니다.

---

## Key Features & Agent Architecture

| Pipeline Stage | Agent / Module | Senior Engineer & Publisher Responsibilities |
| :--- | :--- | :--- |
| **1. Book Blueprint & TOC** | `OutlineAgent` (`BookBlueprintAgent`) | Defines target persona, prerequisites, glossary, running case study, diagram specs, and chapter-by-chapter pedagogical plans. |
| **2. Ecosystem & Reference Research** | `HybridLiteratureAgent` (`TechnicalResearchAgent`) | Discovers systems papers, RFCs, technical reports, and books; writes Preface & Chapter 1 ecosystem trade-off synthesis. |
| **3. Diagrams & Benchmark Plots** | `PlottingAgent` (`TechnicalIllustrationAgent`) | Generates print-ready white-background system diagrams (control/data plane, sequence flows) and unit-labeled latency/throughput plots. |
| **4. Multi-Chapter Authoring** | `SectionWritingAgent` (`ChapterWritingAgent`) | Writes full chapters with `objectivesbox`, code listings (`lstlisting`), `booktabs` trade-off matrices, callout boxes, `exercisesbox`, and Continuity Ledger + modular `chapters/ch*.tex`. |
| **5. Editorial Review Board & Typesetting QA** | `ContentRefinementAgent` (`EditorialRefinementAgent`) & `autoraters/agent_review.py` | Evaluates drafts with a 3-Persona Editorial Board (`Technical_Depth`, `Practical_Utility`, `Code_and_Architecture_Quality`, `Pedagogical_Clarity`, `Structural_Coherence`, `Editorial_Polish`), refines prose/code, and checks typesetting. |

---

## Installation

1. **Python Environment** (Python 3.11+ recommended):
   ```bash
   pip install -r requirements.txt
   ```
   *(Optional for full native TeX typesetting: install TeX Live / MacTeX with `pdflatex` and `bibtex`. Without `pdflatex`, BookOrchestra outputs full LaTeX `.tex` + modular `chapters/`, clean `final_book.md`, and a built-in fallback preview `final_book.pdf`.)*

2. **Environment Variables**: Create `.env` in the repository root:
   ```env
   GEMINI_API_KEY=your_gemini_api_key
   # Or Vertex AI:
   # VERTEX_AI_PROJECT=your_vertex_ai_project
   # VERTEX_AI_LOCATION=your_vertex_ai_location
   # Optional:
   # OPENAI_API_KEY=your_openai_api_key
   # SEMANTIC_SCHOLAR_API_KEY=your_semantic_scholar_api_key
   ```

---

## Usage

### 1. Book Writing CLI (`book_writing_cli.py`)
Run the included IT/Engineering example (*Engineering Production LLM & Multi-Agent Systems*):
```bash
python3 book_writing_cli.py \
  --raw_materials_dir examples/distributed_llm_systems_book/raw_materials \
  --latex_template_dir templates/oreilly_style_book \
  --output_dir ./book_output_llm_systems \
  --target_audience "Senior Backend, Platform, and ML Systems Engineers" \
  --book_style "Practical Architecture & Production Engineering" \
  --use_plotting true
```
Or launch via the convenience shell script:
```bash
./book_writing_cli.sh
```
*(Backward compatibility: `paper_writing_cli.py` and `methods/paper_writer*.py` continue to work and delegate cleanly to the upgraded book pipeline.)*

### 2. Interactive Publishing Studio (`frontend/app.py`)
```bash
streamlit run frontend/app.py
```
Click **"Load IT/Engineering Book Example"** in the sidebar to inspect or run a complete distributed systems book pipeline.

### 3. Generated Outputs
Inside `--output_dir`:
- `final_book.pdf` (and backward-compatible `final_paper.pdf`): Compiled book manuscript PDF
- `final_book.tex`: Complete compilable LaTeX book manuscript
- `final_book.md`: Clean Markdown book manuscript for Markdown/Pandoc/EPUB workflows
- `latex_writeup/chapters/ch01_*.tex`, `ch02_*.tex`, ...: Modular chapter-by-chapter LaTeX files
- `book_continuity_ledger.json`: Tracked glossary terms, chapter titles, code listings, and cross-reference labels
- `outline.json`: Complete Book Blueprint & TOC
- `literature_agent_output/citation_map.json` & `references.bib`: Verified references
- `content_refinement_workdir/peer_reviews/`: 3-Persona Editorial Review Board evaluations

---

## Project Structure

- `book_writing_cli.py` / `book_writing_cli.sh`: Primary CLI for technical book generation (with `paper_writing_cli.py` compatibility wrapper)
- `methods/`: Core orchestration (`book_writer.py`, `book_writer_with_plotting.py`, `agents/`, `prompts/`)
- `templates/`: Publisher LaTeX templates (`oreilly_style_book/`, `manning_in_action_book/`, `systems_handbook/`, plus legacy `cvpr2025/`, `iclr2025/`)
- `autoraters/`: 3-Persona Editorial Review Board (`agent_review.py`), Reference Quality & SxS Book Evaluators
- `examples/distributed_llm_systems_book/`: Complete IT/Engineering book proposal and technical benchmark notes
- `frontend/`: Streamlit Publishing Studio UI (`app.py`)
- `utils/`: LLM backends, PDF/LaTeX/Markdown utilities, diagram/plot generation, reference search
- `tests/`: Automated unit & end-to-end mocked pipeline tests
