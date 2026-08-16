import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytesseract
from pytesseract import Output
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
    """Raised when required source data cannot be extracted safely."""


class ManualReviewRequired(ExtractionError):
    """Raised when OCR produced an ambiguous critical value."""


def preprocess_image(image: Image.Image) -> Image.Image:
    """
    Improve a document image before OCR.

    Smaller inputs are enlarged automatically. This means callers
    do not need to manually provide an enhanced image.
    """
    image = ImageOps.grayscale(image)

    if image.width < 700:
        scale = 4
    elif image.width < 1200:
        scale = 2
    else:
        scale = 1

    if scale > 1:
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


def run_ocr(image: Image.Image, psm: int = 6) -> str:
    pytesseract.pytesseract.tesseract_cmd = (
        TESSERACT_PATH
    )

    return pytesseract.image_to_string(
        image,
        lang="eng",
        config=f"--oem 3 --psm {psm}",
    )


def normalize_text(text: str) -> str:
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


def section_between(
    text: str,
    start_marker: str,
    end_markers: list[str],
) -> str:
    """
    Return text between semantic section headings.
    """
    upper = text.upper()

    start = upper.find(
        start_marker.upper()
    )

    if start == -1:
        raise ExtractionError(
            f"Could not find section '{start_marker}'."
        )

    start += len(start_marker)

    end_positions = []

    for marker in end_markers:
        position = upper.find(
            marker.upper(),
            start,
        )

        if position != -1:
            end_positions.append(position)

    end = min(end_positions) if end_positions else len(text)

    return text[start:end].strip()


def extract_customer_fields(
    text: str,
    invoice_company: str | None,
):
    """
    Extract customer/contact information without relying on
    specific names from the supplied test order.
    """

    try:
        section = section_between(
            text,
            "CUSTOMER AND CONTACT",
            [
                "ADDRESSES",
                "BILLING ADDRESS",
                "PAYMENT",
            ],
        )
    except ExtractionError:
        section = text

    # ----- Email -----

    email = find_required(
        r"([\w.\-+]+@[\w.\-]+\.[A-Za-z]+)",
        section,
        "email",
    )

    # ----- Phone -----

    phone_match = re.search(
        r"(\+\d[\d\s().\-]{7,}\d)",
        section,
    )

    if not phone_match:
        raise ExtractionError(
            "Could not extract phone number."
        )

    phone = phone_match.group(1).strip()

    # ----- Alias -----

    alias_match = re.search(
        r"Alias\s*:\s*([A-Z0-9][A-Z0-9_-]+)",
        section,
        re.IGNORECASE,
    )

    if alias_match:
        alias = alias_match.group(1)
    else:
        alias = None

        # Fallback for layouts where the alias shares a line
        # with another label, e.g.:
        # NORTHSTAR-BERLIN PHONE
        for line in section.splitlines():
            matches = re.findall(
                r"\b([A-Z0-9]+(?:-[A-Z0-9]+)+)\b",
                line,
            )

            if matches:
                alias = matches[0]
                break

        if not alias:
            raise ExtractionError(
                "Could not extract customer alias."
            )

    # ----- Label-based layout -----

    company_match = re.search(
        r"Company\s*:\s*(.+)",
        section,
        re.IGNORECASE,
    )

    contact_match = re.search(
        r"Contact\s*:\s*([A-Za-z][A-Za-z .'\-]+)",
        section,
        re.IGNORECASE,
    )

    company = (
        company_match.group(1).strip()
        if company_match
        else None
    )

    contact = (
        contact_match.group(1).strip()
        if contact_match
        else None
    )

    # ----- Two-column layout -----
    #
    # Example:
    # COMPANY CONTACT NAME
    # Example Company GmbH Jane Smith

    if not company or not contact:
        lines = section.splitlines()

        for index, line in enumerate(lines):
            upper = line.upper()

            if (
                "COMPANY" in upper
                and "CONTACT" in upper
                and index + 1 < len(lines)
            ):
                combined = lines[index + 1].strip()

                company_contact = re.match(
                    r"""
                    ^(.+?\b
                    (?:
                        GmbH |
                        AG |
                        Ltd\.? |
                        LLC |
                        Inc\.? |
                        Corp\.? |
                        PLC |
                        BV |
                        S\.?A\.?
                    ))
                    \s+
                    (.+)$
                    """,
                    combined,
                    re.VERBOSE | re.IGNORECASE,
                )

                if company_contact:
                    company = (
                        company
                        or company_contact.group(1).strip()
                    )

                    contact = (
                        contact
                        or company_contact.group(2).strip()
                    )

                    break

    # Billing company is a legitimate fallback because it was
    # independently extracted from the source address section.
    if not company and invoice_company:
        company = invoice_company

    if not company:
        raise ExtractionError(
            "Could not extract customer company."
        )

    if not contact:
        raise ExtractionError(
            "Could not extract contact name."
        )

    contact_parts = contact.split()

    if len(contact_parts) < 2:
        raise ManualReviewRequired(
            f"Ambiguous contact name: {contact!r}"
        )

    first_name = contact_parts[0]
    last_name = " ".join(contact_parts[1:])

    return {
        "company": company,
        "alias": alias,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "phone": phone,
    }


def find_ocr_word(
    data,
    target: str,
    min_y: int = 0,
):
    target = target.upper()

    candidates = []

    for index, raw_text in enumerate(data["text"]):
        value = raw_text.strip().upper()

        if value != target:
            continue

        top = int(data["top"][index])

        if top < min_y:
            continue

        try:
            confidence = float(
                data["conf"][index]
            )
        except ValueError:
            confidence = -1

        candidates.append(
            {
                "left": int(
                    data["left"][index]
                ),
                "top": top,
                "width": int(
                    data["width"][index]
                ),
                "height": int(
                    data["height"][index]
                ),
                "conf": confidence,
            }
        )

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda item: item["conf"],
    )


def group_words_into_lines(
    words: list[dict],
    tolerance: int,
):
    """
    Group positioned OCR words into visual text lines.
    """
    words = sorted(
        words,
        key=lambda word: (
            word["top"],
            word["left"],
        ),
    )

    lines = []

    for word in words:
        placed = False

        for line in lines:
            if abs(
                word["top"] - line["top"]
            ) <= tolerance:
                line["words"].append(word)

                line["top"] = int(
                    (
                        line["top"]
                        + word["top"]
                    )
                    / 2
                )

                placed = True
                break

        if not placed:
            lines.append(
                {
                    "top": word["top"],
                    "words": [word],
                }
            )

    result = []

    for line in sorted(
        lines,
        key=lambda item: item["top"],
    ):
        ordered = sorted(
            line["words"],
            key=lambda word: word["left"],
        )

        text = " ".join(
            word["text"]
            for word in ordered
        ).strip()

        if text:
            result.append(text)

    return result


def parse_address_lines(
    lines: list[str],
    label: str,
) -> AddressData:
    """
    Parse address lines by locating the postal-code/city row.
    """

    cleaned = [
        line.strip()
        for line in lines
        if line.strip()
        and "ADDRESS" not in line.upper()
    ]

    zip_index = None
    zip_code = None
    city = None

    for index, line in enumerate(cleaned):
        match = re.match(
            r"^(\d{4,6})\s+(.+)$",
            line,
        )

        if match:
            zip_index = index
            zip_code = match.group(1)
            city = match.group(2).strip()
            break

    if zip_index is None:
        raise ExtractionError(
            f"Could not parse {label} postal code/city."
        )

    if zip_index < 2:
        raise ExtractionError(
            f"Incomplete {label} address."
        )

    company = cleaned[zip_index - 2]
    street = cleaned[zip_index - 1]

    if zip_index + 1 >= len(cleaned):
        raise ExtractionError(
            f"Missing country for {label} address."
        )

    country = cleaned[zip_index + 1]

    return AddressData(
        company=company,
        street=street,
        zip_code=zip_code,
        city=city,
        country=country,
    )


def extract_addresses(
    processed: Image.Image,
):
    """
    Extract billing and delivery addresses spatially.

    This avoids hardcoding specific address values and works with
    side-by-side address columns.
    """

    data = pytesseract.image_to_data(
        processed,
        lang="eng",
        config="--oem 3 --psm 6",
        output_type=Output.DICT,
    )

    billing = find_ocr_word(
        data,
        "BILLING",
    )

    delivery = find_ocr_word(
        data,
        "DELIVERY",
    )

    if not billing or not delivery:
        raise ExtractionError(
            "Could not locate billing/delivery address headings."
        )

    payment = find_ocr_word(
        data,
        "PAYMENT",
        min_y=max(
            billing["top"],
            delivery["top"],
        ),
    )

    if not payment:
        raise ExtractionError(
            "Could not locate the PAYMENT section after addresses."
        )

    billing_center = (
        billing["left"]
        + billing["width"] / 2
    )

    delivery_center = (
        delivery["left"]
        + delivery["width"] / 2
    )

    split_x = int(
        (
            billing_center
            + delivery_center
        )
        / 2
    )

    start_y = max(
        billing["top"] + billing["height"],
        delivery["top"] + delivery["height"],
    )

    end_y = payment["top"]

    left_words = []
    right_words = []

    for index, raw_text in enumerate(data["text"]):
        value = raw_text.strip()

        if not value:
            continue

        left = int(data["left"][index])
        top = int(data["top"][index])
        width = int(data["width"][index])

        center_x = left + width / 2

        if top <= start_y:
            continue

        if top >= end_y:
            continue

        word = {
            "text": value,
            "left": left,
            "top": top,
        }

        if center_x < split_x:
            left_words.append(word)
        else:
            right_words.append(word)

    tolerance = max(
        8,
        processed.height // 350,
    )

    billing_lines = group_words_into_lines(
        left_words,
        tolerance,
    )

    delivery_lines = group_words_into_lines(
        right_words,
        tolerance,
    )

    invoice_address = parse_address_lines(
        billing_lines,
        "billing",
    )

    delivery_address = parse_address_lines(
        delivery_lines,
        "delivery",
    )

    return (
        invoice_address,
        delivery_address,
    )


def parse_payment(text: str) -> PaymentData:
    section = section_between(
        text,
        "PAYMENT",
        ["ITEMS"],
    )

    supported_methods = [
        "Bank Transfer",
        "Credit Card",
        "SEPA Direct Debit",
    ]

    method = None

    for candidate in supported_methods:
        if re.search(
            re.escape(candidate),
            section,
            re.IGNORECASE,
        ):
            method = candidate
            break

    if not method:
        labelled = re.search(
            r"Method\s*:\s*([A-Za-z ]+?)(?:Status|$)",
            section,
            re.IGNORECASE,
        )

        if labelled:
            method = labelled.group(1).strip()

    if not method:
        raise ExtractionError(
            "Could not extract payment method."
        )

    status_match = re.search(
        r"\b(PAID|UNPAID)\b",
        section,
        re.IGNORECASE,
    )

    if not status_match:
        raise ExtractionError(
            "Could not extract payment status."
        )

    status = status_match.group(1).upper()

    payment_date = None

    if status == "PAID":
        date_match = re.search(
            r"\b(\d{4}-\d{2}-\d{2})\b",
            section,
        )

        if not date_match:
            raise ExtractionError(
                "PAID order has no payment date."
            )

        payment_date = datetime.strptime(
            date_match.group(1),
            "%Y-%m-%d",
        ).date()

    return PaymentData(
        method=method,
        status=status,
        payment_date=payment_date,
    )


def parse_items_from_text(
    text: str,
) -> list[OrderItem]:
    """
    First attempt: parse item rows from whole-page OCR.
    """

    items = []

    row_pattern = re.compile(
        r"""
        (?P<sku>
            [A-Z0-9]+
            (?:-[A-Z0-9]+){1,}
        )
        \s+
        (?P<description>
            [A-Za-z][A-Za-z0-9 &'()/.\-]+?
        )
        \s+
        (?P<qty>\d+(?:\.\d+)?)
        \s+
        (?:
            pcs? |
            pieces? |
            ea |
            each |
            units?
        )
        \s+
        (?P<unit_price>\d+\.\d{2})
        \s+
        (?P<discount>\d+(?:\.\d+)?)%
        \s+
        (?P<vat>\d+(?:\.\d+)?)%
        \s+
        (?P<line_total>\d+\.\d{2})
        """,
        re.VERBOSE | re.IGNORECASE,
    )

    for line in text.splitlines():
        match = row_pattern.search(line)

        if not match:
            continue

        item = OrderItem(
            sku=match.group("sku").upper(),
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

        source_line_total = Decimal(
            match.group("line_total")
        )

        if (
            item.calculated_net()
            != source_line_total
        ):
            raise ExtractionError(
                f"Line total mismatch for {item.sku}: "
                f"calculated {item.calculated_net()}, "
                f"source {source_line_total}"
            )

        items.append(item)

    return items


def normalize_sku_numeric_suffix(
    value: str,
) -> str:
    """
    Normalize OCR ambiguity only inside a numeric SKU suffix.

    Example:
        CHR-ERG-O1 -> CHR-ERG-01

    Alphabetic SKU sections are not silently corrected.
    """
    value = re.sub(
        r"[^A-Z0-9_-]",
        "",
        value.upper(),
    )

    parts = re.split(
        r"([-_])",
        value,
    )

    if len(parts) >= 3:
        suffix = parts[-1]

        if re.fullmatch(
            r"[0-9OIL]+",
            suffix,
        ):
            suffix = (
                suffix
                .replace("O", "0")
                .replace("I", "1")
                .replace("L", "1")
            )

            parts[-1] = suffix

            return "".join(parts)

    # OCR modes sometimes remove separators.
    if len(value) >= 2:
        tail = value[-2:]

        if re.fullmatch(
            r"[0-9OIL]{2}",
            tail,
        ):
            tail = (
                tail
                .replace("O", "0")
                .replace("I", "1")
                .replace("L", "1")
            )

            value = value[:-2] + tail

    return value


def group_positions(
    indexes,
    max_gap: int = 6,
):
    if len(indexes) == 0:
        return []

    groups = [[indexes[0]]]

    for index in indexes[1:]:
        if (
            index
            <= groups[-1][-1] + max_gap
        ):
            groups[-1].append(index)
        else:
            groups.append([index])

    return [
        int(
            sum(group) / len(group)
        )
        for group in groups
    ]


def detect_table_lines(
    image: Image.Image,
):
    array = np.array(
        image.convert("L")
    )

    dark = array < 150

    vertical_counts = dark.sum(axis=0)
    horizontal_counts = dark.sum(axis=1)

    vertical_indexes = np.where(
        vertical_counts
        > dark.shape[0] * 0.25
    )[0]

    horizontal_indexes = np.where(
        horizontal_counts
        > dark.shape[1] * 0.35
    )[0]

    return (
        group_positions(
            vertical_indexes
        ),
        group_positions(
            horizontal_indexes
        ),
    )


def prepare_cell(
    cell: Image.Image,
) -> Image.Image:
    cell = ImageOps.grayscale(cell)
    cell = ImageOps.autocontrast(cell)

    cell = cell.resize(
        (
            cell.width * 2,
            cell.height * 2,
        ),
        Image.Resampling.LANCZOS,
    )

    return ImageEnhance.Contrast(
        cell
    ).enhance(1.5)


def cell_text(
    cell: Image.Image,
) -> str:
    return pytesseract.image_to_string(
        cell,
        lang="eng",
        config="--oem 3 --psm 7",
    ).strip()


def cell_number(
    cell: Image.Image,
) -> str:
    return pytesseract.image_to_string(
        cell,
        lang="eng",
        config=(
            "--oem 3 --psm 7 "
            "-c tessedit_char_whitelist=0123456789.,%"
        ),
    ).strip()


def cell_sku(
    cell: Image.Image,
    psm: int,
) -> str:
    return pytesseract.image_to_string(
        cell,
        lang="eng",
        config=(
            f"--oem 3 --psm {psm} "
            "-c tessedit_char_whitelist="
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        ),
    ).strip()


def decimal_value(
    value: str,
    field_name: str,
) -> Decimal:
    cleaned = (
        value
        .replace("%", "")
        .replace(",", ".")
        .strip()
    )

    try:
        return Decimal(cleaned)

    except Exception as exc:
        raise ExtractionError(
            f"Invalid numeric OCR for {field_name}: {value!r}"
        ) from exc


def extract_items_from_grid(
    processed: Image.Image,
) -> list[OrderItem]:
    """
    Fallback for table layouts that whole-page OCR cannot read.
    """

    data = pytesseract.image_to_data(
        processed,
        lang="eng",
        config="--oem 3 --psm 4",
        output_type=Output.DICT,
    )

    items_heading = find_ocr_word(
        data,
        "ITEMS",
    )

    if not items_heading:
        raise ExtractionError(
            "Could not locate ITEMS table."
        )

    net_heading = find_ocr_word(
        data,
        "NET",
        min_y=items_heading["top"] + 20,
    )

    if not net_heading:
        raise ExtractionError(
            "Could not locate totals after ITEMS."
        )

    top = max(
        0,
        items_heading["top"] - 10,
    )

    bottom = net_heading["top"] - 5

    region = processed.crop(
        (
            0,
            top,
            processed.width,
            bottom,
        )
    )

    if region.width < 4000:
        scale = 4
    else:
        scale = 2

    region = region.resize(
        (
            region.width * scale,
            region.height * scale,
        ),
        Image.Resampling.LANCZOS,
    )

    region = ImageOps.autocontrast(
        region.convert("L")
    )

    region = ImageEnhance.Contrast(
        region
    ).enhance(1.5)

    vertical, horizontal = (
        detect_table_lines(region)
    )

    column_count = (
        len(vertical) - 1
    )

    if column_count == 7:
        roles = [
            "sku",
            "description",
            "quantity",
            "unit_price",
            "vat",
            "discount",
            "line_total",
        ]

    elif column_count == 9:
        roles = [
            "row_number",
            "sku",
            "description",
            "quantity",
            "unit",
            "unit_price",
            "discount",
            "vat",
            "line_total",
        ]

    else:
        raise ManualReviewRequired(
            "Could not safely understand item-table columns. "
            f"Detected {column_count} columns."
        )

    if len(horizontal) < 3:
        raise ExtractionError(
            "Not enough table rows detected."
        )

    items = []

    # row 0 is the header, so start at row 1.
    for row_index in range(
        1,
        len(horizontal) - 1,
    ):
        top = horizontal[row_index] + 8
        bottom = horizontal[row_index + 1] - 8

        cells = {}

        for column_index, role in enumerate(roles):
            left = vertical[column_index] + 8
            right = vertical[column_index + 1] - 8

            cell = region.crop(
                (
                    left,
                    top,
                    right,
                    bottom,
                )
            )

            cells[role] = prepare_cell(
                cell
            )

        sku_candidate_7 = normalize_sku_numeric_suffix(
            cell_sku(
                cells["sku"],
                7,
            )
        )

        sku_candidate_8 = normalize_sku_numeric_suffix(
            cell_sku(
                cells["sku"],
                8,
            )
        )

        compact_7 = re.sub(
            r"[-_]",
            "",
            sku_candidate_7,
        )

        compact_8 = re.sub(
            r"[-_]",
            "",
            sku_candidate_8,
        )

        if (
            compact_7
            and compact_8
            and compact_7 != compact_8
        ):
            raise ManualReviewRequired(
                "Ambiguous SKU OCR. "
                f"Candidates: {sku_candidate_7!r}, "
                f"{sku_candidate_8!r}"
            )

        sku = (
            sku_candidate_7
            or sku_candidate_8
        )

        if not sku:
            raise ManualReviewRequired(
                "Could not read SKU."
            )

        description = cell_text(
            cells["description"]
        ).strip()

        if not description:
            raise ExtractionError(
                f"Missing description for {sku}."
            )

        unit_price = decimal_value(
            cell_number(
                cells["unit_price"]
            ),
            "unit net price",
        )

        vat = decimal_value(
            cell_number(
                cells["vat"]
            ),
            "VAT",
        )

        discount = decimal_value(
            cell_number(
                cells["discount"]
            ),
            "discount",
        )

        line_total = decimal_value(
            cell_number(
                cells["line_total"]
            ),
            "line total",
        )

        quantity_raw = cell_number(
            cells["quantity"]
        )

        try:
            quantity = decimal_value(
                quantity_raw,
                "quantity",
            )

        except ExtractionError:
            # Quantity may be mathematically recoverable from
            # line total, unit price and discount.
            multiplier = (
                Decimal("1")
                - discount / Decimal("100")
            )

            denominator = (
                unit_price
                * multiplier
            )

            if denominator <= 0:
                raise ManualReviewRequired(
                    f"Could not recover quantity for {sku}."
                )

            quantity = (
                line_total
                / denominator
            )

            if (
                quantity
                != quantity.to_integral_value()
                or quantity <= 0
            ):
                raise ManualReviewRequired(
                    f"Ambiguous quantity for {sku}."
                )

        item = OrderItem(
            sku=sku,
            description=description,
            quantity=quantity,
            unit_net_price=unit_price,
            vat_percent=vat,
            discount_percent=discount,
        )

        if (
            item.calculated_net()
            != line_total
        ):
            raise ExtractionError(
                f"Line total mismatch for {sku}: "
                f"calculated {item.calculated_net()}, "
                f"source {line_total}"
            )

        items.append(item)

    if not items:
        raise ExtractionError(
            "No valid item rows found."
        )

    return items


def extract_order(
    image_path: str | Path,
) -> OrderData:
    """
    Convert an order image into the generic OrderData model.
    """

    pytesseract.pytesseract.tesseract_cmd = (
        TESSERACT_PATH
    )

    image_path = Path(image_path)

    if not image_path.exists():
        raise FileNotFoundError(
            f"Order image not found: {image_path}"
        )

    image = Image.open(image_path)

    processed = preprocess_image(
        image
    )

    raw_text = run_ocr(
        processed,
        psm=6,
    )

    text = normalize_text(
        raw_text
    )

    # Useful evidence/debug artifact.
    log_path = Path(
        "artifacts/logs/latest_extraction_ocr.txt"
    )

    log_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    log_path.write_text(
        text,
        encoding="utf-8",
    )

    # -------------------------
    # Header
    # -------------------------

    external_reference = find_required(
        r"\b([A-Z]{2,}-\d{4}-\d{4}-[A-Z0-9]+)\b",
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

    customer_id_match = re.search(
        r"\b(CUST-[A-Z0-9_-]+)\b",
        text,
        re.IGNORECASE,
    )

    source_customer_id = (
        customer_id_match.group(1)
        if customer_id_match
        else None
    )

    currency = find_required(
        r"\b(EUR|USD|GBP)\b",
        text,
        "currency",
        re.IGNORECASE,
    ).upper()

    # -------------------------
    # Addresses
    # -------------------------

    (
        invoice_address,
        delivery_address,
    ) = extract_addresses(
        processed
    )

    # -------------------------
    # Customer
    # -------------------------

    customer_fields = extract_customer_fields(
        text,
        invoice_address.company,
    )

    customer = CustomerData(
        source_customer_id=source_customer_id,
        company=customer_fields["company"],
        first_name=customer_fields["first_name"],
        last_name=customer_fields["last_name"],
        alias=customer_fields["alias"],
        email=customer_fields["email"],
        phone=customer_fields["phone"],
        invoice_address=invoice_address,
        delivery_address=delivery_address,
    )

    # -------------------------
    # Payment
    # -------------------------

    payment = parse_payment(
        text
    )

    # -------------------------
    # Items
    # -------------------------

    items = parse_items_from_text(
        text
    )

    if not items:
        items = extract_items_from_grid(
            processed
        )

    # -------------------------
    # Totals
    # -------------------------

    money_values = re.findall(
        rf"\b{re.escape(currency)}\s+(\d+\.\d{{2}})",
        text,
        re.IGNORECASE,
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