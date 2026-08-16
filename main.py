import sys

from src.extraction import (
    ExtractionError,
    ManualReviewRequired,
    extract_order,
)
from src.fakturama import (
    FakturamaAutomation,
    FakturamaError,
)


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: python main.py <order_image>"
        )
        raise SystemExit(1)

    image_path = sys.argv[1]

    print("===================================")
    print("FAKTURAMA IMAGE-TO-CASH AUTOMATION")
    print("===================================")

    # -------------------------------------------------
    # 1. Extract source order
    # -------------------------------------------------

    print(
        f"\nExtracting order from: {image_path}"
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

    print(
        "\nVERIFIED: Source order extracted."
    )

    print(
        f"External Reference: "
        f"{order.external_reference}"
    )

    print(
        f"Order Date: "
        f"{order.order_date}"
    )

    print(
        f"Customer: "
        f"{order.customer.company}"
    )

    print(
        f"Items: "
        f"{len(order.items)}"
    )

    print(
        f"Gross Total: "
        f"{order.currency} "
        f"{order.source_gross_total}"
    )

    # -------------------------------------------------
    # 2. Connect to Fakturama
    # -------------------------------------------------

    fakturama = FakturamaAutomation()

    try:
        fakturama.connect()

        print(
            "\nConnected to Fakturama."
        )

        # ---------------------------------------------
        # 3. Open New Order
        # ---------------------------------------------

        generated_order_number = (
            fakturama.open_new_order()
        )

        print(
            "VERIFIED: Order editor is open."
        )

        print(
            "Generated Order No.: "
            f"{generated_order_number}"
        )

        # ---------------------------------------------
        # 4. Customer Reference
        # ---------------------------------------------

        fakturama.set_customer_reference(
            order.external_reference
        )

        print(
            "VERIFIED: Cust.Ref. = "
            f"{order.external_reference}"
        )

        # ---------------------------------------------
        # 5. Order Date
        # ---------------------------------------------

        fakturama.set_order_date(
            order.order_date
        )

        print(
            "VERIFIED: Order Date = "
            f"{order.order_date}"
        )

        # ---------------------------------------------
        # 6. Net price mode
        # ---------------------------------------------

        print(
            "Setting price mode to Net..."
        )

        fakturama.set_price_mode_net()

        print(
            "VERIFIED: Price mode = Net"
        )

        # ---------------------------------------------
        # 7. VAT mode
        # ---------------------------------------------

        print(
            "Checking VAT mode..."
        )

        fakturama.verify_vat_mode(
            "With VAT"
        )

        print(
            "VERIFIED: VAT mode = With VAT"
        )

        # ---------------------------------------------
        # 8. Resolve debtor
        # ---------------------------------------------

        print(
            "\nResolving debtor..."
        )

        debtor_result = fakturama.resolve_debtor(
            order.customer,
            order.payment.method,
        )

        print(
            "VERIFIED: Debtor resolved."
        )

        print(
            "Debtor action:",
            debtor_result["action"],
        )

        print(
            "Fakturama Customer ID:",
            debtor_result["customer_id"],
        )

        # ---------------------------------------------
        # 9. Resolve products
        # ---------------------------------------------

        print(
            "\nResolving products..."
        )

        for item in order.items:
            print(
                f"\nResolving SKU: {item.sku}"
            )

            product_result = (
                fakturama.resolve_product(
                    item
                )
            )

            print(
                "VERIFIED: Product resolved."
            )

            print(
                "Product action:",
                product_result["action"],
            )

            print(
                "SKU:",
                product_result["sku"],
            )

            if (
                product_result["action"]
                == "created_and_selected"
            ):
                print(
                    "Created gross price:",
                    product_result[
                        "gross_price"
                    ],
                )

                print(
                    "VAT:",
                    product_result[
                        "vat_name"
                    ],
                )


        # ---------------------------------------------
        # 10. Complete and verify Order item lines
        # ---------------------------------------------

        print(
            "\nCompleting Order item lines..."
        )

        line_results = (
            fakturama.complete_order_lines(
                order
            )
        )

        for result in line_results:
            print(
                "\nVERIFIED: Order line complete."
            )
            print(
                "SKU:",
                result["sku"],
            )
            print(
                "Qty:",
                result["quantity"],
            )
            print(
                "U.Price:",
                result["unit_net_price"],
            )
            print(
                "VAT:",
                f'{result["vat_percent"]}%',
            )
            print(
                "Discount:",
                f'{result["discount_percent"]}%',
            )
            print(
                "Line Price:",
                result["line_net"],
            )

        print(
            "\nVerifying Order totals..."
        )

        totals = (
            fakturama.verify_order_totals(
                order
            )
        )

        print(
            "VERIFIED: Order totals match source."
        )

        print(
            "Total Net:",
            totals["net"],
        )

        print(
            "VAT:",
            totals["vat"],
        )

        print(
            "Total:",
            totals["total"],
        )

    except FakturamaError as exc:
        print(
            f"\nFAKTURAMA AUTOMATION FAILED: {exc}"
        )
        raise SystemExit(1)

    print(
        "\n==================================="
    )
    print(
        "ORDER HEADER + DEBTOR + PRODUCTS + LINES COMPLETE"
    )
    print(
        "==================================="
    )

    print(
        "\nNext stage: save Order, verify Documents, "
        "then create linked Invoice."
    )


if __name__ == "__main__":
    main()
