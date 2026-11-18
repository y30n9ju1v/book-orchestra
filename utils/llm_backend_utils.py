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
Antigravity LLM & Multimodal dispatch utilities for BookOrchestra.
"""

from typing import Optional, Callable, Dict, Any

from utils.gemini_utils import (
    call_gemini_with_text_prompt,
    parse_gemini_json_results,
    call_gemini_with_contents,
    call_gemini_with_images,
    types,
)


def identity_parse(response: str):
    return response


def get_llm_parser(model_name: str, return_json: bool = True) -> Callable:
    if not return_json:
        return identity_parse
    return parse_gemini_json_results


def call_llm_with_text_prompt(
    prompt: str,
    model_name: str = "inherit",
    generation_configs: Optional[Dict[str, Any]] = None,
    check_parsed_response_not_none: bool = True,
    return_json: bool = True,
    result_parsing_func: Optional[Callable] = None,
) -> Dict[str, Any]:
    parser = (
        result_parsing_func
        if result_parsing_func is not None
        else get_llm_parser(model_name, return_json)
    )
    return call_gemini_with_text_prompt(
        prompt=prompt,
        model_name=model_name,
        generation_configs=generation_configs or {},
        result_parsing_func=parser,
        check_parsed_response_not_none=check_parsed_response_not_none,
    )


def call_llm_with_images(
    prompt: str,
    images: list[str],
    model_name: str = "inherit",
    generation_configs: Optional[Dict[str, Any]] = None,
    check_parsed_response_not_none: bool = True,
    return_json: bool = True,
    result_parsing_func: Optional[Callable] = None,
) -> Dict[str, Any]:
    parser = (
        result_parsing_func
        if result_parsing_func is not None
        else get_llm_parser(model_name, return_json)
    )
    return call_gemini_with_images(
        prompt=prompt,
        images=images,
        model_name=model_name,
        generation_configs=generation_configs or {},
        result_parsing_func=parser,
        check_parsed_response_not_none=check_parsed_response_not_none,
    )


def call_llm_with_pdf(
    pdf_path: str,
    prompt: str,
    model_name: str = "inherit",
    system_instruction: Optional[str] = None,
    temperature: float = 0.7,
    check_parsed_response_not_none: bool = True,
    return_json: bool = True,
    result_parsing_func: Optional[Callable] = None,
) -> Dict[str, Any]:
    parser = (
        result_parsing_func
        if result_parsing_func is not None
        else get_llm_parser(model_name, return_json)
    )
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    contents = [
        types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
        types.Part.from_text(text=prompt),
    ]
    generation_configs: Dict[str, Any] = {"temperature": temperature}
    if system_instruction is not None:
        generation_configs["system_instruction"] = system_instruction

    return call_gemini_with_contents(
        contents=contents,
        model_name=model_name,
        generation_configs=generation_configs,
        result_parsing_func=parser,
        check_parsed_response_not_none=check_parsed_response_not_none,
    )
