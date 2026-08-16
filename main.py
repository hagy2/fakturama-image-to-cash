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

        print(f"Setting Cust.Ref. to: {reference}")

        observed_reference = (
            fakturama.set_customer_reference(reference)
        )

        print(
            f"VERIFIED: Cust.Ref. = "
            f"{observed_reference}"
        )

        test_date = date(2026, 7, 14)

        print(f"Setting Order Date to: {test_date}")

        observed_date = fakturama.set_order_date(
            test_date
        )

        print(
            f"VERIFIED: Order Date = "
            f"{observed_date}"
        )

        screenshot = fakturama.capture_screenshot(
            "artifacts/screenshots/"
            "order_header_fields.png"
        )

        print(
            f"Screenshot saved to: {screenshot}"
        )

    except FakturamaError as exc:
        print(f"ERROR: {exc}")


if __name__ == "__main__":
    main()