# Fakturama Image-to-Cash Automation

Windows desktop automation that extracts a sales order from an image, validates its financial data, and creates and verifies a persisted Order in Fakturama through the visible user interface.

## Submission status

| Assignment area | Status | Evidence or limitation |
|---|---|---|
| Source-image extraction and validation | Implemented | Structured Pydantic model plus financial cross-checks |
| Order header and modes | Implemented and exercised | Generated Order number, customer reference, date, Net price mode, With VAT |
| Debtor selection | Implemented and exercised | Final sample selected `CUST000001` |
| Missing-Debtor creation branch | Implemented, not exercised by the final sample | Guarded exact re-resolution after creation |
| Product selection | Implemented and exercised | Both sample SKUs selected from the Product selector |
| VAT/Product creation branches | Implemented, not exercised by the final sample | Used only when an exact master record is absent |
| Quantity, unit price, VAT, line discount, and line price | Implemented and exercised | Both source rows verified |
| Order totals | Implemented and exercised | Net `570.00`, VAT `108.30`, total `678.30` |
| Order save and Documents verification | Implemented | Saved Order row was visible; final code handles the observed `PO000008` to `0000008` OCR transformation |
| Linked follow-up Invoice | Not integrated into `main.py` | Requires additional live mapping and validation of payment and paid-value controls |

The final run used existing Debtor and Product masters. The creation branches are present in the code but were not invoked by that run.

## Implemented workflow

- OCR extraction of customer data, addresses, order metadata, payment data, item rows, VAT, discounts, currency, and totals.
- Validation that calculated line, net, VAT, and gross totals agree with the source before Fakturama is changed.
- Creation of a genuinely new Order and verification of its generated number.
- Customer reference, Order date, Net price mode, and VAT mode handling.
- Exact Debtor resolution, with conflict detection and a guarded creation branch.
- Exact Product resolution for every SKU, with guarded VAT and Product creation branches.
- Verified item quantities, unit prices, VAT percentages, discounts, and line totals.
- Verification of Total Net, VAT, and Total against the source image.
- One Order Save action followed by verification in **Data > Documents**.
- Diagnostic screenshots and OCR output under `artifacts/`.

## Validation scope and limitations

1. **The end-to-end desktop workflow was tested only with the single order image supplied with the task:** `samples/order_input.png`. It has not been validated against other document layouts, image qualities, resolutions, languages, currencies, customers, or item-table formats. The extraction and UI logic should therefore be treated as task-specific rather than production-generalized.
2. The production entry point stops after persisted Order verification. It does not create, complete, save, or finally verify the linked Invoice required by assignment sections 4.6–5.7.
3. The source currency is extracted, but the Fakturama transaction currency/symbol is not set or verified. The demonstrated Fakturama workspace displayed `$` while the sample source stated `EUR`; the numeric totals were verified, not the displayed currency symbol.
4. The workflow is not resumable after persistence. If a post-save verification fails, do not rerun the complete program against the same workspace without checking for the already-saved customer reference; otherwise another Order may be created.
5. Tesseract is configured with a Windows path in `src/extraction.py`. A different installation path requires changing `TESSERACT_PATH`.
6. The UI automation requires an unlocked, visible interactive Windows desktop. It is not suitable for a disconnected or minimized non-interactive session.
7. `debug_*.py`, `save_current_order_*.py`, and `verify_po*.py` are development/reconnaissance utilities, not production entry points.

The Invoice and test-scope limitations are documented rather than hidden or replaced with unverified claims.

## Project structure

```text
fakturama-image-to-cash/
├── main.py                 Production workflow entry point
├── requirements.txt       Pinned Python dependencies
├── samples/                Task-provided source order image
├── src/
│   ├── extraction.py       OCR, parsing, and source validation
│   ├── fakturama.py        Fakturama UI automation and verification
│   └── models.py           Validated models and calculations
├── tests/
│   └── test_models.py      Model and financial-calculation tests
├── artifacts/
│   ├── screenshots/        Selected runtime evidence
│   └── logs/               Local diagnostics; excluded from submission
├── docs/
│   └── EVIDENCE.md         Captions for selected screenshots
└── SUBMISSION_CHECKLIST.md Final pre-publication checklist
```

## Requirements

- Windows with Fakturama installed and already running.
- Python 3 with a virtual environment.
- Tesseract OCR installed at:

  ```text
  D:\Tesseract-OCR\tesseract.exe
  ```

- Fakturama visible and restored in the same interactive Windows session as the automation.
- An English Fakturama UI matching the environment used during development.

## Setup

From PowerShell in the project directory:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run

Open Fakturama first, then run:

```powershell
.venv\Scripts\Activate.ps1
python -m py_compile src\fakturama.py main.py
python main.py samples\order_input.png
```

Do not paste PowerShell prompt text, such as `(.venv) PS D:\...>`, into a command.

Exit behavior:

- `0`: completed the production Order workflow.
- `1`: extraction or Fakturama automation failure.
- `2`: manual review required because the source or UI result was ambiguous.

## Expected task-image result

| Field | Expected value |
|---|---:|
| External reference | `WEB-2026-0714-A17` |
| Order date | `2026-07-14` |
| Customer | `Northstar Office GmbH` |
| Item count | `2` |
| Total Net | `570.00` |
| VAT | `108.30` |
| Gross Total | `678.30` |

| SKU | Qty | Unit Net | VAT | Discount | Line Net |
|---|---:|---:|---:|---:|---:|
| `CHR-ERG-01` | 2.00 | 250.00 | 19% | 10.00% | 450.00 |
| `MAT-DESK-02` | 3.00 | 40.00 | 19% | 0.00% | 120.00 |

## Verification and safety behavior

The automation stops instead of guessing when it encounters conflicting Debtors, Products, VAT masters, unsupported payment methods, mismatched totals, or unverified persistence.

Fakturama's SWT controls and Windows DPI behavior can make wrapper coordinates unreliable. The implementation combines semantic Win32 discovery, OCR grounding, physical input, and visual verification. Product selection is accepted only after the exact OCR-matched table row is visibly selected.

Saved document numbers tolerate only observed OCR transformations. For example, `PO000008` may be read as `0000008`; that token is accepted only while the filtered saved row also matches the expected date, customer reference, open state, and total.

## Evidence

See [docs/EVIDENCE.md](docs/EVIDENCE.md) for the annotated evidence page. Selected submission screenshots:

- `artifacts/screenshots/order_lines_latest.png`
- `artifacts/screenshots/final_order_documents_verified.png`
- `artifacts/screenshots/product_selector_after_row_click.png`

Review every screenshot before publishing. Runtime screenshots can expose local paths, other Fakturama records, or desktop information.

## Tests

```powershell
python -m pytest
```

The automated suite covers the domain models and financial calculations. The Fakturama desktop workflow was manually exercised only with `samples/order_input.png`; it is not covered by a headless UI test suite or a multi-document evaluation set.

## Written question: If you had three more hours, what would you do?

I would first finish and validate the linked-Invoice stage against a clean Fakturama workspace. This would include mapping the payment-method control, verifying the conditional paid status, payment date, and full payment value, saving the Invoice exactly once, and confirming both the open source Order and saved Invoice in **Data > Documents**. I would then add a resumable workflow checkpoint so a verification failure after persistence cannot create a duplicate Order on rerun. Finally, I would test multiple order layouts and image-quality conditions, add focused tests for OCR normalization, coordinate mapping, and saved-document matching, and record a clean end-to-end demonstration.


