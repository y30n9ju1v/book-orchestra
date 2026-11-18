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
Compatibility wrapper routing former OpenAI helper calls through the Antigravity
native workflow bridge (`utils/gemini_utils.py`) with zero external API keys.
"""

from typing import Any, Callable, Optional
import base64

from utils.gemini_utils import (
    parse_gemini_json_results,
    call_gemini_with_contents,
    call_gemini_with_text_prompt,
    types,
)

parse_openai_json_results = parse_gemini_json_results


def call_openai_models_with_content(
    content: list[Any],
    model_name: str = "inherit",
    max_retries: int = 3,
    base_interval_sec: int = 1,
    result_parsing_func: Callable = parse_openai_json_results,
    generation_configs: Optional[dict] = None,
    check_parsed_response_not_none: bool = True,
    system_prompt: Optional[str] = None,
):
    configs = dict(generation_configs or {})
    if system_prompt:
        configs["system_instruction"] = system_prompt

    parts = []
    for item in content or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(types.Part.from_text(text=item.get("text", "")))
        elif isinstance(item, str):
            parts.append(types.Part.from_text(text=item))
    return call_gemini_with_contents(
        contents=parts,
        model_name=model_name,
        max_retries=max_retries,
        base_interval_sec=base_interval_sec,
        result_parsing_func=result_parsing_func,
        generation_configs=configs,
        check_parsed_response_not_none=check_parsed_response_not_none,
    )


def call_openai_models_with_text_prompt(
    prompt: str,
    model_name: str = "inherit",
    result_parsing_func: Callable = parse_openai_json_results,
    generation_configs: Optional[dict] = None,
    check_parsed_response_not_none: bool = True,
    max_retries: int = 3,
    base_interval_sec: int = 1,
    system_prompt: Optional[str] = None,
):
    configs = dict(generation_configs or {})
    if system_prompt:
        configs["system_instruction"] = system_prompt
    return call_gemini_with_text_prompt(
        prompt=prompt,
        model_name=model_name,
        result_parsing_func=result_parsing_func,
        generation_configs=configs,
        check_parsed_response_not_none=check_parsed_response_not_none,
        max_retries=max_retries,
        base_interval_sec=base_interval_sec,
    )


def get_openai_image_part_from_path(image_path: str):
    with open(image_path, "rb") as image_file:
        base64_image = base64.b64encode(image_file.read()).decode("utf-8")

    return [
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{base64_image}",
            },
        }
    ]
