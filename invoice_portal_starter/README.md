# Invoice Splitter Portal Starter

This is a starter web app for turning your invoice workflow into a product.

## What it does

- upload one or more PDFs
- detect invoice boundaries
- extract supplier business name
- extract ABN
- extract invoice number
- extract invoice date
- remove duplicates
- output one PDF per invoice
- name files like:

`BUSINESS NAME - ABN - YYYY-MM-DD.pdf`

## Tech stack

- FastAPI
- Jinja2 templates
- PyMuPDF

## Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Open:
`http://127.0.0.1:8000`

## Notes

This starter works best with text-based PDFs.

For scanned image PDFs, add OCR next. The usual product path is:

- OCRmyPDF or Tesseract
- database for job history
- auth and billing
- Stripe
- cloud storage
- background queue like Celery or RQ
- webhook and API access

## Suggested product roadmap

### v1
- upload
- split
- dedupe
- zip download

### v2
- OCR
- team accounts
- usage history
- admin dashboard

### v3
- accounting integrations
- API keys
- white label portal
