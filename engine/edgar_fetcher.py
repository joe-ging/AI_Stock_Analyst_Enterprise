"""
edgar_fetcher.py — SEC EDGAR Data Layer for JL Intelligence RAG Engine

Provides:
  - get_cik(ticker)           → SEC CIK string (zero-padded to 10 digits)
  - get_filing_url(cik, year, form_type) → Direct URL to primary HTM document
  - fetch_filing_text(url)    → Clean extracted text (MD&A + Financials sections)
  - chunk_text(text, ...)     → List of chunk dicts with metadata

All HTTP calls use async httpx with EDGAR's required User-Agent header.
"""

import re
import logging
import asyncio
import warnings
import httpx
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from typing import Optional

# Suppress BS4 warning when parsing SEC EDGAR HTM documents with lxml
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger("edgar-fetcher")

# EDGAR requires a descriptive User-Agent or it returns 403
EDGAR_HEADERS = {
    "User-Agent": "JL-Intelligence research@jlintelligence.com",
    "Accept-Encoding": "gzip, deflate",
    "Host": "data.sec.gov",
}

EDGAR_EFTS_HEADERS = {
    "User-Agent": "JL-Intelligence research@jlintelligence.com",
    "Host": "efts.sec.gov",
}

# ─── Ticker → CIK resolution ──────────────────────────────────────────────────

# Cached company ticker JSON from SEC
_TICKER_MAP: Optional[dict] = None

async def _load_ticker_map() -> dict:
    """Load and cache the SEC official ticker → CIK mapping."""
    global _TICKER_MAP
    if _TICKER_MAP is not None:
        return _TICKER_MAP
    url = "https://www.sec.gov/files/company_tickers.json"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers={"User-Agent": "JL-Intelligence research@jlintelligence.com"})
        resp.raise_for_status()
        data = resp.json()
    # data = {"0": {"cik_str": 1234, "ticker": "AAPL", ...}, "1": {...}}
    _TICKER_MAP = {v["ticker"].upper(): str(v["cik_str"]) for v in data.values()}
    logger.info(f"[EDGAR] Loaded {len(_TICKER_MAP)} tickers from SEC")
    return _TICKER_MAP


async def get_cik(ticker: str) -> str:
    """
    Resolve a stock ticker to its SEC CIK (zero-padded to 10 digits).
    
    Args:
        ticker: Stock ticker, e.g. "EDU", "TAL", "AAPL"
    Returns:
        CIK string zero-padded to 10 chars, e.g. "0001255819"
    Raises:
        ValueError if ticker not found
    """
    ticker = ticker.upper().strip()
    ticker_map = await _load_ticker_map()
    if ticker not in ticker_map:
        raise ValueError(f"Ticker '{ticker}' not found in SEC EDGAR database")
    cik_raw = ticker_map[ticker]
    cik_padded = cik_raw.zfill(10)
    logger.info(f"[EDGAR] Resolved {ticker} → CIK {cik_padded}")
    return cik_padded


# ─── Filing index lookup ───────────────────────────────────────────────────────

async def get_filing_url(
    cik: str,
    year: str,
    form_type: str = "20-F"
) -> Optional[str]:
    """
    Find the primary document URL for a given company/year/form-type filing.

    Tries 20-F first (foreign private issuers like EDU, TAL),
    falls back to 10-K for US domestic companies.

    Args:
        cik:       Zero-padded CIK string
        year:      Fiscal year string, e.g. "2024" or "2025"
        form_type: "20-F" or "10-K"
    Returns:
        Full HTTPS URL to the primary HTM filing document, or None
    """
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    async with httpx.AsyncClient(timeout=30.0, headers=EDGAR_HEADERS) as client:
        resp = await client.get(submissions_url)
        resp.raise_for_status()
        subs = resp.json()

    filings = subs.get("filings", {}).get("recent", {})
    forms = filings.get("form", [])
    dates = filings.get("filingDate", [])
    accessions = filings.get("accessionNumber", [])
    primary_docs = filings.get("primaryDocument", [])

    # Filter for the requested form type within the target year
    for i, form in enumerate(forms):
        if form.upper() != form_type.upper():
            continue
        filing_date = dates[i] if i < len(dates) else ""
        if not filing_date.startswith(year):
            continue
        accession = accessions[i].replace("-", "")
        primary_doc = primary_docs[i] if i < len(primary_docs) else ""
        if not primary_doc:
            continue
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{primary_doc}"
        logger.info(f"[EDGAR] Found {form_type} for {year}: {url}")
        return url

    # Try adjacent year (e.g., 2025 annual filed in early 2025 covers FY2024)
    next_year = str(int(year) + 1)
    for i, form in enumerate(forms):
        if form.upper() != form_type.upper():
            continue
        filing_date = dates[i] if i < len(dates) else ""
        if not filing_date.startswith(next_year):
            continue
        accession = accessions[i].replace("-", "")
        primary_doc = primary_docs[i] if i < len(primary_docs) else ""
        if not primary_doc:
            continue
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{primary_doc}"
        logger.info(f"[EDGAR] Found {form_type} for ~{year} (filed {filing_date}): {url}")
        return url

    logger.warning(f"[EDGAR] No {form_type} found for CIK {cik} year {year}")
    return None


# ─── HTML text extraction ──────────────────────────────────────────────────────

# Sections we care about in 20-F / 10-K
_SECTION_PATTERNS = [
    # MD&A
    re.compile(r"item\s+5[.\s]", re.IGNORECASE),
    re.compile(r"management.{0,10}discussion.{0,10}analysis", re.IGNORECASE),
    re.compile(r"item\s+7[.\s]", re.IGNORECASE),
    # Financial Statements
    re.compile(r"item\s+8[.\s]", re.IGNORECASE),
    re.compile(r"financial\s+statements", re.IGNORECASE),
    re.compile(r"consolidated\s+statements?\s+of", re.IGNORECASE),
    # Results of Operations
    re.compile(r"results?\s+of\s+operations?", re.IGNORECASE),
    # Risk Factors
    re.compile(r"item\s+3[.\s]", re.IGNORECASE),
    re.compile(r"key\s+information", re.IGNORECASE),
]

async def fetch_filing_text(url: str) -> str:
    """
    Download and extract full clean text from an SEC HTM filing.
    
    Returns the complete plain text of the document.
    """
    headers = {
        "User-Agent": "JL-Intelligence research@jlintelligence.com",
        "Accept": "text/html,application/xhtml+xml",
    }
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = resp.text

    soup = BeautifulSoup(html, "lxml")

    # Remove scripts, styles, and hidden elements
    for tag in soup(["script", "style", "head", "meta", "link"]):
        tag.decompose()

    # Get all text blocks
    full_text = soup.get_text(separator="\n", strip=True)

    # Clean up excessive whitespace
    full_text = re.sub(r"\n{3,}", "\n\n", full_text)
    full_text = re.sub(r" {2,}", " ", full_text)

    logger.info(f"[EDGAR] Extracted FULL filing text: {len(full_text):,} chars from {url}")
    return full_text


# ─── Text chunking ─────────────────────────────────────────────────────────────

def chunk_text(
    text: str,
    doc_id: str,
    ticker: str,
    year: str,
    chunk_size: int = 1000,
    overlap: int = 100
) -> list[dict]:
    """
    Split filing text into overlapping chunks with metadata tags.

    Args:
        text:       Full filing text
        doc_id:     Unique document identifier (e.g., "EDU_2024_20F")
        ticker:     Stock ticker
        year:       Fiscal year string
        chunk_size: Target chars per chunk
        overlap:    Overlap chars between adjacent chunks
    Returns:
        List of {"text": str, "doc_id": str, "ticker": str, "year": str}
    """
    chunks = []
    start = 0
    total = len(text)
    chunk_index = 0

    while start < total:
        end = min(start + chunk_size, total)
        chunk_text_content = text[start:end]

        # Add source header to each chunk for citation clarity
        header = f"[Source: {ticker} {year} Annual Report | Chunk {chunk_index + 1}]\n"
        chunks.append({
            "text": header + chunk_text_content,
            "doc_id": doc_id,
            "ticker": ticker,
            "year": year,
        })

        chunk_index += 1
        start += chunk_size - overlap

    logger.info(f"[EDGAR] Chunked '{doc_id}' → {len(chunks)} chunks ({chunk_size} chars, {overlap} overlap)")
    return chunks


# ─── High-level convenience function ──────────────────────────────────────────

async def fetch_edgar_chunks(
    ticker: str,
    years: list[str],
    form_type: str = "20-F"
) -> dict[str, list[dict]]:
    """
    Full pipeline: ticker + years → chunked text dicts ready for Milvus ingestion.

    Args:
        ticker:    Stock ticker, e.g. "EDU"
        years:     List of fiscal year strings, e.g. ["2024", "2025"]
        form_type: Default "20-F" for foreign private issuers; "10-K" for US companies
    Returns:
        Dict mapping year → list of chunk dicts
        e.g., {"2024": [...chunks...], "2025": [...chunks...]}
    """
    results = {}
    cik = await get_cik(ticker)

    for year in years:
        logger.info(f"[EDGAR] Fetching {ticker} ({form_type}) for FY{year}...")
        try:
            url = await get_filing_url(cik, year, form_type)
            if not url:
                # Try fallback form type
                fallback = "10-K" if form_type == "20-F" else "20-F"
                logger.info(f"[EDGAR] Trying fallback form type {fallback}...")
                url = await get_filing_url(cik, year, fallback)

            if not url:
                logger.error(f"[EDGAR] No filing found for {ticker} {year}")
                results[year] = []
                continue

            text = await fetch_filing_text(url)
            doc_id = f"{ticker}_{year}_{form_type.replace('-', '')}"
            chunks = chunk_text(text, doc_id=doc_id, ticker=ticker, year=year)
            results[year] = chunks
            logger.info(f"[EDGAR] ✅ {ticker} {year}: {len(chunks)} chunks ready")

        except Exception as e:
            logger.error(f"[EDGAR] Failed to fetch {ticker} {year}: {e}")
            results[year] = []

    return results


# ─── CLI test runner ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    async def _test():
        ticker = sys.argv[1] if len(sys.argv) > 1 else "EDU"
        years = sys.argv[2].split(",") if len(sys.argv) > 2 else ["2024"]
        print(f"\n🔍 Testing EDGAR fetch for {ticker} | years: {years}")

        cik = await get_cik(ticker)
        print(f"✅ CIK: {cik}")

        for year in years:
            url = await get_filing_url(cik, year)
            print(f"✅ Filing URL ({year}): {url}")
            if url:
                text = await fetch_filing_text(url)
                print(f"✅ Extracted text: {len(text):,} chars")
                chunks = chunk_text(text, f"{ticker}_{year}", ticker, year)
                print(f"✅ Chunks: {len(chunks)}")
                print(f"\nSample chunk 0:\n{chunks[0]['text'][:400]}\n")

    asyncio.run(_test())
