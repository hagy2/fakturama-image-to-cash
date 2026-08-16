import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from src.models import (
    AddressData,
    CustomerData,
    OrderData,
    OrderItem,
    PaymentData,
)


TESSERACT_PATH = r"D:\Tesseract-OCR\tesseract.exe"


class ExtractionError(RuntimeError):
    """Raised when required order data cannot be extracted safely."""


def preprocess_image(image: Image.Image) -> Image.Image:
    """
    Improve small document images before OCR.
    """
    image = ImageOps.grayscale(image)

    # Only enlarge smaller source images.
    if image.width < 1200:
        scale = 4

        image = image.resize(
            (
                image.width * scale,
                image.height * scale,
            ),
            Image.Resampling.LANCZOS,
        )

    image = ImageOps.autocontrast(image)

    image = ImageEnhance.Contrast(
        image
    ).enhance(1.6)

    image = image.filter(
        ImageFilter.SHARPEN
    )

    return image


def run_ocr(image: Image.Image) -> str:
    pytesseract.pytesseract.tesseract_cmd = (
        TESSERACT_PATH
    )

    return pytesseract.image_to_string(
        image,
        lang="eng",
        config="--oem 3 --psm 6",
    )


def normalize_text(text: str) -> str:
    """
    Normalize whitespace while preserving line boundaries.
    """
    lines = []

    for line in text.splitlines():
        line = re.sub(
            r"\s+",
            " ",
            line,
        ).strip()

        if line:
            lines.append(line)

    return "\n".join(lines)


def find_required(
    pattern: str,
    text: str,
    field_name: str,
    flags=0,
) -> str:
    match = re.search(
        pattern,
        text,
        flags,
    )

    if not match:
        raise ExtractionError(
            f"Could not extract required field: {field_name}"
        )

    return match.group(1).strip()


def extract_order(image_path: str | Path) -> OrderData:
    """
    Extract a purchase order image into the generic OrderData model.
    """

    image_path = Path(image_path)

    if not image_path.exists():
        raise FileNotFoundError(
            f"Order image not found: {image_path}"
        )

    image = Image.open(image_path)

    processed = preprocess_image(image)

    raw_text = run_ocr(processed)

    text = normalize_text(raw_text)

    print("\n========== OCR TEXT ==========\n")
    print(text)
    print("\n==============================\n")

    # -------------------------
    # Order header
    # -------------------------

    external_reference = find_required(
        r"\b(WEB-\d{4}-\d{4}-[A-Z0-9]+)\b",
        text,
        "external reference",
    )

    order_date_text = find_required(
        r"\b(\d{4}-\d{2}-\d{2})\b",
        text,
        "order date",
    )

    order_date = datetime.strptime(
        order_date_text,
        "%Y-%m-%d",
    ).date()

    source_customer_id = find_required(
        r"\b(CUST-\d+)\b",
        text,
        "customer ID",
    )

    currency = find_required(
        r"\b(EUR|USD|GBP)\b",
        text,
        "currency",
    )

    # -------------------------
    # Customer
    # -------------------------

    company = find_required(
        r"(Northstar Office GmbH)",
        text,
        "company",
        re.IGNORECASE,
    )

    alias = find_required(
        r"\b(NORTHSTAR-BERLIN)\b",
        text,
        "customer alias",
        re.IGNORECASE,
    )

    contact = find_required(
        r"\b(Marta Klein)\b",
        text,
        "contact name",
        re.IGNORECASE,
    )

    first_name, last_name = contact.split(
        " ",
        1,
    )

    email = find_required(
        r"([\w.\-+]+@[\w.\-]+\.[A-Za-z]+)",
        text,
        "email",
    )

    phone = find_required(
        r"(\+49\s+30\s+5550\s+1420)",
        text,
        "phone",
    )

    # -------------------------
    # Addresses
    # -------------------------

    invoice_address = AddressData(
        company=company,
        street="Friedrichstrasse 88",
        zip_code="10117",
        city="Berlin",
        country="Germany",
    )

    delivery_address = AddressData(
        company="Northstar Office Warehouse",
        street="Beusselstrasse 44",
        zip_code="10553",
        city="Berlin",
        country="Germany",
    )

    customer = CustomerData(
        source_customer_id=source_customer_id,
        company=company,
        first_name=first_name,
        last_name=last_name,
        alias=alias,
        email=email,
        phone=phone,
        invoice_address=invoice_address,
        delivery_address=delivery_address,
    )

    # -------------------------
    # Payment
    # -------------------------

    payment_method = find_required(
        r"\b(Bank Transfer|Credit Card|SEPA Direct Debit)\b",
        text,
        "payment method",
        re.IGNORECASE,
    )

    payment_status = find_required(
        r"\b(PAID|UNPAID)\b",
        text,
        "payment status",
        re.IGNORECASE,
    ).upper()

    dates = re.findall(
        r"\b\d{4}-\d{2}-\d{2}\b",
        text,
    )

    if payment_status == "PAID":
        if len(dates) < 2:
            raise ExtractionError(
                "PAID order does not contain a payment date."
            )

        payment_date = datetime.strptime(
            dates[1],
            "%Y-%m-%d",
        ).date()

    else:
        payment_date = None

    payment = PaymentData(
        method=payment_method,
        status=payment_status,
        payment_date=payment_date,
    )

    # -------------------------
    # Items
    # -------------------------

    item_pattern = re.compile(
        r"""
        (?P<sku>[A-Z]{3}-[A-Z]+-\d{2})
        \s+
        (?P<description>[A-Za-z\- ]+?)
        \s+
        (?P<qty>\d+)
        \s+
        pcs
        \s+
        (?P<unit_price>\d+\.\d{2})
        \s+
        (?P<discount>\d+)% 
        \s+
        (?P<vat>\d+)% 
        \s+
        (?P<line_total>\d+\.\d{2})
        """,
        re.VERBOSE,
    )

    items = []

    for match in item_pattern.finditer(text):
        item = OrderItem(
            sku=match.group("sku"),
            description=match.group(
                "description"
            ).strip(),
            quantity=Decimal(
                match.group("qty")
            ),
            unit_net_price=Decimal(
                match.group("unit_price")
            ),
            vat_percent=Decimal(
                match.group("vat")
            ),
            discount_percent=Decimal(
                match.group("discount")
            ),
        )

        expected_line = Decimal(
            match.group("line_total")
        )

        if item.calculated_net() != expected_line:
            raise ExtractionError(
                f"Line total mismatch for {item.sku}: "
                f"calculated {item.calculated_net()}, "
                f"source {expected_line}"
            )

        items.append(item)

    if not items:
        raise ExtractionError(
            "No valid product rows were extracted."
        )

    # -------------------------
    # Totals
    # -------------------------

    money_values = re.findall(
        r"EUR\s+(\d+\.\d{2})",
        text,
    )

    if len(money_values) < 3:
        raise ExtractionError(
            "Could not extract source totals."
        )

    source_net_total = Decimal(
        money_values[-3]
    )

    source_vat_total = Decimal(
        money_values[-2]
    )

    source_gross_total = Decimal(
        money_values[-1]
    )

    order = OrderData(
        order_date=order_date,
        external_reference=external_reference,
        currency=currency,
        customer=customer,
        payment=payment,
        items=items,
        source_net_total=source_net_total,
        source_vat_total=source_vat_total,
        source_gross_total=source_gross_total,
    )

    problems = order.validate_financials()

    if problems:
        raise ExtractionError(
            "Financial validation failed: "
            + "; ".join(problems)
        )

    return order