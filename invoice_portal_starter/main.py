import io
import re
import zipfile
from datetime import datetime

import fitz
import streamlit as st

st.set_page_config(page_title="Invoice Splitter", layout="wide")
st.title("Invoice Splitter")
st.write("Upload PDF invoices, remove duplicates, delete bad invoice rows, and download cleaned invoice PDFs.")

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

    for line in lines[:30]:
        if "pty ltd" in line.lower():
            bad = [
                "customer bill to",
                "customer ship to",
                "installation address",
                "office print solutions",
                "docufy",
            ]
            if any(b in line.lower() for b in bad):
                continue
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
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]
    full_text = "\n".join(lines)

    date_patterns = [
        r"(\d{1,2}/\d{1,2}/\d{4})",
        r"(\d{1,2}-[A-Za-z]{3}-\d{2,4})",
        r"(\d{4}-\d{2}-\d{2})",
    ]

    anchor_patterns = [
        r"Invoice Date\s*[:\-]?\s*",
        r"Date\s*[:\-]?\s*",
    ]

    for anchor in anchor_patterns:
        for dp in date_patterns:
            match = re.search(anchor + dp, full_text, re.IGNORECASE)
            if match:
                raw = match.group(1).strip()
                for fmt in ("%d/%m/%Y", "%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                    except ValueError:
                        pass

    for dp in date_patterns:
        matches = re.findall(dp, full_text)
        for raw in matches:
            raw = raw.strip()
            for fmt in ("%d/%m/%Y", "%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d"):
                try:
                    return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                except ValueError:
                    pass

    return "UNKNOWN-DATE"


def extract_invoice_number(text: str) -> str:
    patterns = [
        r"(?:Invoice\s*(?:No|Number|#)|Tax Invoice No)\s*[:\-]?\s*([A-Z0-9\-\/]+)",
        r"Tax Invoice\s+([A-Z0-9][A-Z0-9\-\/]{3,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return clean_name(match.group(1)).replace(" ", "")
    return "UNKNOWN-INVOICE"


def build_zip_bytes(results):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in results:
            zf.writestr(r["filename"], r["pdf"])
    zip_buffer.seek(0)
    return zip_buffer.getvalue()


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
        except Exception:
            continue

        business = extract_business(text)
        abn = extract_abn(text)
        invoice_date = parse_date(text)
        invoice_number = extract_invoice_number(text)

        dedupe_key = f"{business}|{abn}|{invoice_date}|{invoice_number}"

        if dedupe_key in seen:
            duplicates.append({
                "business": business,
                "abn": abn,
                "invoice_date": invoice_date,
                "invoice_number": invoice_number,
                "source_file": uploaded.name,
            })
            continue

        seen.add(dedupe_key)

        filename = clean_name(f"{business} - {abn} - {invoice_date}.pdf")

        results.append({
            "filename": filename,
            "business": business,
            "abn": abn,
            "invoice_date": invoice_date,
            "invoice_number": invoice_number,
            "source_file": uploaded.name,
            "pdf": file_bytes,
        })

    st.session_state.results = results
    st.session_state.duplicates = duplicates
    st.session_state.total_uploaded = len(uploaded_files)


# -----------------------
# INVOICES
# -----------------------

if st.session_state.results:
    st.subheader("Invoices")

    to_delete = None

    for i, r in enumerate(st.session_state.results):
        row_col1, row_col2 = st.columns([8, 1])

        with row_col1:
            with st.expander(f"{i+1}. {r['filename']}"):
                st.write(f"Business: {r['business']}")
                st.write(f"ABN: {r['abn']}")
                st.write(f"Date: {r['invoice_date']}")
                st.write(f"Invoice #: {r['invoice_number']}")
                st.write(f"Source file: {r['source_file']}")

                st.download_button(
                    "Download PDF",
                    r["pdf"],
                    file_name=r["filename"],
                    mime="application/pdf",
                    key=f"download_{i}",
                )

        with row_col2:
            st.write("")
            st.write("")
            if st.button("Delete", key=f"delete_{i}"):
                to_delete = i

    if to_delete is not None:
        st.session_state.results.pop(to_delete)
        st.rerun()

    st.subheader("Summary")
    st.write(f"Total uploaded: {st.session_state.total_uploaded}")
    st.write(f"Unique invoices kept: {len(st.session_state.results)}")
    st.write(f"Duplicates removed: {len(st.session_state.duplicates)}")

    if st.session_state.duplicates:
        st.subheader("Duplicate invoices removed")
        st.dataframe(st.session_state.duplicates, use_container_width=True)

    zip_bytes = build_zip_bytes(st.session_state.results)

    st.download_button(
        "Download All (ZIP)",
        zip_bytes,
        "invoices.zip",
        mime="application/zip",
    )
