import sys
from pathlib import Path

import pytesseract
from PIL import Image


TESSERACT_PATH = r"D:\Tesseract-OCR\tesseract.exe"


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: python debug_ocr.py <image_path>"
        )
        raise SystemExit(1)

    image_path = Path(sys.argv[1])

    if not image_path.exists():
        raise FileNotFoundError(
            f"Image not found: {image_path}"
        )

    pytesseract.pytesseract.tesseract_cmd = (
        TESSERACT_PATH
    )

    image = Image.open(image_path)

    print(f"Reading image: {image_path}")
    print(
        f"Image size: {image.width} x {image.height}"
    )

    text = pytesseract.image_to_string(
        image,
        lang="eng",
        config="--oem 3 --psm 6",
    )

    print("\n========== RAW OCR ==========\n")
    print(text)
    print("=============================")


if __name__ == "__main__":
    main()