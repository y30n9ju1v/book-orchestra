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
Side-by-Side (SxS) Technical Book Quality Evaluator for BookOrchestra.
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from utils.pdf_utils import load_book
from utils.llm_backend_utils import get_llm_parser
from utils.agent_runtime_utils import call_agent_with_contents, types
from autoraters.prompts.sxs_quality_prompts import sxs_book_quality_system_prompt

FILE_LOCK = threading.Lock()


def rate_sxs_book_quality(
    book1_path: str, book2_path: str, model_name: str = "inherit"
):
    prompt_text = (
        "Task: Compare the overall engineering depth, code quality, pedagogical progression, "
        "trade-off analysis, and typesetting of these two technical book manuscripts side by side."
    )

    book1_text = load_book(book1_path, min_size=10)
    book2_text = load_book(book2_path, min_size=10)

    contents = [
        types.Part.from_text(text="Book 1:\n" + book1_text),
        types.Part.from_text(text="\n\nBook 2:\n" + book2_text),
        types.Part.from_text(text="\n\n" + prompt_text),
    ]

    response_dict = call_agent_with_contents(
        contents=contents,
        model_name=model_name,
        result_parsing_func=get_llm_parser(model_name, return_json=True),
        generation_configs={
            "temperature": 0.0,
            "system_instruction": sxs_book_quality_system_prompt,
        },
        check_parsed_response_not_none=True,
    )
    return (
        response_dict.get("parsed_response", response_dict)
        if isinstance(response_dict, dict)
        else response_dict
    )
