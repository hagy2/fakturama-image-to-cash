from datetime import date

from src.fakturama import FakturamaAutomation, FakturamaError


def main():
    try:
        fakturama = FakturamaAutomation().connect()

        print("Connected to Fakturama.")

        order_number = fakturama.open_new_order()

        print("VERIFIED: Order editor is open.")
        print(f"Generated Order No.: {order_number}")

        reference = "TEST-REF-123"

        observed_reference = (
            fakturama.set_customer_reference(reference)
        )

        print(
            f"VERIFIED: Cust.Ref. = {observed_reference}"
        )

        test_date = date(2026, 7, 14)

        observed_date = fakturama.set_order_date(
            test_date
        )

        print(
            f"VERIFIED: Order Date = {observed_date}"
        )

        print("Setting price mode to Net...")

        price_mode = fakturama.set_price_mode_net()


        print(
            f"VERIFIED: Price mode = {price_mode}"
        )

        date_after_price_change = (
            fakturama.get_order_date_text()
        )

        print(
            "Order Date after changing price mode:",
            date_after_price_change,
        )
        

        print("Checking VAT mode...")

        vat_mode = fakturama.verify_vat_mode(
            "With VAT"
        )

        print(
            f"VERIFIED: VAT mode = {vat_mode}"
        )

        screenshot = fakturama.capture_screenshot(
            "artifacts/screenshots/order_header_complete.png"
        )

        print(
            f"Screenshot saved to: {screenshot}"
        )

    except FakturamaError as exc:
        print(f"ERROR: {exc}")


if __name__ == "__main__":
    main()