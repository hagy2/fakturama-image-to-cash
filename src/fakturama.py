from pathlib import Path
from typing import Optional

from pywinauto import Desktop


class FakturamaError(RuntimeError):
    """Raised when Fakturama cannot be located or verified."""


class FakturamaAutomation:
    def __init__(self, title_pattern: str = r"^Fakturama.*"):
        self.title_pattern = title_pattern
        self.window = None

    def connect(self):
        """
        Connect to an already-running Fakturama window.
        """
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
        """Ensure that connect() has already succeeded."""
        if self.window is None:
            raise FakturamaError(
                "Not connected to Fakturama. Call connect() first."
            )

    @staticmethod
    def _usable_control(control) -> bool:
        """
        Ignore invisible and zero-sized controls.
        Fakturama exposes several internal SWT controls
        that are not actually usable on screen.
        """
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
        """Return currently visible and usable Fakturama controls."""
        self.require_connection()

        controls = []

        for control in self.window.descendants():
            if self._usable_control(control):
                controls.append(control)

        return controls

    def visible_texts(self) -> set[str]:
        """Return non-empty text belonging to visible controls."""
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
        """
        Verify that an Order editor is open by checking for
        several independent Order-screen labels.
        """
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
        """
        Find a visible generated Order number such as PO000001.
        """
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

    def capture_screenshot(self, path: str | Path):
        """Save a screenshot of the current Fakturama window."""
        self.require_connection()

        destination = Path(path)

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        image = self.window.capture_as_image()
        image.save(destination)

        return destination