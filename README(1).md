# Fakturama Image-to-Cash Automation

Windows desktop automation that extracts a sales order from an image, validates its financial data, and creates a persisted Order in Fakturama through the visible user interface.

## Implemented workflow

- OCR extraction of customer, addresses, order metadata, payment data, item rows, VAT, discounts, and totals.
- Financial validation before any Fakturama changes are made.
- Creation of a genuinely new Order and verification of its generated number.
- Customer reference, date, Net price mode, and VAT mode handling.
- Exact Debtor resolution, with a guarded Debtor-creation branch.
- Exact Product resolution for every SKU, with guarded VAT and Product creation branches.
- Verified item quantities, unit prices, VAT percentages, discounts, and line totals.
- Verification of Total Net, VAT, and Total against the source image.
- Single Order save followed by verification in **Data > Documents**.
- Diagnostic screenshots and OCR logs under `artifacts/`.

## Current limitation

The production entry point currently completes requirements through the persisted Order verification stage. The linked-Invoice stage is not integrated into `main.py`.

`debug_create_linked_invoice.py` contains the verified reconnaissance for opening **Invoice** from the saved Order's **Create a follow-up document** area. The remaining payment-method, paid-date/value, Invoice save, and final dual-document verification require live UI mapping and validation before they can be safely promoted into the production workflow. They are intentionally not replaced with hard-coded or unverified clicks.

## Requirements

- Windows with Fakturama installed and running.
- Python virtual environment.
- Tesseract OCR installed at:

  ```text
  D:\Tesseract-OCR\tesseract.exe
  ```

- Fakturama must be visible, restored, and running in the same interactive Windows session as the automation.

## Setup

From PowerShell in the project directory:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Run

Open Fakturama first, then run:

```powershell
.venv\Scripts\Activate.ps1
python -m py_compile src\fakturama.py main.py
python main.py samples\order_input.png
```

Do not copy the PowerShell prompt text such as `(.venv) PS D:\...>` into a command.

## Expected verified source data

The included sample resolves to:

| Field | Expected value |
|---|---:|
| External reference | `WEB-2026-0714-A17` |
| Order date | `2026-07-14` |
| Customer | `Northstar Office GmbH` |
| Item count | `2` |
| Total Net | `570.00` |
| VAT | `108.30` |
| Gross Total | `678.30` |

Expected item lines:

| SKU | Qty | Unit Net | VAT | Discount | Line Net |
|---|---:|---:|---:|---:|---:|
| `CHR-ERG-01` | 2.00 | 250.00 | 19% | 10.00% | 450.00 |
| `MAT-DESK-02` | 3.00 | 40.00 | 19% | 0.00% | 120.00 |

## Verification and safety behavior

The automation stops instead of guessing when it encounters conflicting Debtors, Products, VAT masters, unsupported payment methods, mismatched financial totals, or unverified persistence.

Fakturama's SWT controls and Windows DPI handling can make wrapper coordinates unreliable. The implementation combines semantic Win32 discovery, OCR grounding, physical input, and visual verification. Product selection, for example, is accepted only after the exact OCR-matched table row is visibly selected.

Saved document numbers tolerate only known OCR transformations. For example, `PO000008` may be read as `0000008`; that token is accepted only while the saved row also matches the expected date, customer reference, open state, and total.

## Evidence

Runtime evidence is written to:

- `artifacts/screenshots/order_lines_latest.png`
- `artifacts/screenshots/final_order_documents_verified.png`
- `artifacts/screenshots/product_selector_latest.png`
- `artifacts/screenshots/product_selector_after_row_click.png`
- `artifacts/logs/`

The screenshots show the visible Fakturama state used by the verification logic; logs preserve OCR output for diagnosis.

## Tests

Run the model and financial validation tests with:

```powershell
python -m pytest
```

The desktop workflow must be tested manually against a visible Fakturama session because it depends on real Windows controls and the installed Fakturama workspace.

## Written question: If you had three more hours, what would you do?

I would first finish and validate the linked-Invoice stage against a clean Fakturama workspace. This would include mapping the payment-method control, verifying the conditional paid status, payment date, and full payment value, saving the Invoice exactly once, and confirming both the open source Order and saved Invoice in **Data > Documents**. I would then add a resumable workflow checkpoint so a verification failure after persistence cannot create a duplicate Order on rerun. Finally, I would add focused tests for OCR normalization, window-coordinate mapping, and saved-document row matching, and record a short clean end-to-end demonstration.

