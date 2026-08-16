from src.fakturama import FakturamaAutomation, FakturamaError


def main():
    try:
        fakturama = FakturamaAutomation().connect()

        print("Connected to Fakturama.")
        print(
            f"Window title: {fakturama.window.window_text()}"
        )

        print(
            "Order editor before action:",
            fakturama.is_order_editor_open(),
        )

        print("Opening New Order...")

        order_number = fakturama.open_new_order()

        print("VERIFIED: Order editor is open.")
        print(f"Generated Order No.: {order_number}")

        screenshot = fakturama.capture_screenshot(
            "artifacts/screenshots/"
            "new_order_open.png"
        )

        print(f"Screenshot saved to: {screenshot}")

    except FakturamaError as exc:
        print(f"ERROR: {exc}")


if __name__ == "__main__":
    main()