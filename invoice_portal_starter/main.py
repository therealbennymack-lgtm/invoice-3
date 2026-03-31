import io
import re
import zipfile
from datetime import datetime

import fitz
import streamlit as st

st.set_page_config(page_title="Invoice Splitter", layout="wide")
st.title("Invoice Splitter")
st.write("Upload PDF invoices, remove bad files, detect duplicates, and download cleaned invoice PDFs.")

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


def is_likely_invoice(text: str) -> bool:
    text_lower = text.lower()
    invoice_signals = [
        "invoice",
        "tax invoice",
        "invoice no",
        "invoice number",
        "abn",
        "amount due",
        "subtotal",
        "total",
    ]
    score = sum(1 for signal in invoice_signals if signal in text_lower)
    return score >= 2


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


# -----------------------
# UPLOAD
# -----------------------

uploaded_files = st.file_uploader("Upload PDF invoices", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    st.subheader("Uploaded files")

    file_records = []
    for idx, uploaded in enumerate(uploaded_files):
        file_bytes = uploaded.read()
        try:
            text = read_pdf_text(file_bytes)
            corrupt = False
        except Exception:
            text = ""
            corrupt = True

        likely_invoice = is_likely_invoice(text) if not corrupt else False

        file_records.append({
            "idx": idx,
            "name": uploaded.name,
            "bytes": file_bytes,
            "text": text,
            "corrupt": corrupt,
            "likely_invoice": likely_invoice,
        })

    st.write("Tick any file you want to exclude before processing.")

    files_to_process = []
    for record in file_records:
        label = record["name"]
        if record["corrupt"]:
            label += "  |  CORRUPT PDF"
        elif not record["likely_invoice"]:
            label += "  |  POSSIBLY NOT AN INVOICE"

        exclude = st.checkbox(f"Exclude: {label}", key=f"exclude_{record['idx']}")
        if not exclude:
            files_to_process.append(record)

    if st.button("Process remaining files"):
        if not files_to_process:
            st.error("No files selected for processing.")
        else:
            zip_buffer = io.BytesIO()
            seen = set()
            results = []
            duplicates = []
            excluded = []

            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for record in files_to_process:
                    text = record["text"]
                    file_bytes = record["bytes"]

                    if record["corrupt"]:
                        excluded.append({
                            "file": record["name"],
                            "reason": "Corrupt PDF",
                        })
                        continue

                    if not record["likely_invoice"]:
                        excluded.append({
                            "file": record["name"],
                            "reason": "Not recognised as an invoice",
                        })
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
                            "file": record["name"],
                        })
                        continue

                    seen.add(dedupe_key)

                    filename = clean_name(f"{business} - {abn} - {invoice_date}.pdf")
                    zf.writestr(filename, file_bytes)

                    results.append({
                        "filename": filename,
                        "business": business,
                        "abn": abn,
                        "invoice_date": invoice_date,
                        "invoice_number": invoice_number,
                        "pdf": file_bytes,
                    })

            zip_buffer.seek(0)

            st.subheader("Invoices")
            for r in results:
                with st.expander(r["filename"]):
                    st.write(f"Business: {r['business']}")
                    st.write(f"ABN: {r['abn']}")
                    st.write(f"Date: {r['invoice_date']}")
                    st.write(f"Invoice #: {r['invoice_number']}")
                    st.download_button(
                        "Download PDF",
                        r["pdf"],
                        file_name=r["filename"],
                        mime="application/pdf",
                        key=f"download_{r['filename']}",
                    )

            st.subheader("Summary")
            st.write(f"Total uploaded: {len(uploaded_files)}")
            st.write(f"Files processed: {len(files_to_process)}")
            st.write(f"Unique invoices kept: {len(results)}")
            st.write(f"Duplicates removed: {len(duplicates)}")
            st.write(f"Excluded files: {len(excluded)}")

            if duplicates:
                st.subheader("Duplicate invoices removed")
                st.dataframe(duplicates, use_container_width=True)

            if excluded:
                st.subheader("Excluded files")
                st.dataframe(excluded, use_container_width=True)

            st.download_button(
                "Download All (ZIP)",
                zip_buffer.getvalue(),
                "invoices.zip",
                mime="application/zip",
            )
