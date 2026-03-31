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
    text_lower = text.lower()

    if "canon australia" in text_lower:
        return "CANON AUSTRALIA PTY LTD"
    if "kyocera" in text_lower:
        return "KYOCERA DOCUMENT SOLUTIONS AUSTRALIA PTY LTD"
    if "bbc digital" in text_lower:
        return "BBC DIGITAL"
    if "kk technical services" in text_lower:
        return "KK TECHNICAL SERVICES PTY LTD"
    if "that marketing co" in text_lower:
        return "THAT MARKETING CO"

    lines = text.splitlines()
    for line in lines[:30]:
        if "pty ltd" in line.lower():
            return clean_name(line).upper()

    return "UNKNOWN BUSINESS"


def extract_abn(text: str) -> str:
    match = re.search(r"(?:ABN)\s*[:\-]?\s*(?:ABN\s*[:\-]?\s*)?(\d[\d\s]{9,20}\d)", text, re.IGNORECASE)
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


def build_zip(results):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in results:
            zf.writestr(r["filename"], r["pdf"])
    buffer.seek(0)
    return buffer.getvalue()


# -----------------------
# SESSION STATE
# -----------------------

if "results" not in st.session_state:
    st.session_state.results = []

if "duplicates" not in st.session_state:
    st.session_state.duplicates = []

if "total_uploaded" not in st.session_state:
    st.session_state.total_uploaded = 0


# -----------------------
# UPLOAD
# -----------------------

uploaded_files = st.file_uploader("Upload PDF invoices", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    seen = set()
    results = []
    duplicates = []

    for uploaded in uploaded_files:
        file_bytes = uploaded.read()

        try:
            text = read_pdf_text(file_bytes)
        except:
            continue

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

        results.append({
            "filename": filename,
            "business": business,
            "abn": abn,
            "date": date,
            "invoice_number": invoice_number,
            "pdf": file_bytes,
        })

    st.session_state.results = results
    st.session_state.duplicates = duplicates
    st.session_state.total_uploaded = len(uploaded_files)


# -----------------------
# INVOICE LIST
# -----------------------

if st.session_state.results:

    st.subheader("Invoices")

    to_delete = None

    for i, r in enumerate(st.session_state.results):

        col1, col2 = st.columns([9, 1])

        with col1:
            label = f"{i+1}. {r['filename']}"
            st.write(label)

        with col2:
            delete_clicked = st.button("🗑️", key=f"delete_{i}")

        if delete_clicked:
            to_delete = i

        with st.expander("View details"):
            st.write(f"Business: {r['business']}")
            st.write(f"ABN: {r['abn']}")
            st.write(f"Date: {r['date']}")
            st.write(f"Invoice #: {r['invoice_number']}")

            st.download_button(
                "Download PDF",
                r["pdf"],
                file_name=r["filename"],
                mime="application/pdf",
                key=f"download_{i}",
            )

    if to_delete is not None:
        st.session_state.results.pop(to_delete)
        st.rerun()


# -----------------------
# SUMMARY
# -----------------------

if st.session_state.results:

    st.subheader("Summary")

    st.write(f"Total uploaded: {st.session_state.total_uploaded}")
    st.write(f"Unique invoices: {len(st.session_state.results)}")
    st.write(f"Duplicates removed: {len(st.session_state.duplicates)}")

    if st.session_state.duplicates:
        st.subheader("Duplicate invoices")
        st.dataframe(st.session_state.duplicates)

    zip_bytes = build_zip(st.session_state.results)

    st.download_button(
        "Download All (ZIP)",
        zip_bytes,
        "invoices.zip"
    )
