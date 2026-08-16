from pathlib import Path

import pytesseract
from PIL import Image


TESSERACT_PATH = r"D:\Tesseract-OCR\tesseract.exe"

FILES = [
    Path("artifacts/screenshots/table_cells/row_1_col_0.png"),
    Path("artifacts/screenshots/table_cells/row_2_col_0.png"),
]


def main():
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

    for path in FILES:
        print("\n==============================")
        print(path)
        print("==============================")

        image = Image.open(path)

        for psm in [6, 7, 8, 10, 13]:
            value = pytesseract.image_to_string(
                image,
                lang="eng",
                config=(
                    f"--oem 3 --psm {psm} "
                    "-c tessedit_char_whitelist="
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                ),
            ).strip()

            print(
                f"PSM {psm}: {value!r}"
            )


if __name__ == "__main__":
    main()