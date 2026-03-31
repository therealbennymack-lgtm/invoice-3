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


def pdf_to_images(file_bytes: bytes, zoom: float = 1.4):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    matrix = fitz.Matrix(zoom, zoom)
    for page in doc:
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        images.append(pix.tobytes("png"))
    doc.close()
    return images


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
                except Exception:
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


def build_filename(item: dict) -> str:
    return clean_name(f"{item['business']} - {item['abn']} - {item['date']}.pdf")


def build_zip(results):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in results:
            zf.writestr(r["filename"], r["pdf"])
    buffer.seek(0)
    return buffer.getvalue()


def has_unknown_fields(item: dict) -> bool:
    return (
        item["business"] == "UNKNOWN BUSINESS"
        or item["abn"] == "UNKNOWNABN"
        or item["date"] == "UNKNOWN-DATE"
        or item["invoice_number"] == "UNKNOWN-INVOICE"
    )


# -----------------------
# SESSION STATE
# -----------------------

if "results" not in st.session_state:
    st.session_state.results = []

if "duplicates" not in st.session_state:
    st.session_state.duplicates = []

if "total_uploaded" not in st.session_state:
    st.session_state.total_uploaded = 0

if "pending_delete" not in st.session_state:
    st.session_state.pending_delete = None

if "selected_invoice" not in st.session_state:
    st.session_state.selected_invoice = None

if "save_message" not in st.session_state:
    st.session_state.save_message = ""

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
            page_images = pdf_to_images(file_bytes)
        except Exception:
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

        item = {
            "filename": "",
            "business": business,
            "abn": abn,
            "date": date,
            "invoice_number": invoice_number,
            "pdf": file_bytes,
            "text": text,
            "page_images": page_images,
        }
        item["filename"] = build_filename(item)
        results.append(item)

    st.session_state.results = results
    st.session_state.duplicates = duplicates
    st.session_state.total_uploaded = len(uploaded_files)
    st.session_state.pending_delete = None
    st.session_state.selected_invoice = 0 if results else None
    st.session_state.save_message = ""

# -----------------------
# MAIN LAYOUT
# -----------------------

left_col, right_col = st.columns([1.1, 1])

with left_col:
    if st.session_state.results:
        st.subheader("Invoices")

        for i, r in enumerate(st.session_state.results):
            row1, row2, row3 = st.columns([8, 1, 1])

            with row1:
                if st.button(f"{i+1}. {r['filename']}", key=f"select_{i}", use_container_width=True):
                    st.session_state.selected_invoice = i
                    st.session_state.save_message = ""

            with row2:
                if st.button("Edit", key=f"edit_{i}", use_container_width=True):
                    st.session_state.selected_invoice = i
                    st.session_state.save_message = ""

            with row3:
                if st.button("Delete", key=f"delete_{i}", use_container_width=True):
                    st.session_state.pending_delete = i
                    st.rerun()

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
                    use_container_width=True,
                )

            if st.session_state.pending_delete == i:
                st.warning(f"Are you sure you want to delete: {r['filename']}?")

                confirm_col, cancel_col = st.columns(2)

                with confirm_col:
                    if st.button("Confirm Delete", key=f"confirm_delete_{i}", use_container_width=True):
                        st.session_state.results.pop(i)
                        if st.session_state.selected_invoice == i:
                            st.session_state.selected_invoice = 0 if st.session_state.results else None
                        elif st.session_state.selected_invoice is not None and st.session_state.selected_invoice > i:
                            st.session_state.selected_invoice -= 1
                        st.session_state.pending_delete = None
                        st.rerun()

                with cancel_col:
                    if st.button("Cancel", key=f"cancel_delete_{i}", use_container_width=True):
                        st.session_state.pending_delete = None
                        st.rerun()

        st.subheader("Summary")
        st.write(f"Total uploaded: {st.session_state.total_uploaded}")
        st.write(f"Unique invoices: {len(st.session_state.results)}")
        st.write(f"Duplicates removed: {len(st.session_state.duplicates)}")

        if st.session_state.duplicates:
            st.subheader("Duplicate invoices")
            st.dataframe(st.session_state.duplicates, use_container_width=True)

        zip_bytes = build_zip(st.session_state.results)

        st.download_button(
            "Download All (ZIP)",
            zip_bytes,
            "invoices.zip",
            mime="application/zip",
            use_container_width=True,
        )

with right_col:
    if st.session_state.results and st.session_state.selected_invoice is not None:
        idx = st.session_state.selected_invoice
        item = st.session_state.results[idx]

        st.subheader("Invoice Viewer")

        if has_unknown_fields(item):
            st.warning("Some fields are missing. Review the invoice pages below and update the fields.")
        else:
            st.success("All key fields were detected.")

        if st.session_state.save_message:
            st.success(st.session_state.save_message)

        st.download_button(
            "Download Selected PDF",
            data=item["pdf"],
            file_name=item["filename"],
            mime="application/pdf",
            use_container_width=True,
            key=f"download_selected_{idx}",
        )

        st.subheader("Edit extracted fields")

        with st.form(key=f"edit_form_{idx}"):
            business_val = st.text_input("Business Name", value=item["business"])
            abn_val = st.text_input("ABN", value=item["abn"])
            date_val = st.text_input("Invoice Date", value=item["date"])
            invoice_val = st.text_input("Invoice Number", value=item["invoice_number"])

            submitted = st.form_submit_button("Save changes", use_container_width=True)

        if submitted:
            st.session_state.results[idx]["business"] = business_val.strip() or "UNKNOWN BUSINESS"
            st.session_state.results[idx]["abn"] = abn_val.strip() or "UNKNOWNABN"
            st.session_state.results[idx]["date"] = date_val.strip() or "UNKNOWN-DATE"
            st.session_state.results[idx]["invoice_number"] = invoice_val.strip() or "UNKNOWN-INVOICE"
            st.session_state.results[idx]["filename"] = build_filename(st.session_state.results[idx])
            st.session_state.save_message = "Invoice updated successfully."
            st.rerun()

        st.subheader("PDF Pages")
        for page_num, page_img in enumerate(item["page_images"], start=1):
            st.markdown(f"**Page {page_num}**")
            st.image(page_img, use_container_width=True)

        with st.expander("View extracted text"):
            st.text(item["text"][:12000])
