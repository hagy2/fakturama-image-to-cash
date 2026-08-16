import calendar
import time

from datetime import date, datetime
from pathlib import Path
from typing import Optional

from pywinauto import Desktop, mouse
from pywinauto.keyboard import send_keys


class FakturamaError(RuntimeError):
    """Raised when Fakturama cannot be located or verified."""


class FakturamaAutomation:
    def __init__(self, title_pattern: str = r"^Fakturama.*"):
        self.title_pattern = title_pattern
        self.window = None

    def connect(self):
        """
        Connect to an already-running Fakturama window and ensure
        it is restored before controls are inspected.
        """
        try:
            window = Desktop(backend="win32").window(
                title_re=self.title_pattern
            )

            window.wait(
                "exists",
                timeout=10,
            )

            # Windows places minimized windows near (-32000, -32000).
            # Restore Fakturama before using control geometry.
            try:
                if window.is_minimized():
                    window.restore()
                    time.sleep(0.5)
            except Exception:
                pass

            try:
                window.set_focus()
                time.sleep(0.3)
            except Exception:
                pass

            window.wait(
                "visible",
                timeout=10,
            )

        except Exception as exc:
            raise FakturamaError(
                "Could not find or restore a visible Fakturama window."
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
        Verify that an Order editor is open by checking
        several independent labels.
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
        Find a visible generated Order 
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

    def _find_toolbar_button(self, button_name: str):
        """
        Find a toolbar button dynamically by semantic text.
        """
        self.require_connection()

        matches = []

        for control in self.visible_controls():
            try:
                if control.class_name() != "ToolbarWindow32":
                    continue

                if button_name in control.texts():
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
            return toolbar.button(button_name)

        except Exception as exc:
            raise FakturamaError(
                f"Found toolbar but could not resolve "
                f"button '{button_name}'."
            ) from exc

    def _wait_for_order_editor(
        self,
        timeout: float = 5.0,
    ) -> bool:
        """Wait until the Order editor can be verified."""
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if self.is_order_editor_open():
                return True

            time.sleep(0.25)

        return False

    def open_new_order(self):
        """
        Open a New Order and verify that the editor really appears.
        """
        self.require_connection()

        if self.is_order_editor_open():
            return self.find_generated_order_number()

        try:
            self.window.set_focus()
        except Exception:
            pass

        button = self._find_toolbar_button("Order")

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
                "artifacts/screenshots/open_order_failure.png"
            )

            raise FakturamaError(
                "Could not activate the Order toolbar button."
            ) from exc

        if not self._wait_for_order_editor():
            self.capture_screenshot(
                "artifacts/screenshots/open_order_failure.png"
            )

            raise FakturamaError(
                "Order action was triggered, but the "
                "Order editor could not be verified."
            )

        return self.find_generated_order_number()

    def _visible_controls_by_class(self, class_name: str):
        """
        Return usable controls matching a native Windows class.
        """
        return [
            control
            for control in self.visible_controls()
            if control.class_name() == class_name
        ]

    def _find_visible_label(self, label_text: str):
        """
        Find exactly one visible Static control with the requested text.
        """
        matches = []

        for control in self._visible_controls_by_class("Static"):
            try:
                if control.window_text().strip() == label_text:
                    matches.append(control)
            except Exception:
                continue

        if len(matches) == 0:
            raise FakturamaError(
                f"Could not find visible label '{label_text}'."
            )

        if len(matches) > 1:
            raise FakturamaError(
                f"Found multiple visible labels '{label_text}'."
            )

        return matches[0]

    def _find_edit_for_label(
        self,
        label_text: str,
        max_vertical_distance: int = 50,
    ):
     
        label = self._find_visible_label(label_text)
        label_rect = label.rectangle()

        candidates = []

        label_mid_y = (
            label_rect.top + label_rect.bottom
        ) / 2

        for edit in self._visible_controls_by_class("Edit"):
            try:
                rect = edit.rectangle()

                edit_mid_y = (
                    rect.top + rect.bottom
                ) / 2

                vertical_distance = abs(
                    edit_mid_y - label_mid_y
                )

                if vertical_distance > max_vertical_distance:
                    continue

                if rect.right <= label_rect.left:
                    continue

                horizontal_distance = max(
                    0,
                    rect.left - label_rect.right,
                )

                score = (
                    vertical_distance * 10
                    + horizontal_distance
                )

                candidates.append(
                    (score, edit)
                )

            except Exception:
                continue

        if not candidates:
            raise FakturamaError(
                f"Could not find an Edit control for '{label_text}'."
            )

        candidates.sort(
            key=lambda item: item[0]
        )

        return candidates[0][1]
    def set_order_date(self, order_date: date):
       
        self.require_connection()

        if not self.is_order_editor_open():
            raise FakturamaError(
                "Cannot set Order Date: "
                "no verified Order editor is open."
            )

        field = self._find_edit_for_label("Date")

        expected = (
            f"{order_date.strftime('%b')} "
            f"{order_date.day}, "
            f"{order_date.year}"
        )

        try:
            field.click_input()

            # Move to the first date segment.
            field.type_keys("{HOME}")

            # Month
            field.type_keys(
                f"{order_date.month:02d}"
            )

            # Move to day.
            field.type_keys("{RIGHT}")

            # Day
            field.type_keys(
                f"{order_date.day:02d}"
            )

            # Move to year.
            field.type_keys("{RIGHT}")

            # Year
            field.type_keys(
                str(order_date.year)
            )

            # Commit the control.
            field.type_keys("{TAB}")

            time.sleep(0.5)

        except Exception as exc:
            raise FakturamaError(
                "Could not set and commit the Order Date."
            ) from exc

        observed = field.window_text().strip()

        if observed != expected:
            raise FakturamaError(
                "Order Date verification failed after commit: "
                f"expected '{expected}', "
                f"observed '{observed}'."
            )

        return observed
    def get_order_date_text(self) -> str:
        """Read the currently displayed Order Date."""
        self.require_connection()

        field = self._find_edit_for_label("Date")

        return field.window_text().strip()

    def _open_order_date_calendar(self):
    
        self.require_connection()

        field = self._find_edit_for_label("Date")
        field_rect = field.rectangle()

        containers = []

        for control in self.visible_controls():
            try:
                if control.class_name() != "SWT_Window0":
                    continue

                rect = control.rectangle()

                contains_field = (
                    rect.left <= field_rect.left
                    and rect.top <= field_rect.top
                    and rect.right >= field_rect.right
                    and rect.bottom >= field_rect.bottom
                )

                extends_right = (
                    rect.right > field_rect.right
                )

                if not (
                    contains_field
                    and extends_right
                ):
                    continue

                area = (
                    rect.width()
                    * rect.height()
                )

                containers.append(
                    (area, rect)
                )

            except Exception:
                continue

        if not containers:
            raise FakturamaError(
                "Could not locate the Order Date calendar container."
            )

        containers.sort(
            key=lambda item: item[0]
        )

        container_rect = containers[0][1]

        click_x = int(
            (
                field_rect.right
                + container_rect.right
            )
            / 2
        )

        click_y = int(
            (
                container_rect.top
                + container_rect.bottom
            )
            / 2
        )

        mouse.click(
            button="left",
            coords=(click_x, click_y),
        )

        time.sleep(0.4)

    def set_order_date(self, target_date: date):
  
        self.require_connection()

        if not self.is_order_editor_open():
            raise FakturamaError(
                "Cannot set Order Date: "
                "no verified Order editor is open."
            )

        field = self._find_edit_for_label("Date")

        current_text = field.window_text().strip()

        try:
            current_date = datetime.strptime(
                current_text,
                "%b %d, %Y",
            ).date()

        except ValueError as exc:
            raise FakturamaError(
                "Could not parse current Fakturama Order Date: "
                f"'{current_text}'."
            ) from exc

        expected = (
            f"{target_date.strftime('%b')} "
            f"{target_date.day}, "
            f"{target_date.year}"
        )

        # Already correct.
        if current_date == target_date:
            return current_text

        self._open_order_date_calendar()

        month_delta = (
            (target_date.year - current_date.year) * 12
            + target_date.month
            - current_date.month
        )

        selected_year = current_date.year
        selected_month = current_date.month
        selected_day = current_date.day

        if month_delta > 0:
            for _ in range(month_delta):
                send_keys("{PGDN}")
                time.sleep(0.08)

                selected_month += 1

                if selected_month > 12:
                    selected_month = 1
                    selected_year += 1

                selected_day = min(
                    selected_day,
                    calendar.monthrange(
                        selected_year,
                        selected_month,
                    )[1],
                )

        elif month_delta < 0:
            for _ in range(abs(month_delta)):
                send_keys("{PGUP}")
                time.sleep(0.08)

                selected_month -= 1

                if selected_month < 1:
                    selected_month = 12
                    selected_year -= 1

                selected_day = min(
                    selected_day,
                    calendar.monthrange(
                        selected_year,
                        selected_month,
                    )[1],
                )

        day_delta = (
            target_date.day
            - selected_day
        )

        if day_delta > 0:
            send_keys(
                f"{{RIGHT {day_delta}}}"
            )

        elif day_delta < 0:
            send_keys(
                f"{{LEFT {abs(day_delta)}}}"
            )

        send_keys("{ENTER}")

        time.sleep(0.6)

        observed = field.window_text().strip()

        if observed != expected:
            raise FakturamaError(
                "Order Date verification failed: "
                f"expected '{expected}', "
                f"observed '{observed}'."
            )

        return observed

        return field.window_text().strip()
    def _find_combobox_with_text(self, text: str):
        """
        Find exactly one visible ComboBox whose current text matches.
        """
        matches = []

        for control in self._visible_controls_by_class("ComboBox"):
            try:
                if control.window_text().strip() == text:
                    matches.append(control)
            except Exception:
                continue

        if len(matches) == 0:
            raise FakturamaError(
                f"Could not find visible ComboBox with value '{text}'."
            )

        if len(matches) > 1:
            raise FakturamaError(
                f"Found multiple ComboBoxes with value '{text}'."
            )

        return matches[0]

    def set_price_mode_net(self):
        """
        Change the Order price mode from Gross to Net
        and verify the result.
        """
        self.require_connection()

        if not self.is_order_editor_open():
            raise FakturamaError(
                "Cannot change price mode: "
                "no verified Order editor is open."
            )

        # If it is already Net, do nothing.
        try:
            combo = self._find_combobox_with_text("Net")
            return combo.window_text().strip()
        except FakturamaError:
            pass

        combo = self._find_combobox_with_text("Gross")

        try:
            combo.select("Net")

        except Exception:
            try:
                combo.set_focus()
                combo.type_keys("{HOME}n{ENTER}")
            except Exception as exc:
                raise FakturamaError(
                    "Could not change price mode to Net."
                ) from exc

        observed = combo.window_text().strip()

        if observed != "Net":
            raise FakturamaError(
                "Price mode verification failed: "
                f"expected 'Net', observed '{observed}'."
            )

        return observed

    def verify_vat_mode(self, expected: str = "With VAT"):
        """
        Verify the Order VAT mode without changing it.
        """
        self.require_connection()

        if not self.is_order_editor_open():
            raise FakturamaError(
                "Cannot verify VAT mode: "
                "no verified Order editor is open."
            )

        combo = self._find_combobox_with_text(expected)

        observed = combo.window_text().strip()

        if observed != expected:
            raise FakturamaError(
                "VAT mode verification failed: "
                f"expected '{expected}', observed '{observed}'."
            )

        return observed

    def set_customer_reference(self, reference: str):
        """
        Set Cust.Ref. and verify the value by reading it back.
        """
        self.require_connection()

        if not self.is_order_editor_open():
            raise FakturamaError(
                "Cannot set customer reference: "
                "no verified Order editor is open."
            )

        field = self._find_edit_for_label(
            "Cust.Ref."
        )

        try:
            field.set_focus()
            field.set_edit_text(reference)

        except Exception as exc:
            raise FakturamaError(
                "Could not write the customer reference."
            ) from exc

        try:
            observed = field.window_text().strip()

        except Exception as exc:
            raise FakturamaError(
                "Could not read customer reference back."
            ) from exc

        if observed != reference:
            raise FakturamaError(
                "Customer reference verification failed: "
                f"expected '{reference}', "
                f"observed '{observed}'."
            )

        return observed

    def capture_screenshot(
        self,
        path: str | Path,
    ):
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