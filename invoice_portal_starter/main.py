import io
import re
import zipfile
from datetime import datetime

import fitz
import streamlit as st

st.set_page_config(page_title="Invoice Splitter", layout="wide")
st.title("Invoice Splitter")
st.write("Upload PDF invoices. The app will extract Business Name, ABN, Invoice Date, and Invoice Number, remove duplicates, and let you preview each PDF.")

ABN_REGEX = re.compile(r"\b(?:ABN|A\.B\.N\.?)[^\d]{0,10}(\d[\d\s]{9,20}\d)\b", re.IGNORECASE)
DATE_REGEXES = [
    re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b"),
    re.compile(r"\b(\d{1,2}-[A-Za-z]{3}-\d{2,4})\b"),
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
]
INVOICE_NO_REGEXES = [
    re.compile(r"\b(?:Invoice\s*(?:No\.?|Number|#)|Tax\s*Invoice\s*(?:No\.?|Number|#))\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\/_]*)\b", re.IGNORECASE),
    re.compile(r"\bTax\s+Invoice\s+([A-Z0-9][A-Z0-9\-\/_]{3,})\b", re.IGNORECASE),
]

def clean_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r'[\\/:*?"<>|]+', "_", value)
    return value[:150] or "UNKNOWN"

def parse_date(text: str) -> str:
    import re
    from datetime import datetime

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

    # Fallback: first valid date anywhere in text
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

def extract_abn(text: str) -> str:
    import re

    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]
    full_text = " ".join(lines)

    # Priority 1: ABN label followed by number
    match = re.search(r"(?:ABN|A\.B\.N\.?)\s*[:\-]?\s*(?:ABN\s*[:\-]?\s*)?(\d[\d\s]{9,20}\d)", full_text, re.IGNORECASE)
    if match:
        digits = re.sub(r"\D", "", match.group(1))
        if len(digits) == 11:
            return digits

    # Priority 2: any 11-digit ABN-like number
    candidates = re.findall(r"\b\d{2}\s?\d{3}\s?\d{3}\s?\d{3}\b", full_text)
    for candidate in candidates:
        digits = re.sub(r"\D", "", candidate)
        if len(digits) == 11:
            return digits

    return "UNKNOWNABN"

def extract_invoice_number(text: str) -> str:
    short = text[:6000]
    for pattern in INVOICE_NO_REGEXES:
        match = pattern.search(short)
        if match:
            return clean_name(match.group(1)).replace(" ", "")
    return "UNKNOWN-INVOICE"

def extract_business(text: str) -> str:
    import re

    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]
    full_text = " ".join(lines).lower()

    # Hard match known suppliers first
    known_suppliers = [
        ("canon australia pty ltd", "CANON AUSTRALIA PTY LTD"),
        ("canon australia pty. ltd.", "CANON AUSTRALIA PTY LTD"),
        ("kyocera document solutions australia pty ltd", "KYOCERA DOCUMENT SOLUTIONS AUSTRALIA PTY LTD"),
        ("bbc digital", "BBC DIGITAL"),
        ("kk technical services pty ltd", "KK TECHNICAL SERVICES PTY LTD"),
        ("that marketing co", "THAT MARKETING CO"),
    ]
    for needle, label in known_suppliers:
        if needle in full_text:
            return label

    bad_starts = ("<~", "~", "<", ">", "customer no", "invoice no", "date", "abn")
    bad_contains = [
        "customer no",
        "customer bill to",
        "customer ship to",
        "installation address",
        "remittance",
        "destination",
        "service order no",
        "order date",
        "payment due by",
        "trading terms",
        "bpay",
        "invoice",
        "tax invoice",
        "invoice date",
        "due date",
        "page",
        "description",
        "subtotal",
        "total",
        "amount due",
        "phone",
        "fax",
        "www.",
        "email",
    ]

    # Prefer supplier lines that appear after "direct to:"
    for line in lines[:60]:
        lower = line.lower()
        if "direct to:" in lower and "canon" in lower:
            cleaned = lower.replace("direct to:", "").strip()
            return clean_name(cleaned).upper().replace(".", "")

    # Prefer PTY LTD lines that do not look like customer/address labels
    for line in lines[:60]:
        lower = line.lower()

        if lower.startswith(bad_starts):
            continue
        if any(x in lower for x in bad_contains):
            continue
        if re.search(r"(street|road|vic|nsw|qld|wa|sa|tas|australia)$", lower):
            continue
        if "pty ltd" in lower:
            return clean_name(line).upper().replace(".", "")

    # Fallback clean line
    for line in lines[:60]:
        lower = line.lower()

        if lower.startswith(bad_starts):
            continue
        if any(x in lower for x in bad_contains):
            continue
        if re.search(r"\d{3,}", line):
            continue
        if re.search(r"(street|road|vic|nsw|qld|wa|sa|tas|australia|email|www\.)", lower):
            continue
        if len(line) < 3:
            continue

        return clean_name(line).upper().replace(".", "")

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
duplicates = []

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for uploaded in uploaded_files:
            file_bytes = uploaded.read()
            text = read_pdf_text(file_bytes)

            business = extract_business(text)
            abn = extract_abn(text)
            invoice_date = parse_date(text)
            invoice_number = extract_invoice_number(text)

       if dedupe_key in seen:
    duplicates.append({
        "business": business,
        "abn": abn,
        "invoice_date": invoice_date,
        "invoice_number": invoice_number,
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
                "pdf_bytes": file_bytes,
            })

    zip_buffer.seek(0)

    st.subheader("Invoices")
    for i, item in enumerate(results, start=1):
        with st.expander(f"{i}. {item['filename']}"):
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**Business Name:** {item['business']}")
                st.write(f"**ABN:** {item['abn']}")
                st.write(f"**Invoice Date:** {item['invoice_date']}")
                st.write(f"**Invoice Number:** {item['invoice_number']}")
            with col2:
                st.download_button(
                    label="Download this PDF",
                    data=item["pdf_bytes"],
                    file_name=item["filename"],
                    mime="application/pdf",
                    key=f"download_{i}",
                )

            st.write("**PDF Preview**")
            pdf_base64 = item["pdf_bytes"].hex()
            pdf_bytes = bytes.fromhex(pdf_base64)
            st.download_button(
                label="Open PDF in browser",
                data=pdf_bytes,
                file_name=item["filename"],
                mime="application/pdf",
                key=f"open_{i}",
            )

    table_rows = [
        {
            "File Name": item["filename"],
            "Business Name": item["business"],
            "ABN": item["abn"],
            "Invoice Date": item["invoice_date"],
            "Invoice Number": item["invoice_number"],
        }
        for item in results
    ]

    st.subheader("Summary")

total_uploaded = len(uploaded_files)
unique_count = len(results)
duplicate_count = len(duplicates)

st.write(f"Total files uploaded: {total_uploaded}")
st.write(f"Unique invoices kept: {unique_count}")
st.write(f"Duplicate invoices removed: {duplicate_count}")

if duplicates:
    st.subheader("Duplicate invoices detected")
    st.dataframe(duplicates, use_container_width=True)
