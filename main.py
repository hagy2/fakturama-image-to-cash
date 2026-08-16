from src.fakturama import FakturamaAutomation, FakturamaError


def main():
    try:
        fakturama = FakturamaAutomation().connect()

        print("Connected to Fakturama.")

        order_number = fakturama.open_new_order()

        print("VERIFIED: Order editor is open.")
        print(f"Generated Order No.: {order_number}")

        reference = "TEST-REF-123"

        print(
            f"Setting Cust.Ref. to: {reference}"
        )

        observed = fakturama.set_customer_reference(
            reference
        )

        print(
            f"VERIFIED: Cust.Ref. = {observed}"
        )

        screenshot = fakturama.capture_screenshot(
            "artifacts/screenshots/"
            "customer_reference_set.png"
        )

        print(
            f"Screenshot saved to: {screenshot}"
        )

    except FakturamaError as exc:
        print(f"ERROR: {exc}")


if __name__ == "__main__":
    main()