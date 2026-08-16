import sys

from src.extraction import (
    ExtractionError,
    ManualReviewRequired,
    extract_order,
)


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: python debug_extract.py <image_path>"
        )
        raise SystemExit(1)

    image_path = sys.argv[1]

    print(
        f"Extracting: {image_path}"
    )

    try:
        order = extract_order(
            image_path
        )

    except ManualReviewRequired as exc:
        print(
            f"\nMANUAL REVIEW REQUIRED: {exc}"
        )
        raise SystemExit(2)

    except ExtractionError as exc:
        print(
            f"\nEXTRACTION FAILED: {exc}"
        )
        raise SystemExit(1)

    print("\nEXTRACTION SUCCESS\n")

    print(
        order.model_dump_json(
            indent=2
        )
    )

    print(
        "\nFinancial problems:",
        order.validate_financials(),
    )


if __name__ == "__main__":
    main()