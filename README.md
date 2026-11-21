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
    "author_voice": "Warm, exploratory, reader-facing, and rich in concrete examples",
    "use_plotting": true,
    "visual_density": "high",
    "max_reflections": 2
  }
  ```

  `visual_density`는 `minimal`, `balanced`, `high` 중 하나입니다. 기본값 `high`는 서문·부록을 제외한 각 장에 최소 2개의 학습용 개념도/흐름도를 계획합니다. 정량 그래프는 `technical_notes.md`에 실제 수치가 있을 때만 만들며, 생성에 실패한 자리표시자 이미지는 원고에 포함하지 않습니다. `raw_materials/figures/`에 넣은 PNG/JPEG/PDF는 자동으로 등록되어 집필 에이전트가 활용합니다.

  **권장 모델 및 역할별 자동 티어 분배:**
  대화창 모델을 **Gemini 4 Neon (Medium)**으로 선택해 두고 워크플로우를 실행하면, 역할별 기본 설정(`writer_model_name="inherit"`, `reflection_model_name="pro"`, `plotting_model_name="flash"`)에 따라 아래와 같이 자동 분배됩니다.
  - **집필 & 청사진** (`writer_model_name="inherit"`): **Gemini 4 Neon**으로 도서 청사진, 서문/1장, 전체 본문 집필을 수행합니다.
  - **병렬 리서치 & 플로팅** (`research` / `plotting_model_name="flash"`): 경량 고속 티어로 병렬 참고문헌 탐색과 차트 코드 생성·시각 QA 루프를 빠르게 처리합니다.
  - **편집위원회 리뷰 & 퇴고·조판 QA** (`reflection_model_name="pro"`): **Pro** 티어로 3인 편집위원회 비평, 퇴고, 최종 조판 QA를 수행합니다.

### 2. Generate a `run_workflow` Payload from CLI
```bash
python3 book_writing_cli.py \
  --raw_materials_dir examples/distributed_llm_systems_book/raw_materials \
  --latex_template_dir templates/oreilly_style_book \
  --output_dir ./book_output_llm_systems \
  --emit_workflow_only
```

### 3. Turn Lecture Slides into a Friendly Explanatory Book

PPTX와 슬라이드 PDF를 바로 입력할 수 있습니다. PPTX에서는 보이는 텍스트, 발표자 노트,
표, 차트의 캐시 값과 재사용 가능한 이미지를 추출합니다. PDF에서는 페이지 텍스트와 의미
있는 크기의 래스터 이미지를 추출하고, 독립 이미지가 없는 벡터 중심 페이지는 제한된
해상도의 학습용 이미지로 보존합니다. 숨김 PPTX 슬라이드는 원본 추적 정보에는 남기지만
책 집필 입력에서는 제외합니다. 원본 파일과 SHA-256, 슬라이드별 출처 위치는
`raw_materials/source_slides/`와 `raw_materials/slide_source_manifest.json`에 보존됩니다.

```bash
python3 book_writing_cli.py \
  --slides_file ./lecture/async-programming.pptx \
  --latex_template_dir templates/oreilly_style_book \
  --output_dir ./book_output_async_course \
  --target_audience "처음 비동기 프로그래밍을 배우는 개발자" \
  --book_style "친절한 개념 설명과 작은 예제가 풍부한 강의형 교재" \
  --author_voice "독자 옆에서 차근차근 설명하는 따뜻하고 정확한 멘토" \
  --visual_density high
```

`--slides_file`과 `--raw_materials_dir`은 둘 중 하나만 사용합니다. 변환기는 슬라이드 한 장을
책의 한 절로 기계적으로 옮기지 않고, 인접한 슬라이드를 주제별 장으로 묶도록 집필 에이전트에
지시합니다. 불릿 사이의 전제와 인과관계는 풀어 쓰되, 원본에 없는 수치·주장·발표자 의도는
만들지 않습니다.

### 4. Turn a Codebase into an Explanatory Book

로컬 프로젝트 또는 별도로 clone한 저장소를 정적 분석하여 아키텍처와 주요 실행 흐름을
설명하는 책을 만들 수 있습니다. 수집 단계에서는 프로젝트 코드를 실행하지 않습니다. `.git`,
의존성, 빌드 산출물, 바이너리, 비밀 파일과 심볼릭 링크를 제외하고 문서·설정·소스·테스트 중
대표 파일을 선택합니다. 비밀값으로 보이는 리터럴은 `<REDACTED>`로 치환하며, 원본 해시와
경로·언어·심볼 정보를 `codebase_source_manifest.json`에 기록합니다.

```bash
python3 book_writing_cli.py \
  --codebase_dir /path/to/local/repository \
  --latex_template_dir templates/oreilly_style_book \
  --output_dir ./book_output_codebase \
  --target_audience "이 프로젝트를 처음 유지보수하는 개발자" \
  --book_style "아키텍처에서 실제 요청 흐름으로 내려가는 코드 워크스루" \
  --author_voice "근거 경로를 제시하며 친절하게 설명하는 시니어 개발자"
```

`--codebase_dir`, `--slides_file`, `--raw_materials_dir`은 상호 배타적입니다. 다른 저장소를
다룰 때는 자동 clone 대신 사용자가 검토한 로컬 checkout을 입력합니다. 출판 전에는 해당
저장소의 라이선스와 소스 인용 조건을 확인해야 합니다.

---

## Generated Outputs
Inside `output_dir`:
- `final_book.pdf`: Publication-grade book manuscript PDF compiled via `pdflatex` (`-no-shell-escape`) or `tectonic` inside an isolated TeX sandbox
- `preview_book.pdf`: Semantic HTML preview with embedded Unicode/CJK font, generated only when no TeX engine is installed and `strict_latex=false`
- `build_manifest.json`: Build metadata recording `render_mode` (`pdflatex`, `tectonic`, or `preview`) and `is_publication_grade`
- `final_book.tex`: Complete compilable LaTeX book manuscript (with `references.bib` and `figures/` synced at `output_dir` root)
- `final_book.md`: Clean Markdown book manuscript for Markdown/Pandoc/EPUB workflows; always exported alongside TeX/PDF for proposal, slide/PDF, and codebase inputs
- `latex_writeup/chapters/ch01_*.tex`, `ch02_*.tex`, ...: Modular chapter-by-chapter LaTeX files
- `book_continuity_ledger.json`: Tracked glossary terms, chapter titles, code listings, and cross-reference labels
- `raw_materials/slide_source_manifest.json`: Slide/page-level provenance, original source hash, notes, and extracted-visual map when `--slides_file` is used
- `raw_materials/codebase_source_manifest.json`: Selected path/symbol/hash provenance and redaction metadata when `--codebase_dir` is used
- `raw_materials/source_code/`: Secret-redacted snapshot of the selected codebase evidence files
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
