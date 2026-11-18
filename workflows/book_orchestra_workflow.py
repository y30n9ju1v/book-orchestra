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
Antigravity Native `run_workflow` Entrypoint for BookOrchestra.

Zero external `GEMINI_API_KEY` or `OPENAI_API_KEY` required.
Invoke directly with Antigravity's `run_workflow` tool:
  run_workflow(
      ScriptPath="/Users/yeongjun/Develops/book-orchestra/workflows/book_orchestra_workflow.py",
      Args={
          "raw_materials_dir": "examples/distributed_llm_systems_book/raw_materials",
          "latex_template_dir": "templates/oreilly_style_book",
          "output_dir": "./book_output_agy",
          "use_plotting": True,
      },
      Description="Author and review an IT/Engineering book with BookOrchestra"
  )
"""

import os
import sys

_WORK_ROOT = (
    WORKSPACES[0]  # type: ignore[name-defined]
    if "WORKSPACES" in globals() and WORKSPACES  # type: ignore[name-defined]
    else os.getcwd()
)
if _WORK_ROOT not in sys.path:
    sys.path.insert(0, _WORK_ROOT)

from methods.agy_workflow_runner import run_book_orchestra_workflow

_wf_args = args if "args" in globals() and isinstance(args, dict) else {}  # type: ignore[name-defined]

result = await run_book_orchestra_workflow(  # type: ignore[top-level-await]
    workflow_args=_wf_args,
    phase_fn=phase,  # type: ignore[name-defined]
    log_fn=log,  # type: ignore[name-defined]
    agent_fn=agent,  # type: ignore[name-defined]
    parallel_fn=parallel,  # type: ignore[name-defined]
    pipeline_fn=pipeline,  # type: ignore[name-defined]
)
print(result)
