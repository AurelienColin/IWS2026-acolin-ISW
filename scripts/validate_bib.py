#!/usr/bin/env python3
# Validate .bib files: DOI presence, key format, resolution, and field accuracy.
from __future__ import annotations

import json
import re
import sys
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

FIELD_PATTERN: re.Pattern[str] = re.compile(
    r"(\w+)\s*=\s*\{([^}]*)\}", re.IGNORECASE
)

REDIRECT_STATUS_CODES: tuple[int, ...] = (301, 302, 303, 307, 308)
HTTP_TIMEOUT_SECONDS: int = 15

CHECKED_FIELDS: tuple[str, ...] = ("title", "author", "year", "journal", "volume", "pages")

ENTRY_HEADER_PATTERN: re.Pattern[str] = re.compile(r"@(\w+)\{([^,]+),")


def extract_entries(bib_text: str) -> list[dict[str, str]]:
    bib_text = bib_text.replace("\r\n", "\n")
    entries: list[dict[str, str]] = []
    position = 0
    while position < len(bib_text):
        header_match = ENTRY_HEADER_PATTERN.search(bib_text, position)
        if header_match is None:
            break
        entry_type = header_match.group(1)
        citation_key = header_match.group(2).strip()
        body_start = header_match.end()
        brace_depth = 1
        index = body_start
        while index < len(bib_text) and brace_depth > 0:
            if bib_text[index] == "{":
                brace_depth += 1
            elif bib_text[index] == "}":
                brace_depth -= 1
            index += 1
        body = bib_text[body_start:index - 1]
        fields: dict[str, str] = {"_type": entry_type, "_key": citation_key}
        for field_match in FIELD_PATTERN.finditer(body):
            fields[field_match.group(1).lower()] = field_match.group(2).strip()
        entries.append(fields)
        position = index
    return entries


def check_doi_present(entries: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    for entry in entries:
        if "doi" not in entry:
            errors.append(f"[{entry['_key']}] Missing DOI field")
    return errors


def check_key_is_lowercase_doi(entries: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    for entry in entries:
        doi = entry.get("doi")
        if doi is None:
            continue
        expected_key = doi.lower()
        if entry["_key"] != expected_key:
            errors.append(
                f"[{entry['_key']}] Citation key must be lowercase DOI: "
                f"expected '{expected_key}'"
            )
    return errors


def resolve_doi(doi: str) -> tuple[bool, str]:
    url_path = "/" + quote(doi, safe="/:.()-")
    connection = HTTPSConnection("doi.org", timeout=HTTP_TIMEOUT_SECONDS)
    try:
        connection.request("HEAD", url_path)
        response = connection.getresponse()
        is_valid = response.status in REDIRECT_STATUS_CODES
        return is_valid, f"HTTP {response.status}"
    except (OSError, TimeoutError, HTTPException) as error:
        return False, f"Network error: {error}"
    finally:
        connection.close()


def check_doi_resolves(entries: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    for entry in entries:
        doi = entry.get("doi")
        if doi is None:
            continue
        is_valid, detail = resolve_doi(doi)
        if not is_valid:
            errors.append(f"[{entry['_key']}] DOI does not resolve: {doi} ({detail})")
        else:
            print(f"  OK: {doi}")
    return errors


def fetch_crossref_metadata(doi: str) -> tuple[dict[str, str] | None, str]:
    url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
    request = Request(url, headers={"Accept": "application/json"})
    try:
        response = urlopen(request, timeout=HTTP_TIMEOUT_SECONDS)
        data = json.loads(response.read().decode("utf-8"))
        message = data.get("message", {})
        metadata: dict[str, str] = {}
        title_list = message.get("title", [])
        if title_list:
            metadata["title"] = title_list[0]
        authors = message.get("author", [])
        if authors:
            author_names = [
                f"{a.get('family', '')}, {a.get('given', '')}" for a in authors
            ]
            metadata["author"] = " and ".join(author_names)
        published = message.get("published-print", message.get("published-online", {}))
        date_parts = published.get("date-parts", [[]])
        if date_parts and date_parts[0]:
            metadata["year"] = str(date_parts[0][0])
        container = message.get("container-title", [])
        if container:
            metadata["journal"] = container[0]
        metadata["volume"] = message.get("volume", "")
        metadata["pages"] = message.get("page", "")
        return metadata, "ok"
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        return None, f"Crossref fetch failed: {error}"


def normalize_for_comparison(text: str) -> str:
    text = re.sub(r"[{}\\\"\']", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def check_fields_match_doi(entries: list[dict[str, str]]) -> list[str]:
    warnings: list[str] = []
    for entry in entries:
        doi = entry.get("doi")
        if doi is None:
            continue
        crossref, error_detail = fetch_crossref_metadata(doi)
        if crossref is None:
            warnings.append(
                f"[{entry['_key']}] {error_detail} for {doi}"
            )
            continue
        for field in CHECKED_FIELDS:
            bib_value = entry.get(field, "")
            crossref_value = crossref.get(field, "")
            if not crossref_value:
                continue
            bib_normalized = normalize_for_comparison(bib_value)
            crossref_normalized = normalize_for_comparison(crossref_value)
            if not bib_normalized and crossref_normalized:
                warnings.append(
                    f"[{entry['_key']}] Missing field '{field}' "
                    f"(Crossref: {crossref_value[:60]})"
                )
            elif bib_normalized != crossref_normalized:
                warnings.append(
                    f"[{entry['_key']}] Field '{field}' mismatch: "
                    f"bib='{bib_value[:40]}' vs Crossref='{crossref_value[:40]}'"
                )
    return warnings


def main() -> int:
    bib_files = sorted(Path(".").glob("**/*.bib"))
    if not bib_files:
        print("No .bib files found -- skipping DOI validation.")
        return 0

    all_errors: list[str] = []
    all_warnings: list[str] = []

    for bib_path in bib_files:
        print(f"\n{'=' * 60}")
        print(f"Validating: {bib_path}")
        print("=" * 60)

        bib_text = bib_path.read_text(encoding="utf-8")
        entries = extract_entries(bib_text)
        print(f"Found {len(entries)} entries")

        if not entries:
            continue

        print("\n[1/4] Checking DOI presence...")
        all_errors.extend(check_doi_present(entries))

        print("[2/4] Checking citation key = lowercase DOI...")
        all_errors.extend(check_key_is_lowercase_doi(entries))

        print("[3/4] Checking DOI resolution...")
        all_errors.extend(check_doi_resolves(entries))

        print("[4/4] Checking fields match Crossref metadata...")
        all_warnings.extend(check_fields_match_doi(entries))

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print("=" * 60)

    if all_errors:
        print(f"\nERRORS ({len(all_errors)}):")
        for error in all_errors:
            print(f"  x {error}")

    if all_warnings:
        print(f"\nWARNINGS ({len(all_warnings)}):")
        for warning in all_warnings:
            print(f"  ! {warning}")

    if not all_errors and not all_warnings:
        print("All checks passed.")

    if all_errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
