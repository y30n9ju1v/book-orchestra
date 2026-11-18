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
Reference Verification, Authoritative Registry Resolution, Cutoff Enforcement,
BibTeX Typing, and Citation Audit for BookOrchestra.
"""

import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

DOI_REGEX = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Za-z0-9]+$")
FAKE_DOI_PREFIXES = (
    "10.9999/",
    "10.0000/",
    "10.1234/",
    "10.5555/fake",
    "10.1111/fake",
    "10.1000/test",
)
ARXIV_REGEX = re.compile(r"^(?:arXiv:)?(\d{4}\.\d{4,5})(?:v\d+)?$", re.IGNORECASE)
RFC_REGEX = re.compile(r"^(?:RFC[\s\-:]*)?(\d{3,5})$", re.IGNORECASE)
ISBN_REGEX = re.compile(
    r"^(?:97[89][-\s]?)?\d{1,5}[-\s]?\d{1,7}[-\s]?\d{1,6}[-\s]?[\dX]$",
    re.IGNORECASE,
)

RESERVED_FAKE_HOSTS = {
    "example.com",
    "www.example.com",
    "example.org",
    "www.example.org",
    "example.net",
    "www.example.net",
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
}
RESERVED_FAKE_TLDS = (".invalid", ".example", ".test", ".localhost", ".local")

# Curated canonical catalog of foundational systems/AI/engineering references
# used to cross-verify metadata and allow deterministic verification when running
# inside an offline sandbox without external network access.
CANONICAL_ENGINEERING_REFERENCES: List[Dict] = [
    {
        "doi": "10.1145/3600006.3613165",
        "arxiv_id": "2309.06180",
        "title": "Efficient Memory Management for Large Language Model Serving with PagedAttention",
        "authors": ["Woosuk Kwon", "Zhuohan Li"],
        "year": 2023,
    },
    {
        "arxiv_id": "2205.14135",
        "title": "FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness",
        "authors": ["Tri Dao", "Daniel Y. Fu", "Stefano Ermon", "Atri Rudra", "Christopher Re"],
        "year": 2022,
    },
    {
        "arxiv_id": "1706.03762",
        "title": "Attention Is All You Need",
        "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"],
        "year": 2017,
    },
    {
        "isbn": "9781449373320",
        "title": "Designing Data-Intensive Applications",
        "authors": ["Martin Kleppmann"],
        "year": 2017,
    },
    {
        "isbn": "9781491929124",
        "title": "Site Reliability Engineering: How Google Runs Production Systems",
        "authors": ["Betsy Beyer", "Chris Jones", "Jennifer Petoff", "Niall Richard Murphy"],
        "year": 2016,
    },
    {
        "rfc_number": "9114",
        "title": "RFC 9114: HTTP/3",
        "authors": ["Mike Bishop"],
        "year": 2022,
        "url": "https://www.rfc-editor.org/rfc/rfc9114",
    },
    {
        "rfc_number": "9000",
        "title": "RFC 9000: QUIC: A UDP-Based Multiplexed and Secure Transport",
        "authors": ["Jana Iyengar", "Martin Thomson"],
        "year": 2021,
        "url": "https://www.rfc-editor.org/rfc/rfc9000",
    },
    {
        "doi": "10.1145/2408776.2408794",
        "title": "The Tail at Scale",
        "authors": ["Jeffrey Dean", "Luiz Andre Barroso"],
        "year": 2013,
    },
    {
        "doi": "10.1145/945445.945450",
        "title": "The Google File System",
        "authors": ["Sanjay Ghemawat", "Howard Gobioff", "Shun-Tak Leung"],
        "year": 2003,
    },
    {
        "doi": "10.1145/1327452.1327492",
        "title": "MapReduce: Simplified Data Processing on Large Clusters",
        "authors": ["Jeffrey Dean", "Sanjay Ghemawat"],
        "year": 2004,
    },
    {
        "doi": "10.1145/2491245",
        "title": "Spanner: Google's Globally-Distributed Database",
        "authors": ["James C. Corbett", "Jeffrey Dean"],
        "year": 2012,
    },
    {
        "doi": "10.1145/279227.279229",
        "title": "The Part-Time Parliament",
        "authors": ["Leslie Lamport"],
        "year": 1998,
    },
]

_STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "for",
    "in",
    "on",
    "with",
    "and",
    "to",
    "by",
    "from",
    "at",
    "via",
    "using",
}


def _tokenize_title(title: str) -> Set[str]:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    tokens = {w for w in words if len(w) >= 2 and w not in _STOPWORDS}
    return tokens or set(words)


def _title_similarity(title_a: str, title_b: str) -> float:
    ta = _tokenize_title(title_a)
    tb = _tokenize_title(title_b)
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    return overlap / float(min(len(ta), len(tb)))


def is_valid_isbn_checksum(isbn_raw: str) -> bool:
    """Validates ISBN-10 (mod 11) and ISBN-13 (EAN-13 mod 10) mathematical check digits."""
    clean = re.sub(r"[^0-9Xx]", "", str(isbn_raw or "")).upper()
    if len(clean) == 10:
        if not clean[:9].isdigit():
            return False
        total = 0
        for i in range(9):
            total += (10 - i) * int(clean[i])
        check = 10 if clean[9] == "X" else int(clean[9])
        total += check
        return total % 11 == 0
    if len(clean) == 13:
        if not clean.isdigit() or not clean.startswith(("978", "979")):
            return False
        total = sum(
            (1 if i % 2 == 0 else 3) * int(d) for i, d in enumerate(clean[:12])
        )
        check = (10 - (total % 10)) % 10
        return check == int(clean[12])
    return False


def _is_forbidden_ip_address(ip_str: str) -> bool:
    """Returns True if `ip_str` is loopback, private, link-local, multicast, reserved, or unspecified."""
    try:
        ip_obj = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return bool(
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
        or not ip_obj.is_global
    )


def _check_host_public_dns(host: str) -> bool:
    """
    Validates that `host` does not denote a forbidden IP literal and, when DNS is
    available, resolves cleanly without pointing to private/link-local/loopback IPs.
    """
    clean_host = (host or "").strip("[]").lower()
    if not clean_host:
        return False

    # Reject IP literals in forbidden ranges immediately (e.g. 169.254.169.254, 10.0.0.1, 127.0.0.1, ::1)
    try:
        ipaddress.ip_address(clean_host)
        return not _is_forbidden_ip_address(clean_host)
    except ValueError:
        pass

    # If host resolves via DNS, ensure EVERY resolved IP is globally routable
    try:
        infos = socket.getaddrinfo(clean_host, None)
        if infos:
            for info in infos:
                addr = info[4][0]
                if "%" in addr:
                    addr = addr.split("%", 1)[0]
                if _is_forbidden_ip_address(addr):
                    return False
    except OSError:
        # DNS resolution may be disabled in offline sandboxes; rely on hostname/domain checks below
        pass
    return True


def is_safe_authoritative_url(
    url_raw: str, check_dns: bool = True
) -> bool:
    """
    Rejects malformed, local, `.invalid`, `.example`, `.test`, private/link-local/metadata IP,
    and fake URLs to prevent SSRF.
    """
    url = str(url_raw or "").strip()
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    host = parsed.hostname.lower() if parsed.hostname else ""
    if not host or "." not in host:
        return False
    if host in RESERVED_FAKE_HOSTS:
        return False
    if any(host.endswith(tld) for tld in RESERVED_FAKE_TLDS):
        return False
    if any(
        part in ("fake", "invalid", "example", "nonexistent", "placeholder", "dummy")
        for part in host.split(".")
    ):
        return False
    if check_dns and not _check_host_public_dns(host):
        return False
    return True


class _SSRFGuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validates every redirect target URL against SSRF rules before following."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        resolved = urllib.parse.urljoin(req.full_url, str(newurl))
        if not is_safe_authoritative_url(resolved, check_dns=True):
            raise urllib.error.URLError(
                f"Blocked unsafe redirect destination: {resolved}"
            )
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def _ssrf_safe_urlopen(req: urllib.request.Request, timeout: float = 3.5):
    """
    Validates both initial URL and all HTTP redirects against SSRF rules (private/loopback/metadata IPs).
    """
    if not is_safe_authoritative_url(req.full_url, check_dns=True):
        raise ValueError(f"Blocked unsafe or private URL: {req.full_url}")
    opener = urllib.request.build_opener(_SSRFGuardedRedirectHandler())
    return opener.open(req, timeout=timeout)


def _match_canonical_catalog(cand: Dict) -> Tuple[Optional[bool], str]:
    """
    Checks if `cand` matches an identifier in `CANONICAL_ENGINEERING_REFERENCES`.
    Returns:
      - (True, source) if identifier + title + year match the canonical record,
      - (False, reason) if identifier is in catalog but title or year contradicts it,
      - (None, "") if identifier is not in the local canonical catalog.
    """
    doi = str(cand.get("doi") or "").strip().lower()
    arxiv_m = ARXIV_REGEX.match(str(cand.get("arxiv_id") or "").strip())
    arxiv_id = arxiv_m.group(1) if arxiv_m else ""
    rfc_m = RFC_REGEX.match(str(cand.get("rfc_number") or "").strip())
    rfc_num = rfc_m.group(1) if rfc_m else ""
    isbn_clean = re.sub(r"[^0-9Xx]", "", str(cand.get("isbn") or "")).upper()
    cand_title = str(cand.get("title") or "").strip()
    cand_year = int(cand.get("year") or 0)

    for entry in CANONICAL_ENGINEERING_REFERENCES:
        matched_id = None
        if doi and entry.get("doi", "").lower() == doi:
            matched_id = f"doi:{cand.get('doi')}"
        elif arxiv_id and entry.get("arxiv_id") == arxiv_id:
            matched_id = f"arxiv:{arxiv_id}"
        elif rfc_num and entry.get("rfc_number") == rfc_num:
            matched_id = f"rfc:RFC {rfc_num}"
        elif isbn_clean and entry.get("isbn") == isbn_clean:
            matched_id = f"isbn:{cand.get('isbn')}"

        if matched_id:
            sim = _title_similarity(cand_title, entry["title"])
            year_diff = abs(cand_year - int(entry["year"])) if cand_year else 999
            if sim >= 0.45 and year_diff <= 1:
                return True, matched_id
            return False, f"metadata_mismatch_with_catalog:{matched_id}"

    return None, ""


def _http_get_json(url: str, timeout: float = 3.5) -> Tuple[str, Optional[dict]]:
    """
    Fetches JSON from an authoritative bibliographic API.
    Returns (status_kind, data) where status_kind is one of:
      - 'ok': 200 JSON parsed
      - 'not_found': HTTP 404 / 400 (record definitely does not exist)
      - 'unreachable': network blocked / offline / timeout
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "BookOrchestra-ReferenceVerifier/1.0 (mailto:book-orchestra@example.org)",
            "Accept": "application/json",
        },
    )
    try:
        with _ssrf_safe_urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                raw = resp.read().decode("utf-8", errors="replace")
                return "ok", json.loads(raw)
            if resp.status in (400, 404, 410):
                return "not_found", None
            return "unreachable", None
    except ValueError:
        return "not_found", None
    except urllib.error.HTTPError as e:
        if e.code in (400, 404, 410):
            return "not_found", None
        return "unreachable", None
    except Exception:
        return "unreachable", None


def _verify_doi_online(doi: str, cand_title: str, cand_year: int) -> Tuple[Optional[bool], str]:
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
    status, data = _http_get_json(url)
    if status == "not_found":
        return False, "doi_not_found_in_crossref"
    if status == "ok" and isinstance(data, dict):
        msg = data.get("message", {})
        titles = msg.get("title") or []
        remote_title = titles[0] if titles else ""
        if remote_title and _title_similarity(cand_title, remote_title) < 0.4:
            return False, "doi_title_mismatch"
        issued = (
            msg.get("issued", {}).get("date-parts")
            or msg.get("published-print", {}).get("date-parts")
            or msg.get("published-online", {}).get("date-parts")
            or []
        )
        if issued and issued[0] and isinstance(issued[0][0], int) and cand_year:
            if abs(int(issued[0][0]) - cand_year) > 2:
                return False, "doi_year_mismatch"
        return True, f"doi:{doi}"
    return None, "network_unreachable"


def _verify_arxiv_online(
    arxiv_num: str, cand_title: str, cand_year: int
) -> Tuple[Optional[bool], str]:
    url = f"https://export.arxiv.org/api/query?id_list={urllib.parse.quote(arxiv_num)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "BookOrchestra-ReferenceVerifier/1.0"}
    )
    try:
        with _ssrf_safe_urlopen(req, timeout=3.5) as resp:
            xml_text = resp.read().decode("utf-8", errors="replace")
        root = ET.fromstring(xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entry = root.find("atom:entry", ns)
        if entry is None:
            return False, "arxiv_entry_not_found"
        title_el = entry.find("atom:title", ns)
        remote_title = (title_el.text or "").strip() if title_el is not None else ""
        if not remote_title or remote_title.lower() == "error":
            return False, "arxiv_id_invalid"
        if _title_similarity(cand_title, remote_title) < 0.4:
            return False, "arxiv_title_mismatch"
        pub_el = entry.find("atom:published", ns)
        if pub_el is not None and pub_el.text and cand_year:
            remote_year = int(pub_el.text[:4])
            if abs(remote_year - cand_year) > 2:
                return False, "arxiv_year_mismatch"
        return True, f"arxiv:{arxiv_num}"
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            return False, "arxiv_not_found"
        return None, "network_unreachable"
    except Exception:
        return None, "network_unreachable"


def _verify_rfc_online(
    rfc_num: str, cand_title: str, cand_year: int
) -> Tuple[Optional[bool], str]:
    url = f"https://www.rfc-editor.org/rfc/rfc{int(rfc_num)}.json"
    status, data = _http_get_json(url)
    if status == "not_found":
        return False, "rfc_not_found"
    if status == "ok" and isinstance(data, dict):
        remote_title = str(data.get("title") or "").strip()
        if remote_title and _title_similarity(cand_title, f"RFC {rfc_num} {remote_title}") < 0.35:
            return False, "rfc_title_mismatch"
        return True, f"rfc:RFC {int(rfc_num)}"
    return None, "network_unreachable"


def _verify_isbn_online(
    isbn_clean: str, cand_title: str, cand_year: int
) -> Tuple[Optional[bool], str]:
    url = f"https://openlibrary.org/isbn/{urllib.parse.quote(isbn_clean)}.json"
    status, data = _http_get_json(url)
    if status == "not_found":
        return False, "isbn_not_found_in_openlibrary"
    if status == "ok" and isinstance(data, dict):
        remote_title = str(data.get("title") or "").strip()
        if remote_title and _title_similarity(cand_title, remote_title) < 0.4:
            return False, "isbn_title_mismatch"
        return True, f"isbn:{isbn_clean}"
    return None, "network_unreachable"


def _verify_url_online(url: str, cand_title: str) -> Tuple[Optional[bool], str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "BookOrchestra-ReferenceVerifier/1.0",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with _ssrf_safe_urlopen(req, timeout=3.5) as resp:
            if not is_safe_authoritative_url(resp.geturl(), check_dns=True):
                return False, "unsafe_redirect_target"
            if resp.status >= 400:
                return False, f"url_http_{resp.status}"
            chunk = resp.read(32768).decode("utf-8", errors="ignore")
            title_m = re.search(r"<title[^>]*>(.*?)</title>", chunk, re.IGNORECASE | re.DOTALL)
            if title_m:
                page_title = title_m.group(1).strip()
                if page_title and _title_similarity(cand_title, page_title) < 0.25:
                    # Also check if candidate title tokens appear in body chunk
                    tokens = _tokenize_title(cand_title)
                    chunk_lower = chunk.lower()
                    matched = sum(1 for t in tokens if t in chunk_lower)
                    if tokens and (matched / len(tokens)) < 0.5:
                        return False, "url_content_mismatch"
            return True, f"url:{url}"
    except urllib.error.HTTPError as e:
        if e.code in (400, 404, 410):
            return False, f"url_http_{e.code}"
        return None, "network_unreachable"
    except Exception:
        return None, "network_unreachable"


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
    verify_online: bool = True,
) -> Tuple[bool, str]:
    """
    Verifies a reference candidate against authoritative registries (Crossref, arXiv,
    RFC Editor, Open Library), mathematical checksums (ISBN-10/13), the curated
    canonical systems catalog, or verbatim full-title grounding in the source notes.

    Rejects:
    - Reserved/test/fake DOIs (`10.9999/...`, `10.0000/...`, `10.1234/...`)
    - Invalid ISBN check digits
    - Reserved/invalid/fake URLs (`.invalid`, `.example`, `.test`, `example.com`, etc.)
    - Identifiers whose remote metadata contradicts the candidate's title or year
    - Loose author-surname-only matches
    """
    cand_title = str(cand.get("title") or "").strip()
    if not cand_title or len(cand_title) < 4:
        return False, "invalid_title"
    try:
        cand_year = int(cand.get("year") or 0)
    except (TypeError, ValueError):
        cand_year = 0

    # 0. Check canonical systems/engineering catalog first (deterministic & fast)
    cat_match, cat_reason = _match_canonical_catalog(cand)
    if cat_match is True:
        return True, cat_reason
    if cat_match is False:
        return False, cat_reason

    # 1. DOI verification
    doi = str(cand.get("doi") or "").strip()
    if doi:
        doi_lower = doi.lower()
        if not DOI_REGEX.match(doi) or any(
            doi_lower.startswith(pfx) for pfx in FAKE_DOI_PREFIXES
        ) or "not-a-real" in doi_lower or "fake" in doi_lower:
            return False, "invalid_or_fake_doi"
        if verify_online:
            online_ok, reason = _verify_doi_online(doi, cand_title, cand_year)
            if online_ok is True:
                return True, reason
            if online_ok is False:
                return False, reason
        else:
            return True, f"doi:{doi}"

    # 2. arXiv ID verification
    arxiv_raw = str(cand.get("arxiv_id") or "").strip()
    if arxiv_raw:
        m = ARXIV_REGEX.match(arxiv_raw)
        if not m:
            return False, "invalid_arxiv_id"
        arxiv_num = m.group(1)
        yymm = arxiv_num[:4]
        yy = int(yymm[:2])
        mm = int(yymm[2:4])
        if mm < 1 or mm > 12 or yy < 7 or yy > 30:
            return False, "invalid_arxiv_yymm"
        if verify_online:
            online_ok, reason = _verify_arxiv_online(arxiv_num, cand_title, cand_year)
            if online_ok is True:
                return True, reason
            if online_ok is False:
                return False, reason
        else:
            return True, f"arxiv:{arxiv_num}"

    # 3. RFC number verification
    rfc_raw = str(cand.get("rfc_number") or "").strip()
    if rfc_raw:
        m = RFC_REGEX.match(rfc_raw)
        if not m:
            return False, "invalid_rfc_number"
        rfc_num = int(m.group(1))
        if rfc_num < 1 or rfc_num > 9800:
            return False, "out_of_range_rfc_number"
        if verify_online:
            online_ok, reason = _verify_rfc_online(str(rfc_num), cand_title, cand_year)
            if online_ok is True:
                return True, reason
            if online_ok is False:
                return False, reason
        else:
            return True, f"rfc:RFC {rfc_num}"

    # 4. ISBN verification (requires valid ISBN-10 or ISBN-13 check digit)
    isbn_raw = str(cand.get("isbn") or "").strip()
    if isbn_raw:
        if not ISBN_REGEX.match(isbn_raw) or not is_valid_isbn_checksum(isbn_raw):
            return False, "invalid_isbn_checksum"
        isbn_clean = re.sub(r"[^0-9Xx]", "", isbn_raw).upper()
        if verify_online:
            online_ok, reason = _verify_isbn_online(isbn_clean, cand_title, cand_year)
            if online_ok is True:
                return True, f"isbn:{isbn_raw}"
            if online_ok is False:
                return False, reason
        else:
            return True, f"isbn:{isbn_raw}"

    # 5. URL verification (rejects .invalid / .example / fake domains; verifies reachability & title)
    url_raw = str(cand.get("url") or "").strip()
    if url_raw:
        if not is_safe_authoritative_url(url_raw):
            return False, "invalid_or_fake_url"
        if verify_online:
            online_ok, reason = _verify_url_online(url_raw, cand_title)
            if online_ok is True:
                return True, reason
            if online_ok is False:
                return False, reason
        else:
            return True, f"url:{url_raw}"

    # 6. Strict full-title verbatim grounding in the user's source notes (minimum 15 alphanumeric chars,
    #    never author surname alone)
    if not require_strict_id and cand_title and grounding_text:
        norm_title = re.sub(r"[^a-z0-9]", "", cand_title.lower())
        norm_grounding = re.sub(r"[^a-z0-9]", "", grounding_text.lower())
        if len(norm_title) >= 15 and norm_title in norm_grounding:
            return True, "grounded_full_title_in_source_notes"

    return False, "unverified"


def infer_bibtex_entry_type(ref: Dict) -> str:
    """Infers the proper BibTeX entry type (@book, @techreport, @article, @inproceedings, @misc)."""
    explicit = str(ref.get("source_type") or ref.get("entry_type") or "").lower().strip()
    venue = str(ref.get("venue") or ref.get("publisher") or "").lower()
    title = str(ref.get("title") or "").lower()

    if (
        explicit == "book"
        or ref.get("isbn")
        or any(
            pub in venue
            for pub in (
                "o'reilly",
                "manning",
                "addison-wesley",
                "pragmatic",
                "springer",
                "press",
                "wiley",
            )
        )
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

    if (
        explicit in ("article", "journal")
        or ref.get("journal")
        or "journal" in venue
        or "transactions on" in venue
    ):
        return "article"

    if explicit in ("misc", "documentation", "specification"):
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
    Removes any unverified/hallucinated citation keys not present in `allowed_keys`
    (even when `allowed_keys` is empty, in which case all \\cite commands are stripped).
    Returns (cleaned_latex, list_of_removed_keys).
    """
    if not latex_text:
        return "", []

    allowed = set(allowed_keys or ())
    removed_keys: List[str] = []

    def _replace_cite(match):
        cmd = match.group(1)
        opt = match.group(2) or ""
        keys_raw = match.group(3)
        keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
        valid = []
        for k in keys:
            if k in allowed:
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
