import io
import re
import zipfile
from datetime import datetime

import fitz
import streamlit as st

st.set_page_config(page_title="Invoice Splitter", layout="wide")
st.title("Invoice Splitter")

# -----------------------
# HELPERS
# -----------------------

def clean_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r'[\\/:*?"<>|]+', "_", value)
    return value[:150] or "UNKNOWN"


def read_pdf_text(file_bytes: bytes) -> str:
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text


def extract_business(text: str) -> str:
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]
    full_text = " ".join(lines).lower()

    # hard match
    if "canon australia" in full_text:
        return "CANON AUSTRALIA PTY LTD"
    if "kyocera" in full_text:
        return "KYOCERA DOCUMENT SOLUTIONS AUSTRALIA PTY LTD"
    if "bbc digital" in full_text:
        return "BBC DIGITAL"
    if "kk technical services" in full_text:
        return "KK TECHNICAL SERVICES PTY LTD"
    if "that marketing co" in full_text:
        return "THAT MARKETING CO"

    # fallback
    for line in lines[:20]:
        if "pty ltd" in line.lower():
            return clean_name(line).upper()

    return "UNKNOWN BUSINESS"


def extract_abn(text: str) -> str:
    match = re.search(r"(?:ABN|A\.B\.N\.?)\s*[:\-]?\s*(?:ABN\s*[:\-]?\s*)?(\d[\d\s]{9,20}\d)", text, re.IGNORECASE)
    if match:
        digits = re.sub(r"\D", "", match.group(1))
        if len(digits) == 11:
            return digits

    matches = re.findall(r"\b\d{2}\s?\d{3}\s?\d{3}\s?\d{3}\b", text)
    for m in matches:
        digits = re.sub(r"\D", "", m)
        if len(digits) == 11:
            return digits

    return "UNKNOWNABN"


def parse_date(text: str) -> str:
    patterns = [
        r"(\d{1,2}/\d{1,2}/\d{4})",
        r"(\d{1,2}-[A-Za-z]{3}-\d{2,4})",
        r"(\d{4}-\d{2}-\d{2})",
    ]

    for p in patterns:
        match = re.search(p, text)
        if match:
            raw = match.group(1)
            for fmt in ("%d/%m/%Y", "%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d"):
                try:
                    return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                except:
                    pass

    return "UNKNOWN-DATE"


def extract_invoice_number(text: str) -> str:
    match = re.search(r"(?:Invoice\s*(?:No|#)|Tax Invoice No)\s*[:\-]?\s*([A-Z0-9\-]+)", text, re.IGNORECASE)
    if match:
        return match.group(1)

    return "UNKNOWN-INVOICE"


# -----------------------
# UI
# -----------------------

uploaded_files = st.file_uploader("Upload PDF invoices", type=["pdf"], accept_multiple_files=True)

if uploaded_files:

    zip_buffer = io.BytesIO()

    seen = set()
    results = []
    duplicates = []

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:

        for uploaded in uploaded_files:

            file_bytes = uploaded.read()
            text = read_pdf_text(file_bytes)

            business = extract_business(text)
            abn = extract_abn(text)
            date = parse_date(text)
            invoice_number = extract_invoice_number(text)

            key = f"{business}|{abn}|{date}|{invoice_number}"

            if key in seen:
                duplicates.append({
                    "business": business,
                    "abn": abn,
                    "date": date,
                    "invoice_number": invoice_number,
                })
                continue

            seen.add(key)

            filename = clean_name(f"{business} - {abn} - {date}.pdf")

            zf.writestr(filename, file_bytes)

            results.append({
                "filename": filename,
                "business": business,
                "abn": abn,
                "date": date,
                "invoice_number": invoice_number,
                "pdf": file_bytes,
            })

    zip_buffer.seek(0)

    # -----------------------
    # RESULTS
    # -----------------------

    st.subheader("Invoices")

    for r in results:
        with st.expander(r["filename"]):
            st.write(f"Business: {r['business']}")
            st.write(f"ABN: {r['abn']}")
            st.write(f"Date: {r['date']}")
            st.write(f"Invoice #: {r['invoice_number']}")

            st.download_button(
                "Download PDF",
                r["pdf"],
                file_name=r["filename"],
                mime="application/pdf"
            )

    # -----------------------
    # SUMMARY
    # -----------------------

    st.subheader("Summary")

    st.write(f"Total uploaded: {len(uploaded_files)}")
    st.write(f"Unique invoices: {len(results)}")
    st.write(f"Duplicates removed: {len(duplicates)}")

    if duplicates:
        st.subheader("Duplicate invoices")
        st.dataframe(duplicates)

    # -----------------------
    # ZIP DOWNLOAD
    # -----------------------

    st.download_button(
        "Download All (ZIP)",
        zip_buffer.getvalue(),
        "invoices.zip"
    )
