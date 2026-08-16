from pathlib import Path
import sys
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


TESSERACT_PATH = r"D:\Tesseract-OCR\tesseract.exe"

if len(sys.argv) != 2:
    print(
        "Usage: python debug_ocr_enhanced.py <image_path>"
    )
    raise SystemExit(1)

INPUT_PATH = Path(sys.argv[1])

PROCESSED_PATH = Path(
    "artifacts/screenshots/order_input_processed.png"
)

OUTPUT_DIR = Path("artifacts/logs")


def preprocess_image(image: Image.Image) -> Image.Image:
    """
    Prepare a low-resolution document image for OCR.
    """

    # Convert to grayscale.
    image = ImageOps.grayscale(image)

    # Upscale substantially because the supplied screenshot
    # contains small text.
    scale = 4

    image = image.resize(
        (
            image.width * scale,
            image.height * scale,
        ),
        Image.Resampling.LANCZOS,
    )

    # Improve contrast.
    image = ImageOps.autocontrast(image)

    image = ImageEnhance.Contrast(
        image
    ).enhance(1.8)

    # Sharpen characters.
    image = image.filter(
        ImageFilter.SHARPEN
    )

    image = image.filter(
        ImageFilter.SHARPEN
    )

    return image


def run_ocr(
    image: Image.Image,
    psm: int,
) -> str:
    return pytesseract.image_to_string(
        image,
        lang="eng",
        config=f"--oem 3 --psm {psm}",
    )


def main():
    pytesseract.pytesseract.tesseract_cmd = (
        TESSERACT_PATH
    )

    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"Missing input image: {INPUT_PATH}"
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    PROCESSED_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    original = Image.open(INPUT_PATH)

    print(
        "Original size:",
        original.size,
    )

    processed = preprocess_image(
        original
    )

    print(
        "Processed size:",
        processed.size,
    )

    processed.save(
        PROCESSED_PATH
    )

    print(
        "Processed image saved to:",
        PROCESSED_PATH,
    )

    # Test several layout assumptions.
    #
    # PSM 4  = columns / document layout
    # PSM 6  = one structured text block
    # PSM 11 = sparse text
    for psm in [4, 6, 11]:

        print(
            "\n"
            + "=" * 20
            + f" PSM {psm} "
            + "=" * 20
        )

        text = run_ocr(
            processed,
            psm,
        )

        print(text)

        output_path = (
            OUTPUT_DIR
            / f"ocr_psm_{psm}.txt"
        )

        output_path.write_text(
            text,
            encoding="utf-8",
        )

        print(
            f"Saved to: {output_path}"
        )


if __name__ == "__main__":
    main()