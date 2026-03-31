import io
import re
import zipfile
from datetime import datetime

import fitz
import streamlit as st

st.set_page_config(page_title="Invoice Splitter", layout="wide")
st.title("Invoice Splitter")
st.write("Upload PDF invoices. The app will name each file as Business Name - ABN - Invoice Date.pdf and remove duplicates.")

ABN_REGEX = re.compile(r"\b(?:ABN|A\.B\.N\.?)[^\d]{0,10}(\d[\d\s]{9,20}\d)\b", re.IGNORECASE)
DATE_REGEXES = [
    re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b"),
    re.compile(r"\b(\d{1,2}-[A-Za-z]{3}-\d{2,4})\b"),
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
]

def clean_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r'[\\/:*?"<>|]+', "_", value)
    return value[:150] or "UNKNOWN"

def parse_date(text: str) -> str:
    short = text[:5000]
    for pattern in DATE_REGEXES:
        match = pattern.search(short)
        if match:
            raw = match.group(1).strip()
            for fmt in ("%d/%m/%Y", "%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d"):
                try:
                    return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                except ValueError:
                    pass
    return "UNKNOWN-DATE"

def extract_abn(text: str) -> str:
    match = ABN_REGEX.search(text)
    if not match:
        return "UNKNOWNABN"
    digits = re.sub(r"\D", "", match.group(1))
    return digits if len(digits) == 11 else "UNKNOWNABN"

def extract_business(text: str) -> str:
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]
    blockers = [
        "invoice", "tax invoice", "invoice date", "due date", "page",
        "description", "subtotal", "total", "amount due", "abn"
    ]
    for line in lines[:20]:
        lower = line.lower()
        if any(b in lower for b in blockers):
            continue
        if re.search(r"\d{3,}", line):
            continue
        if re.search(r"(street|road|vic|nsw|qld|australia|phone|email)", lower):
            continue
        return clean_name(line).upper()
    return "UNKNOWN BUSINESS"

def read_pdf_text(file_bytes: bytes) -> str:
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    parts = []
    for page in doc:
        parts.append(page.get_text("text") or "")
    doc.close()
    return "\n".join(parts)

uploaded_files = st.file_uploader("Upload PDF files", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    zip_buffer = io.BytesIO()
    seen = set()
    results = []

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for uploaded in uploaded_files:
            file_bytes = uploaded.read()
            text = read_pdf_text(file_bytes)

            business = extract_business(text)
            abn = extract_abn(text)
            invoice_date = parse_date(text)

            dedupe_key = f"{business}|{abn}|{invoice_date}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            filename = clean_name(f"{business} - {abn} - {invoice_date}.pdf")
            zf.writestr(filename, file_bytes)
            results.append({
                "filename": filename,
                "business": business,
                "abn": abn,
                "date": invoice_date,
            })

    zip_buffer.seek(0)

    st.subheader("Files created")
    st.dataframe(results, use_container_width=True)

    st.download_button(
        label="Download ZIP",
        data=zip_buffer.getvalue(),
        file_name="processed_invoices.zip",
        mime="application/zip",
    )
