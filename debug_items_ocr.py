import sys
from pathlib import Path

import pytesseract
from pytesseract import Output
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


TESSERACT_PATH = r"D:\Tesseract-OCR\tesseract.exe"


if len(sys.argv) != 2:
    print(
        "Usage: python debug_items_ocr.py <image_path>"
    )
    raise SystemExit(1)


INPUT_PATH = Path(sys.argv[1])

OUTPUT_IMAGE = Path(
    "artifacts/screenshots/items_processed.png"
)


def preprocess(image: Image.Image) -> Image.Image:
    image = ImageOps.grayscale(image)

    # Upscale because the supplied assessment image is small.
    image = image.resize(
        (
            image.width * 4,
            image.height * 4,
        ),
        Image.Resampling.LANCZOS,
    )

    image = ImageOps.autocontrast(image)

    image = ImageEnhance.Contrast(
        image
    ).enhance(1.8)

    image = image.filter(
        ImageFilter.SHARPEN
    )

    return image


def find_word(data, target, min_y=0):
    """
    Find a word using Tesseract's positioned OCR output.
    """
    target = target.upper()

    matches = []

    for i, text in enumerate(data["text"]):
        text = text.strip().upper()

        if text != target:
            continue

        top = int(data["top"][i])

        if top < min_y:
            continue

        try:
            confidence = float(data["conf"][i])
        except ValueError:
            confidence = -1

        matches.append(
            {
                "left": int(data["left"][i]),
                "top": top,
                "width": int(data["width"][i]),
                "height": int(data["height"][i]),
                "conf": confidence,
            }
        )

    if not matches:
        return None

    return max(
        matches,
        key=lambda item: item["conf"],
    )


def main():
    pytesseract.pytesseract.tesseract_cmd = (
        TESSERACT_PATH
    )

    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"Image not found: {INPUT_PATH}"
        )

    print("Reading:", INPUT_PATH)

    image = Image.open(INPUT_PATH)

    processed = preprocess(image)

    data = pytesseract.image_to_data(
        processed,
        lang="eng",
        config="--oem 3 --psm 4",
        output_type=Output.DICT,
    )

    items_heading = find_word(
        data,
        "ITEMS",
    )

    if items_heading is None:
        raise RuntimeError(
            "Could not dynamically locate the ITEMS heading."
        )

    net_heading = find_word(
        data,
        "NET",
        min_y=items_heading["top"] + 20,
    )

    if net_heading is None:
        raise RuntimeError(
            "Could not dynamically locate the NET TOTAL heading."
        )

    print(
        "ITEMS heading:",
        items_heading,
    )

    print(
        "NET heading:",
        net_heading,
    )

    # Crop vertically between ITEMS and NET TOTAL.
    # Full page width is retained.
    top = max(
        0,
        items_heading["top"] - 10,
    )

    bottom = net_heading["top"] - 5

    item_region = processed.crop(
        (
            0,
            top,
            processed.width,
            bottom,
        )
    )

    # Upscale the isolated table once more.
    item_region = item_region.resize(
        (
            item_region.width * 2,
            item_region.height * 2,
        ),
        Image.Resampling.LANCZOS,
    )

    item_region = ImageEnhance.Contrast(
        item_region
    ).enhance(1.5)

    item_region = item_region.filter(
        ImageFilter.SHARPEN
    )

    OUTPUT_IMAGE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    item_region.save(
        OUTPUT_IMAGE
    )

    print(
        "\nSaved isolated item table to:",
        OUTPUT_IMAGE,
    )

    for psm in [4, 6, 11]:
        text = pytesseract.image_to_string(
            item_region,
            lang="eng",
            config=(
                f"--oem 3 --psm {psm} "
                "-c preserve_interword_spaces=1"
            ),
        )

        print(
            "\n"
            + "=" * 20
            + f" ITEMS PSM {psm} "
            + "=" * 20
        )

        print(text)


if __name__ == "__main__":
    main()