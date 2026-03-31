import hashlib
import io
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import fitz  # PyMuPDF
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


APP_TITLE = "Invoice Splitter Portal"
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "runs"
OUTPUT_DIR.mkdir(exist_ok=True)

app = FastAPI(title=APP_TITLE)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

ABN_REGEX = re.compile(r"\b(?:ABN|A\.B\.N\.?)[^\d]{0,10}(\d[\d\s]{9,20}\d)\b", re.IGNORECASE)
PAGE_X_OF_Y_REGEX = re.compile(r"\bPage[:\s]*(\d{1,3})\s*(?:of|/)\s*(\d{1,3})\b", re.IGNORECASE)
DATE_REGEXES = [
    re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b"),
    re.compile(r"\b(\d{1,2}-[A-Za-z]{3}-\d{2,4})\b"),
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
]
INVOICE_NO_REGEXES = [
    re.compile(r"\b(?:Invoice\s*(?:No\.?|#|Number)|Tax\s*Invoice\s*(?:No\.?|#|Number)|Inv(?:oice)?\s*#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\/_]*)\b", re.IGNORECASE),
    re.compile(r"\bTax\s+Invoice\s+([A-Z0-9][A-Z0-9\-\/_]{3,})\b", re.IGNORECASE),
]
BUSINESS_BLOCKERS = {
    "tax invoice",
    "invoice",
    "invoice no",
    "invoice number",
    "invoice date",
    "due date",
    "page",
    "description",
    "subtotal",
    "total",
    "amount due",
    "bill to",
    "invoice to",
    "customer",
}


def clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def safe_filename(value: str) -> str:
    value = clean_spaces(value)
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    return value[:180] or "Unknown"


def digits_only(value: str) -> str:
    return re.sub(r"\D", "", value)


@dataclass
class PageInfo:
    page_index: int
    text: str
    business_name: Optional[str]
    abn: Optional[str]
    invoice_number: Optional[str]
    invoice_date: Optional[str]
    page_marker: Optional[Tuple[int, int]]


@dataclass
class InvoiceGroup:
    business_name: str
    abn: str
    invoice_number: str
    invoice_date: str
    start_page: int
    end_page: int
    source_filename: str
    dedupe_key: str


class InvoiceExtractor:
    def extract_page_texts(self, pdf_bytes: bytes) -> List[str]:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = []
        for page in doc:
            pages.append(page.get_text("text") or "")
        doc.close()
        return pages

    def extract_abn(self, text: str) -> Optional[str]:
        match = ABN_REGEX.search(text)
        if not match:
            return None
        abn = digits_only(match.group(1))
        return abn if len(abn) == 11 else None

    def extract_page_marker(self, text: str) -> Optional[Tuple[int, int]]:
        match = PAGE_X_OF_Y_REGEX.search(text)
        if not match:
            return None
        current_page = int(match.group(1))
        total_pages = int(match.group(2))
        if 1 <= current_page <= total_pages <= 500:
            return current_page, total_pages
        return None

    def extract_invoice_number(self, text: str) -> Optional[str]:
        short = text[:5000]
        for pattern in INVOICE_NO_REGEXES:
            match = pattern.search(short)
            if match:
                return safe_filename(match.group(1)).replace(" ", "")
        return None

    def extract_invoice_date(self, text: str) -> Optional[str]:
        short = text[:5000]
        anchors = [
            r"Invoice Date\s*[:\-]?\s*",
            r"Date\s*[:\-]?\s*",
        ]
        for anchor in anchors:
            for date_pattern in DATE_REGEXES:
                match = re.search(anchor + date_pattern.pattern[2:-2], short, flags=re.IGNORECASE)
                if match:
                    parsed = self._parse_date(match.group(1))
                    if parsed:
                        return parsed
        for date_pattern in DATE_REGEXES:
            match = date_pattern.search(short)
            if match:
                parsed = self._parse_date(match.group(1))
                if parsed:
                    return parsed
        return None

    def _parse_date(self, value: str) -> Optional[str]:
        value = value.strip()
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None

    def extract_business_name(self, text: str) -> Optional[str]:
        lines = [clean_spaces(line) for line in text.splitlines() if clean_spaces(line)]
        best = []
        for line in lines[:20]:
            lower = line.lower()
            if any(blocker in lower for blocker in BUSINESS_BLOCKERS):
                continue
            if "abn" in lower:
                continue
            if len(line) < 3 or len(line) > 90:
                continue
            if re.search(r"\d{3,}", line):
                continue
            if re.search(r"(street|road|vic|nsw|qld|australia|phone|fax|email)", lower):
                continue
            best.append(line)
        if best:
            return safe_filename(best[0]).upper()
        return None

    def analyze(self, pdf_bytes: bytes) -> List[PageInfo]:
        pages = self.extract_page_texts(pdf_bytes)
        return [
            PageInfo(
                page_index=i,
                text=text,
                business_name=self.extract_business_name(text),
                abn=self.extract_abn(text),
                invoice_number=self.extract_invoice_number(text),
                invoice_date=self.extract_invoice_date(text),
                page_marker=self.extract_page_marker(text),
            )
            for i, text in enumerate(pages)
        ]

    def group_pages(self, analyses: List[PageInfo], source_filename: str) -> List[InvoiceGroup]:
        groups: List[InvoiceGroup] = []
        current_start = 0

        def build_group(start: int, end: int) -> InvoiceGroup:
            pages = analyses[start:end + 1]
            business_name = next((p.business_name for p in pages if p.business_name), "UNKNOWN BUSINESS")
            abn = next((p.abn for p in pages if p.abn), "UNKNOWNABN")
            invoice_number = next((p.invoice_number for p in pages if p.invoice_number), f"UNKNOWN-{start+1}")
            invoice_date = next((p.invoice_date for p in pages if p.invoice_date), "UNKNOWN-DATE")
            dedupe_key = f"{abn}|{invoice_number}|{invoice_date}"
            return InvoiceGroup(
                business_name=safe_filename(business_name).upper(),
                abn=abn,
                invoice_number=safe_filename(invoice_number).replace(" ", ""),
                invoice_date=invoice_date,
                start_page=start,
                end_page=end,
                source_filename=source_filename,
                dedupe_key=dedupe_key,
            )

        for i in range(1, len(analyses)):
            prev = analyses[i - 1]
            cur = analyses[i]

            prev_key = (prev.abn, prev.invoice_number, prev.invoice_date)
            cur_key = (cur.abn, cur.invoice_number, cur.invoice_date)

            starts_new = False

            if cur.invoice_number and prev.invoice_number and cur.invoice_number != prev.invoice_number:
                starts_new = True
            elif cur.abn and prev.abn and cur.abn != prev.abn:
                starts_new = True
            elif cur.invoice_date and prev.invoice_date and cur.invoice_date != prev.invoice_date and cur.invoice_number != prev.invoice_number:
                starts_new = True
            elif cur.page_marker and cur.page_marker[0] == 1:
                if prev.page_marker and prev.page_marker[0] != 0:
                    starts_new = True
                elif cur_key != prev_key:
                    starts_new = True

            if starts_new:
                groups.append(build_group(current_start, i - 1))
                current_start = i

        if analyses:
            groups.append(build_group(current_start, len(analyses) - 1))

        return groups

    def write_group_pdf(self, pdf_bytes: bytes, group: InvoiceGroup, out_path: Path) -> None:
        src = fitz.open(stream=pdf_bytes, filetype="pdf")
        new_doc = fitz.open()
        new_doc.insert_pdf(src, from_page=group.start_page, to_page=group.end_page)
        new_doc.save(out_path)
        new_doc.close()
        src.close()


extractor = InvoiceExtractor()


def fingerprint_pdf(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "title": APP_TITLE})


@app.post("/process")
async def process_invoices(request: Request, files: List[UploadFile] = File(...)):
    run_id = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    run_dir = OUTPUT_DIR / run_id
    split_dir = run_dir / "split"
    split_dir.mkdir(parents=True, exist_ok=True)

    results = []
    seen_invoice_keys = set()
    seen_file_hashes = set()

    for upload in files:
        data = await upload.read()
        if not data:
            continue

        file_hash = fingerprint_pdf(data)
        if file_hash in seen_file_hashes:
            continue
        seen_file_hashes.add(file_hash)

        analyses = extractor.analyze(data)
        groups = extractor.group_pages(analyses, upload.filename)

        for group in groups:
            if group.dedupe_key in seen_invoice_keys:
                continue
            seen_invoice_keys.add(group.dedupe_key)

            filename = f"{group.business_name} - {group.abn} - {group.invoice_date}.pdf"
            filename = safe_filename(filename)
            out_path = split_dir / filename
            extractor.write_group_pdf(data, group, out_path)

            results.append(
                {
                    "filename": filename,
                    "business_name": group.business_name,
                    "abn": group.abn,
                    "invoice_date": group.invoice_date,
                    "invoice_number": group.invoice_number,
                    "pages": f"{group.start_page + 1}-{group.end_page + 1}",
                    "source_file": upload.filename,
                }
            )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(split_dir.glob("*.pdf")):
            zf.write(file_path, arcname=file_path.name)
    zip_buffer.seek(0)

    if not results:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "title": APP_TITLE,
                "error": "No invoices were created. Try text-based PDFs first, or add OCR support for scanned PDFs.",
            },
        )

    request.session = {"results": results}
    headers = {"Content-Disposition": f'attachment; filename="split_invoices_{run_id}.zip"'}
    return StreamingResponse(zip_buffer, media_type="application/zip", headers=headers)
