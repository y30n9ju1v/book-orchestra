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
Reference Verification, Cutoff Enforcement, BibTeX Typing, and Citation Audit
for BookOrchestra.
"""

import re
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

DOI_REGEX = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Za-z0-9]+$")
ARXIV_REGEX = re.compile(r"^(?:arXiv:)?(\d{4}\.\d{4,5})(?:v\d+)?$", re.IGNORECASE)
RFC_REGEX = re.compile(r"^(?:RFC[\s-]*)?(\d{3,5})$", re.IGNORECASE)
ISBN_REGEX = re.compile(r"^(?:97[89][-\s]?)?\d{1,5}[-\s]?\d{1,7}[-\s]?\d{1,6}[-\s]?[\dX]$", re.IGNORECASE)


def is_reference_before_cutoff(cand: Dict, cutoff_date: Optional[str]) -> bool:
    """
    Strictly validates that a candidate reference was published on or before `cutoff_date` (YYYY-MM or YYYY-MM-DD).
    Rejects entries with missing/invalid years or dates after the cutoff.
    """
    year_raw = cand.get("year")
    try:
        p_y = int(year_raw)
    except (TypeError, ValueError):
        return False

    if p_y < 1950 or p_y > 2100:
        return False

    if not cutoff_date:
        return True

    try:
        c_parts = [int(x) for x in str(cutoff_date).strip().split("-")]
        c_y = c_parts[0]
        c_m = c_parts[1] if len(c_parts) > 1 else 12
        c_d = c_parts[2] if len(c_parts) > 2 else 31
    except Exception:
        return True

    p_m = None
    p_d = None
    pub_date = cand.get("publication_date") or cand.get("publicationDate")
    if pub_date:
        try:
            p_parts = [int(x) for x in str(pub_date).strip().split("-")]
            if len(p_parts) >= 1:
                p_y = p_parts[0]
            if len(p_parts) >= 2:
                p_m = p_parts[1]
            if len(p_parts) >= 3:
                p_d = p_parts[2]
        except Exception:
            pass

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
    return p_d <= c_d


def has_verifiable_identifier_or_grounding(
    cand: Dict,
    grounding_text: str = "",
    require_strict_id: bool = False,
) -> Tuple[bool, str]:
    """
    Checks whether a reference candidate has a verifiable bibliographic identifier
    (DOI, arXiv ID, RFC number, ISBN, or valid https/http URL) or is explicitly grounded
    in the book's source notes/hints.
    Returns (is_valid, verification_source).
    """
    doi = str(cand.get("doi") or "").strip()
    if doi and DOI_REGEX.match(doi):
        return True, f"doi:{doi}"

    arxiv_id = str(cand.get("arxiv_id") or "").strip()
    if arxiv_id and ARXIV_REGEX.match(arxiv_id):
        return True, f"arxiv:{arxiv_id}"

    rfc_num = str(cand.get("rfc_number") or "").strip()
    if rfc_num and RFC_REGEX.match(rfc_num):
        return True, f"rfc:{rfc_num}"

    isbn = str(cand.get("isbn") or "").strip()
    if isbn and ISBN_REGEX.match(isbn):
        return True, f"isbn:{isbn}"

    url = str(cand.get("url") or "").strip()
    if url:
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.netloc and "." in parsed.netloc:
            return True, f"url:{url}"

    # Check if title or RFC/arXiv pattern appears in title/venue or grounding notes
    title = str(cand.get("title") or "").strip()
    if RFC_REGEX.search(title):
        return True, "rfc_in_title"
    if not require_strict_id and title and grounding_text:
        norm_title = re.sub(r"[^a-z0-9]", "", title.lower())
        norm_grounding = re.sub(r"[^a-z0-9]", "", grounding_text.lower())
        if len(norm_title) >= 8 and norm_title in norm_grounding:
            return True, "grounded_in_source_notes"
        # Check author + keyword grounding in hints/notes
        authors = cand.get("authors") or []
        if authors:
            first_last = str(authors[0]).split()[-1].lower()
            if len(first_last) >= 4 and first_last in grounding_text.lower():
                return True, "grounded_author_in_notes"

    return False, "unverified"


def infer_bibtex_entry_type(ref: Dict) -> str:
    """Infers the proper BibTeX entry type (@book, @techreport, @article, @inproceedings, @misc)."""
    explicit = str(ref.get("source_type") or ref.get("entry_type") or "").lower().strip()
    venue = str(ref.get("venue") or ref.get("publisher") or "").lower()
    title = str(ref.get("title") or "").lower()

    if explicit == "book" or ref.get("isbn") or any(
        pub in venue
        for pub in ("o'reilly", "manning", "addison-wesley", "pragmatic", "springer", "press", "wiley")
    ):
        return "book"

    if (
        explicit in ("rfc", "standard", "techreport", "report")
        or ref.get("rfc_number")
        or "rfc" in venue
        or title.startswith("rfc ")
        or "technical report" in venue
        or "w3c" in venue
        or "ietf" in venue
    ):
        return "techreport"

    if explicit in ("article", "journal") or ref.get("journal") or "journal" in venue or "transactions on" in venue:
        return "article"

    if explicit in ("misc", "documentation", " specification"):
        return "misc"

    return "inproceedings"


def format_bibtex_entry(ref: Dict) -> str:
    """Formats a reference dictionary into a properly typed BibTeX entry."""
    entry_type = infer_bibtex_entry_type(ref)
    key = ref["citation_key"]
    title = ref.get("title", "Untitled")
    authors = ref.get("authors") or ["Unknown"]
    author_str = " and ".join(authors) if isinstance(authors, list) else str(authors)
    year = ref.get("year", 2024)
    venue = ref.get("venue") or "Technical Literature"

    lines = [
        f"@{entry_type}{{{key},",
        f"  title={{{title}}},",
        f"  author={{{author_str}}},",
    ]

    if entry_type == "book":
        publisher = ref.get("publisher") or venue
        lines.append(f"  publisher={{{publisher}}},")
        if ref.get("isbn"):
            lines.append(f"  isbn={{{ref['isbn']}}},")
    elif entry_type == "techreport":
        institution = ref.get("institution") or venue
        lines.append(f"  institution={{{institution}}},")
        if ref.get("rfc_number"):
            lines.append(f"  number={{{ref['rfc_number']}}},")
    elif entry_type == "article":
        journal = ref.get("journal") or venue
        lines.append(f"  journal={{{journal}}},")
        if ref.get("volume"):
            lines.append(f"  volume={{{ref['volume']}}},")
        if ref.get("pages"):
            lines.append(f"  pages={{{ref['pages']}}},")
    elif entry_type == "misc":
        lines.append(f"  howpublished={{{venue}}},")
    else:
        lines.append(f"  booktitle={{{venue}}},")

    if ref.get("doi"):
        lines.append(f"  doi={{{ref['doi']}}},")
    if ref.get("url"):
        lines.append(f"  url={{{ref['url']}}},")

    lines.append(f"  year={{{year}}}")
    lines.append("}")
    return "\n".join(lines)


def audit_and_clean_citations(
    latex_text: str, allowed_keys: Set[str]
) -> Tuple[str, List[str]]:
    """
    Audits all \\cite{...}, \\citet{...}, \\citep{...} commands in `latex_text`.
    Removes any unverified/hallucinated citation keys not present in `allowed_keys`.
    Returns (cleaned_latex, list_of_removed_keys).
    """
    if not latex_text:
        return "", []

    removed_keys: List[str] = []

    def _replace_cite(match):
        cmd = match.group(1)
        opt = match.group(2) or ""
        keys_raw = match.group(3)
        keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
        valid = []
        for k in keys:
            if k in allowed_keys:
                valid.append(k)
            else:
                removed_keys.append(k)
        if not valid:
            return ""
        return f"\\{cmd}{opt}{{{', '.join(valid)}}}"

    cleaned = re.sub(
        r"\\(cite|citet|citep)(\[[^\]]*\])?\{([^}]+)\}",
        _replace_cite,
        latex_text,
    )
    return cleaned, sorted(set(removed_keys))
