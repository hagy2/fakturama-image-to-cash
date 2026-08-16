from pathlib import Path
from typing import Optional
import time

from pywinauto import Desktop, mouse


class FakturamaError(RuntimeError):
    """Raised when Fakturama cannot be located or verified."""


class FakturamaAutomation:
    def __init__(self, title_pattern: str = r"^Fakturama.*"):
        self.title_pattern = title_pattern
        self.window = None

    def connect(self):
        """Connect to an already-running Fakturama window."""
        try:
            window = Desktop(backend="win32").window(
                title_re=self.title_pattern
            )

            window.wait(
                "exists visible",
                timeout=10,
            )

        except Exception as exc:
            raise FakturamaError(
                "Could not find a visible Fakturama window."
            ) from exc

        self.window = window
        return self

    def require_connection(self):
        if self.window is None:
            raise FakturamaError(
                "Not connected to Fakturama. Call connect() first."
            )

    @staticmethod
    def _usable_control(control) -> bool:
        try:
            if not control.is_visible():
                return False

            rectangle = control.rectangle()

            if rectangle.width() <= 0:
                return False

            if rectangle.height() <= 0:
                return False

            return True

        except Exception:
            return False

    def visible_controls(self):
        self.require_connection()

        controls = []

        for control in self.window.descendants():
            if self._usable_control(control):
                controls.append(control)

        return controls

    def visible_texts(self) -> set[str]:
        texts = set()

        for control in self.visible_controls():
            try:
                text = control.window_text().strip()
            except Exception:
                continue

            if text:
                texts.add(text)

        return texts

    def is_order_editor_open(self) -> bool:
        required = {
            "No.",
            "Date",
            "Cust.Ref.",
            "Addresses",
            "Items",
        }

        texts = self.visible_texts()

        return required.issubset(texts)

    def find_generated_order_number(self) -> Optional[str]:
        self.require_connection()

        for control in self.visible_controls():
            try:
                if control.class_name() != "Edit":
                    continue

                text = control.window_text().strip()

            except Exception:
                continue

            if not text:
                continue

            upper = text.upper()

            if upper.startswith("PO") and upper[2:].isdigit():
                return text

        return None

    def _find_toolbar_button(self, button_name: str):
       
        
        self.require_connection()

        matches = []

        for control in self.visible_controls():
            try:
                if control.class_name() != "ToolbarWindow32":
                    continue

                texts = control.texts()

                if button_name in texts:
                    matches.append(control)

            except Exception:
                continue

        if len(matches) == 0:
            raise FakturamaError(
                f"Could not find toolbar containing '{button_name}'."
            )

        if len(matches) > 1:
            raise FakturamaError(
                f"Found multiple toolbars containing '{button_name}'."
            )

        toolbar = matches[0]

        try:
            button = toolbar.button(button_name)
        except Exception as exc:
            raise FakturamaError(
                f"Found toolbar but could not resolve "
                f"button '{button_name}'."
            ) from exc

        return button

    def _wait_for_order_editor(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if self.is_order_editor_open():
                return True

            time.sleep(0.25)

        return False

    def open_new_order(self):
        """
        Open a New Order and verify that the Order editor actually appears.

        """

        self.require_connection()

        if self.is_order_editor_open():
            return self.find_generated_order_number()

        try:
            self.window.set_focus()
        except Exception:
            pass

        button = self._find_toolbar_button("Order")

        # First attempt: normal pywinauto interaction.
        try:
            button.click_input()
        except Exception:
            pass

        if self._wait_for_order_editor():
            return self.find_generated_order_number()

       
        try:
            rectangle = button.rectangle()
            point = rectangle.mid_point()

            mouse.click(
                button="left",
                coords=(point.x, point.y),
            )

        except Exception as exc:
            self.capture_screenshot(
                "artifacts/screenshots/"
                "open_order_failure.png"
            )

            raise FakturamaError(
                "Could not activate the Order toolbar button."
            ) from exc

        if not self._wait_for_order_editor():
            self.capture_screenshot(
                "artifacts/screenshots/"
                "open_order_failure.png"
            )

            raise FakturamaError(
                "Order action was triggered, but the "
                "Order editor could not be verified."
            )

        return self.find_generated_order_number()

    def capture_screenshot(self, path: str | Path):
        self.require_connection()

        destination = Path(path)

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        image = self.window.capture_as_image()
        image.save(destination)

        return destination