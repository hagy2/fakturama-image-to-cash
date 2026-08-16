from pathlib import Path

import numpy as np
import pytesseract
from PIL import Image, ImageEnhance, ImageOps


TESSERACT_PATH = r"D:\Tesseract-OCR\tesseract.exe"

# This file was created by debug_items_ocr.py
INPUT_PATH = Path(
    "artifacts/screenshots/items_processed.png"
)

OUTPUT_DIR = Path(
    "artifacts/screenshots/table_cells"
)


def group_positions(indexes, max_gap=6):
    if len(indexes) == 0:
        return []

    groups = [[indexes[0]]]

    for index in indexes[1:]:
        if index <= groups[-1][-1] + max_gap:
            groups[-1].append(index)
        else:
            groups.append([index])

    return [
        int(sum(group) / len(group))
        for group in groups
    ]


def detect_table_lines(image):
    """
    Detect long horizontal and vertical table borders.
    """
    array = np.array(image)

    # True for dark pixels.
    dark = array < 150

    vertical_counts = dark.sum(axis=0)
    horizontal_counts = dark.sum(axis=1)

    # Table lines span a significant portion of the crop.
    vertical_indexes = np.where(
        vertical_counts > dark.shape[0] * 0.25
    )[0]

    horizontal_indexes = np.where(
        horizontal_counts > dark.shape[1] * 0.35
    )[0]

    vertical_lines = group_positions(
        vertical_indexes
    )

    horizontal_lines = group_positions(
        horizontal_indexes
    )

    return vertical_lines, horizontal_lines


def prepare_cell(cell):
    cell = ImageOps.grayscale(cell)
    cell = ImageOps.autocontrast(cell)

    cell = cell.resize(
        (
            cell.width * 2,
            cell.height * 2,
        ),
        Image.Resampling.LANCZOS,
    )

    cell = ImageEnhance.Contrast(
        cell
    ).enhance(1.5)

    return cell


def ocr_text(cell):
    return pytesseract.image_to_string(
        cell,
        lang="eng",
        config="--oem 3 --psm 7",
    ).strip()


def ocr_sku(cell):
    return pytesseract.image_to_string(
        cell,
        lang="eng",
        config=(
            "--oem 3 --psm 7 "
            "-c tessedit_char_whitelist="
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        ),
    ).strip()


def ocr_number(cell):
    return pytesseract.image_to_string(
        cell,
        lang="eng",
        config=(
            "--oem 3 --psm 7 "
            "-c tessedit_char_whitelist=0123456789.,%"
        ),
    ).strip()


def main():
    pytesseract.pytesseract.tesseract_cmd = (
        TESSERACT_PATH
    )

    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"Missing image: {INPUT_PATH}"
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    image = Image.open(
        INPUT_PATH
    ).convert("L")

    vertical_lines, horizontal_lines = (
        detect_table_lines(image)
    )

    print("VERTICAL LINES:")
    print(vertical_lines)

    print("\nHORIZONTAL LINES:")
    print(horizontal_lines)

    if len(vertical_lines) < 3:
        raise RuntimeError(
            "Not enough vertical table lines detected."
        )

    if len(horizontal_lines) < 3:
        raise RuntimeError(
            "Not enough horizontal table lines detected."
        )

    print("\nCELL OCR:\n")

    for row in range(
        len(horizontal_lines) - 1
    ):
        top = horizontal_lines[row] + 8
        bottom = horizontal_lines[row + 1] - 8

        if bottom <= top:
            continue

        print(f"ROW {row}")

        for col in range(
            len(vertical_lines) - 1
        ):
            left = vertical_lines[col] + 8
            right = vertical_lines[col + 1] - 8

            if right <= left:
                continue

            cell = image.crop(
                (left, top, right, bottom)
            )

            cell = prepare_cell(cell)

            path = OUTPUT_DIR / (
                f"row_{row}_col_{col}.png"
            )

            cell.save(path)

            print(
                f"  COL {col}:",
                "text=",
                repr(ocr_text(cell)),
                "| sku=",
                repr(ocr_sku(cell)),
                "| number=",
                repr(ocr_number(cell)),
            )

        print()


if __name__ == "__main__":
    main()