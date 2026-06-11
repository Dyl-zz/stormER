"""EDGAR / SEC node — the flagship runner (CLAUDE.md §9.1).

All filing-shaped mess (HTML stripping, Item-section slicing, EDGAR API quirks)
is contained here. Nothing in this file may leak into the framework engine.

Trust: citable research — re-runnable and re-fetchable from CIK + accession.
"""

import html as html_lib
import re

import httpx
from pydantic import BaseModel, Field, field_validator

from app.config import Settings
from app.spine.runner import NodeRunner, RetentionPolicy, RunnerError, StructuredResult

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{doc}"

# Section slicing: (start patterns, stop patterns) over Item headings.
# 10-K items are inconsistently formatted; this is deliberately regex-tolerant
# and deliberately honest — the output is an extraction, not an analysis.
SECTIONS: dict[str, tuple[str, str]] = {
    "business": (r"item\s+1\s*[\.\:\-–]?\s*business", r"item\s+1a\s*[\.\:\-–]"),
    "risk_factors": (r"item\s+1a\s*[\.\:\-–]?\s*risk\s+factors", r"item\s+1b\s*[\.\:\-–]"),
    "mdna": (
        r"item\s+7\s*[\.\:\-–]?\s*management.{0,5}s\s+discussion",
        r"item\s+7a\s*[\.\:\-–]",
    ),
    "financial_statements": (
        r"item\s+8\s*[\.\:\-–]?\s*financial\s+statements",
        r"item\s+9\s*[\.\:\-–]",
    ),
}

GENERATED_TEXT_LIMIT = 6000


class EdgarParams(BaseModel):
    """The company is a parameter slot (§8.2) — a binding, never node identity."""

    cik: str = Field(json_schema_extra={"binding": True})
    form_type: str = "10-K"
    section: str | None = None
    # Inclusive ISO-date window over the filing date, e.g. ["2021-01-01", "2023-12-31"].
    date_range: tuple[str, str] | None = None

    @field_validator("cik")
    @classmethod
    def _digits(cls, v: str) -> str:
        v = v.strip().lstrip("0") or "0"
        if not v.isdigit():
            raise ValueError("cik must be numeric")
        return v

    @field_validator("section")
    @classmethod
    def _known_section(cls, v: str | None) -> str | None:
        if v is not None and v not in SECTIONS:
            raise ValueError(f"section must be one of {sorted(SECTIONS)}")
        return v


class EdgarInputKeys(BaseModel):
    """The durable, re-fetchable identity (§6): enough to pull the exact filing again."""

    cik: str
    accession_no: str
    form_type: str
    filing_date: str


def _strip_html(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = re.sub(r"(?i)</(p|div|tr|table|h[1-6]|li|br)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _extract_section(text: str, section: str) -> str | None:
    start_pat, stop_pat = SECTIONS[section]
    # Take the LAST start match: the first occurrences are usually the table of contents.
    starts = list(re.finditer(start_pat, text, re.IGNORECASE))
    if not starts:
        return None
    start, search_from = starts[-1].start(), starts[-1].end()
    stop_match = re.search(stop_pat, text[search_from:], re.IGNORECASE)
    end = search_from + stop_match.start() if stop_match else len(text)
    return text[start:end].strip()


class EdgarRunner(NodeRunner):
    node_type = "edgar"
    params_model = EdgarParams
    input_keys_model = EdgarInputKeys
    timeout_seconds = 180  # EDGAR is slow; be generous (§10)
    trust_label = "citable research"
    refreshable = True
    # Aggressive draft sweep: fully re-fetchable from cik + accession_no (§11).
    retention = RetentionPolicy(
        draft_blob_ttl_days=7, milestone_blob_ttl_days=365, swept_state="swept"
    )

    def __init__(self, settings: Settings) -> None:
        self._headers = {"User-Agent": settings.edgar_user_agent}

    def format_input_keys(self, input_keys: dict) -> str:
        keys = EdgarInputKeys.model_validate(input_keys)
        return (
            f"CIK {keys.cik} | {keys.form_type} filed {keys.filing_date}"
            f" | accession {keys.accession_no}"
        )

    async def run(self, parameters: dict, resolved_inputs: dict) -> tuple[StructuredResult, bytes]:
        params = EdgarParams.model_validate(parameters)
        async with httpx.AsyncClient(headers=self._headers, timeout=60) as client:
            filing = await self._latest_filing(client, params)
            document_text = await self._fetch_document(client, params, filing)

        if params.section:
            section_text = _extract_section(document_text, params.section)
            if section_text is None:
                raise RunnerError(
                    code="section_not_found",
                    message=(
                        f"could not locate section {params.section!r} in"
                        f" {filing['form']} {filing['accessionNumber']}"
                    ),
                    retryable=False,
                )
            finding = section_text
            heading = f"{params.section} — {filing['form']} filed {filing['filingDate']}"
        else:
            finding = document_text
            heading = f"{filing['form']} filed {filing['filingDate']} (full filing excerpt)"

        if len(finding) > GENERATED_TEXT_LIMIT:
            finding = finding[:GENERATED_TEXT_LIMIT] + "\n[... truncated; full text in raw data]"

        input_keys = EdgarInputKeys(
            cik=params.cik,
            accession_no=filing["accessionNumber"],
            form_type=filing["form"],
            filing_date=filing["filingDate"],
        )
        result = StructuredResult(
            generated_text=f"[{heading}]\n\n{finding}",
            input_keys=input_keys.model_dump(),
            raw_mime_type="text/plain",
        )
        return result, document_text.encode("utf-8")

    async def _latest_filing(self, client: httpx.AsyncClient, params: EdgarParams) -> dict:
        url = SUBMISSIONS_URL.format(cik10=params.cik.zfill(10))
        response = await client.get(url)
        if response.status_code == 404:
            raise RunnerError(code="cik_not_found", message=f"no EDGAR company for CIK {params.cik}")
        response.raise_for_status()
        recent = response.json().get("filings", {}).get("recent", {})

        candidates = []
        for form, accession, date, doc in zip(
            recent.get("form", []),
            recent.get("accessionNumber", []),
            recent.get("filingDate", []),
            recent.get("primaryDocument", []),
        ):
            if form != params.form_type:
                continue
            if params.date_range and not (params.date_range[0] <= date <= params.date_range[1]):
                continue
            candidates.append(
                {"form": form, "accessionNumber": accession, "filingDate": date, "doc": doc}
            )
        if not candidates:
            raise RunnerError(
                code="no_matching_filing",
                message=f"no {params.form_type} filing for CIK {params.cik}"
                + (f" in {params.date_range}" if params.date_range else ""),
                retryable=False,
            )
        return max(candidates, key=lambda f: f["filingDate"])

    async def _fetch_document(
        self, client: httpx.AsyncClient, params: EdgarParams, filing: dict
    ) -> str:
        url = ARCHIVE_URL.format(
            cik_int=int(params.cik),
            accession_nodash=filing["accessionNumber"].replace("-", ""),
            doc=filing["doc"],
        )
        response = await client.get(url)
        response.raise_for_status()
        return _strip_html(response.text)
