from src.fakturama import FakturamaAutomation, FakturamaError


def main():
    try:
        fakturama = FakturamaAutomation().connect()

        print("Connected to Fakturama.")
        print(
            f"Window title: {fakturama.window.window_text()}"
        )

        order_open = fakturama.is_order_editor_open()

        print(f"Order editor detected: {order_open}")

        if order_open:
            order_number = fakturama.find_generated_order_number()

            print(f"Generated Order No.: {order_number}")

        screenshot = fakturama.capture_screenshot(
            "artifacts/screenshots/current_state.png"
        )

        print(f"Screenshot saved to: {screenshot}")

    except FakturamaError as exc:
        print(f"ERROR: {exc}")


if __name__ == "__main__":
    main()