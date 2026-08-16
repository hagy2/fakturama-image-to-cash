import calendar
import re
import time

import numpy as np
import pytesseract
from pytesseract import Output

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from PIL import ImageEnhance, ImageGrab, ImageOps
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
        Always open a genuinely new Order editor.

        This is the implementation from the last committed workflow that was
        verified on the user's Fakturama installation.
        """
        self.require_connection()

        previous_no_handle = None

        if self.is_order_editor_open():
            try:
                previous_no_handle = (
                    self._find_edit_for_label("No.").handle
                )
            except Exception:
                previous_no_handle = None

        try:
            self.window.set_focus()
        except Exception:
            pass

        button = self._find_toolbar_button(
            "Order"
        )

        try:
            button.click_input()
        except Exception:
            rectangle = button.rectangle()
            point = rectangle.mid_point()

            mouse.click(
                button="left",
                coords=(point.x, point.y),
            )

        deadline = time.monotonic() + 8.0

        while time.monotonic() < deadline:
            if self.is_order_editor_open():
                try:
                    current_no_edit = (
                        self._find_edit_for_label("No.")
                    )

                    current_handle = (
                        current_no_edit.handle
                    )

                    if (
                        previous_no_handle is None
                        or current_handle
                        != previous_no_handle
                    ):
                        cust_ref = (
                            self._find_edit_for_label(
                                "Cust.Ref."
                            )
                        ).window_text().strip()

                        if cust_ref:
                            raise FakturamaError(
                                "A different Order editor opened, "
                                "but Cust.Ref. was already populated; "
                                "refusing to treat it as a fresh Order."
                            )

                        return (
                            self.find_generated_order_number()
                        )

                except FakturamaError:
                    raise
                except Exception:
                    pass

            time.sleep(0.25)

        self.capture_screenshot(
            "artifacts/screenshots/"
            "open_fresh_order_failure.png"
        )

        raise FakturamaError(
            "Order toolbar was triggered, but a genuinely "
            "new Order editor could not be verified."
        )

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


    # ============================================================
    # Debtor resolution / creation
    # ============================================================

    PAYMENT_METHOD_MAP = {
        "Bank Transfer": "Credit transfer",
        "Credit Card": "Credit card",
        "SEPA Direct Debit": "SEPA direct debit",
    }

    def _find_edits_for_label(
        self,
        label_text: str,
        count: int,
        max_vertical_distance: int = 50,
    ):
        label = self._find_visible_label(label_text)
        label_rect = label.rectangle()

        label_mid_y = (
            label_rect.top + label_rect.bottom
        ) / 2

        candidates = []

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
                    (score, rect.left, edit)
                )

            except Exception:
                continue

        if len(candidates) < count:
            raise FakturamaError(
                f"Could not find {count} Edit controls "
                f"for '{label_text}'."
            )

        candidates.sort(
            key=lambda item: (
                item[0],
                item[1],
            )
        )

        selected = [
            item[2]
            for item in candidates[:count]
        ]

        selected.sort(
            key=lambda control: control.rectangle().left
        )

        return selected

    def _find_combobox_for_label(
        self,
        label_text: str,
        max_vertical_distance: int = 50,
    ):
        label = self._find_visible_label(label_text)
        label_rect = label.rectangle()

        label_mid_y = (
            label_rect.top + label_rect.bottom
        ) / 2

        candidates = []

        for combo in self._visible_controls_by_class("ComboBox"):
            try:
                rect = combo.rectangle()
                combo_mid_y = (
                    rect.top + rect.bottom
                ) / 2

                vertical_distance = abs(
                    combo_mid_y - label_mid_y
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
                    (score, combo)
                )

            except Exception:
                continue

        if not candidates:
            raise FakturamaError(
                f"Could not find a ComboBox for '{label_text}'."
            )

        candidates.sort(
            key=lambda item: item[0]
        )

        return candidates[0][1]

    @staticmethod
    def _write_edit(control, value: str):
        value = "" if value is None else str(value)

        control.set_focus()
        control.set_edit_text(value)

        observed = control.window_text().strip()

        if observed != value:
            raise FakturamaError(
                "Edit verification failed: "
                f"expected '{value}', observed '{observed}'."
            )

        return observed

    @staticmethod
    def _select_combo_value(combo, value: str):
        try:
            items = combo.item_texts()
        except Exception:
            items = []

        if items and value not in items:
            raise FakturamaError(
                f"Required ComboBox value '{value}' does not exist. "
                f"Available values: {items}"
            )

        try:
            combo.select(value)

        except Exception as exc:
            raise FakturamaError(
                f"Could not select ComboBox value '{value}'."
            ) from exc

        observed = combo.window_text().strip()

        if observed != value:
            raise FakturamaError(
                "ComboBox verification failed: "
                f"expected '{value}', observed '{observed}'."
            )

        return observed

    def _ocr_phrase_candidates(
        self,
        phrase: str,
    ):
        self.require_connection()

        image = self.window.capture_as_image()

        data = pytesseract.image_to_data(
            image,
            lang="eng",
            config="--oem 3 --psm 6",
            output_type=Output.DICT,
        )

        target = phrase.lower().strip()
        lines = {}

        for index, raw_text in enumerate(data["text"]):
            word = raw_text.strip()

            if not word:
                continue

            key = (
                data["block_num"][index],
                data["par_num"][index],
                data["line_num"][index],
            )

            lines.setdefault(key, []).append(
                {
                    "text": word,
                    "left": int(data["left"][index]),
                    "top": int(data["top"][index]),
                    "width": int(data["width"][index]),
                    "height": int(data["height"][index]),
                }
            )

        matches = []

        for words in lines.values():
            words.sort(
                key=lambda item: item["left"]
            )

            line_text = " ".join(
                item["text"]
                for item in words
            )

            if target not in line_text.lower():
                continue

            left = min(
                item["left"]
                for item in words
            )
            top = min(
                item["top"]
                for item in words
            )
            right = max(
                item["left"] + item["width"]
                for item in words
            )
            bottom = max(
                item["top"] + item["height"]
                for item in words
            )

            matches.append(
                {
                    "text": line_text,
                    "left": left,
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                }
            )

        return matches

    def _ocr_click_phrase(
        self,
        phrase: str,
        near_control=None,
    ):
        candidates = self._ocr_phrase_candidates(
            phrase
        )

        if not candidates:
            raise FakturamaError(
                f"Could not locate visible text '{phrase}' with OCR."
            )

        window_rect = self.window.rectangle()

        if near_control is not None:
            anchor = near_control.rectangle()
            anchor_x = (
                anchor.left + anchor.right
            ) / 2
            anchor_y = (
                anchor.top + anchor.bottom
            ) / 2

            def score(candidate):
                center_x = (
                    window_rect.left
                    + candidate["left"]
                    + candidate["right"]
                ) / 2

                center_y = (
                    window_rect.top
                    + candidate["top"]
                    + candidate["bottom"]
                ) / 2

                return (
                    (center_x - anchor_x) ** 2
                    + (center_y - anchor_y) ** 2
                )

            candidates.sort(
                key=score
            )

        else:
            candidates.sort(
                key=lambda candidate: (
                    candidate["top"],
                    candidate["left"],
                )
            )

        candidate = candidates[0]

        click_x = int(
            window_rect.left
            + (
                candidate["left"]
                + candidate["right"]
            ) / 2
        )

        click_y = int(
            window_rect.top
            + (
                candidate["top"]
                + candidate["bottom"]
            ) / 2
        )

        mouse.click(
            button="left",
            coords=(click_x, click_y),
        )

        time.sleep(0.4)

    def _find_order_address_action_controls(self):
        """
        Locate the two verified Order address actions.

        Fakturama exposes these icons as empty Static controls directly
        underneath the semantic "Addresses" label:

        upper -> select existing debtor/address
        lower -> create new debtor
        """
        label = self._find_visible_label("Addresses")
        label_rect = label.rectangle()

        candidates = []

        for control in self._visible_controls_by_class("Static"):
            try:
                if control.window_text().strip():
                    continue

                rect = control.rectangle()

                if not (10 <= rect.width() <= 30):
                    continue

                if not (10 <= rect.height() <= 30):
                    continue

                # The icons are aligned with the right edge of the
                # Addresses label and stacked immediately underneath it.
                if rect.right < label_rect.right - 8:
                    continue

                if rect.right > label_rect.right + 8:
                    continue

                if rect.top < label_rect.bottom:
                    continue

                if rect.top > label_rect.bottom + 80:
                    continue

                candidates.append(
                    (
                        rect.top,
                        control,
                    )
                )

            except Exception:
                continue

        candidates.sort(
            key=lambda item: item[0]
        )

        if len(candidates) < 2:
            raise FakturamaError(
                "Could not locate the verified Order address actions."
            )

        return [
            candidates[0][1],
            candidates[1][1],
        ]

    def _address_selector_dialog(
        self,
        timeout: float = 5.0,
    ):
        dialog = Desktop(
            backend="win32"
        ).window(
            title="Select the address"
        )

        try:
            dialog.wait(
                "exists visible",
                timeout=timeout,
            )

        except Exception as exc:
            raise FakturamaError(
                "The 'Select the address' dialog did not appear."
            ) from exc

        return dialog

    def open_existing_debtor_selector(self):
        if not self.is_order_editor_open():
            raise FakturamaError(
                "Cannot open debtor selector: "
                "no verified Order editor is open."
            )

        controls = self._find_order_address_action_controls()

        try:
            controls[0].click_input()
        except Exception:
            rect = controls[0].rectangle()
            point = rect.mid_point()

            mouse.click(
                button="left",
                coords=(point.x, point.y),
            )

        return self._address_selector_dialog()

    def _cancel_address_selector(self, dialog):
        buttons = []

        for control in dialog.descendants():
            try:
                if (
                    control.class_name() == "Button"
                    and control.window_text().strip() == "Cancel"
                ):
                    buttons.append(control)
            except Exception:
                continue

        if len(buttons) != 1:
            raise FakturamaError(
                "Could not uniquely locate Cancel "
                "in the address selector."
            )

        buttons[0].click_input()
        time.sleep(0.3)

    @staticmethod
    def _selector_search_edit(dialog):
        edits = []

        for control in dialog.descendants():
            try:
                if (
                    control.class_name() == "Edit"
                    and control.is_visible()
                ):
                    edits.append(control)
            except Exception:
                continue

        if len(edits) != 1:
            raise FakturamaError(
                "Could not uniquely locate the debtor Search field."
            )

        return edits[0]

    @staticmethod
    def _normalize_customer_id(value: str) -> str:
        """
        Normalize common OCR ambiguity in Fakturama-generated IDs.

        The selector can OCR the row-number column together with the
        customer number, for example:

            "1 cusTo00001"

        so we search for the CUST token anywhere in the cell text
        instead of requiring it to start at character 0.
        """
        cleaned = re.sub(
            r"[^A-Z0-9]",
            "",
            value.upper(),
        )

        match = re.search(
            r"CUST[0-9OIL]+",
            cleaned,
        )

        if not match:
            return cleaned

        token = match.group(0)

        suffix = (
            token[4:]
            .replace("O", "0")
            .replace("I", "1")
            .replace("L", "1")
        )

        return "CUST" + suffix

    @staticmethod
    def _normalize_match_text(value: str) -> str:
        return re.sub(
            r"\s+",
            " ",
            str(value or "").strip(),
        ).casefold()

    def _ocr_selector_rows(self, dialog):
        """
        Read debtor rows from the unfiltered SWT selector.

        Rows are anchored on OCR-detected Fakturama Customer IDs rather
        than relying on Tesseract to keep the whole row in one OCR line.
        This is more robust because the row number, Customer ID and other
        cells can be split into separate OCR line groups.
        """
        image = dialog.capture_as_image()

        # Save evidence/debug screenshot automatically.
        debug_path = Path(
            "artifacts/screenshots/address_selector_latest.png"
        )
        debug_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        image.save(debug_path)

        def read_words(psm: int):
            data = pytesseract.image_to_data(
                image,
                lang="eng",
                config=f"--oem 3 --psm {psm}",
                output_type=Output.DICT,
            )

            result = []

            for index, raw_text in enumerate(data["text"]):
                value = raw_text.strip()

                if not value:
                    continue

                left = int(data["left"][index])
                top = int(data["top"][index])
                width = int(data["width"][index])
                height = int(data["height"][index])

                result.append(
                    {
                        "text": value,
                        "left": left,
                        "top": top,
                        "width": width,
                        "height": height,
                        "center_x": left + width / 2,
                        "center_y": top + height / 2,
                    }
                )

            return result

        # PSM 6 worked well in reconnaissance; PSM 11 is a fallback.
        words = read_words(6)

        def detect_headers(words_):
            no_headers = [
                w for w in words_
                if w["text"].casefold().rstrip(".") == "no"
            ]
            first_headers = [
                w for w in words_
                if w["text"].casefold() == "first"
            ]
            name_headers = [
                w for w in words_
                if w["text"].casefold() == "name"
            ]
            company_headers = [
                w for w in words_
                if w["text"].casefold() == "company"
            ]
            zip_headers = [
                w for w in words_
                if w["text"].casefold().rstrip(".") == "zip"
            ]
            city_headers = [
                w for w in words_
                if w["text"].casefold() == "city"
            ]

            if not (
                no_headers
                and first_headers
                and len(name_headers) >= 2
                and company_headers
                and zip_headers
                and city_headers
            ):
                return None

            company_header = company_headers[0]
            header_y = company_header["center_y"]

            same_line_names = [
                w
                for w in name_headers
                if abs(w["center_y"] - header_y) <= 20
            ]

            if len(same_line_names) < 2:
                return None

            same_line_names.sort(
                key=lambda w: w["center_x"]
            )

            first_name_center = (
                first_headers[0]["center_x"]
                + same_line_names[0]["center_x"]
            ) / 2

            return {
                "header_y": header_y,
                "centers": [
                    no_headers[0]["center_x"],
                    first_name_center,
                    same_line_names[1]["center_x"],
                    company_header["center_x"],
                    zip_headers[0]["center_x"],
                    city_headers[0]["center_x"],
                ],
            }

        header_info = detect_headers(words)

        if header_info is None:
            words = read_words(11)
            header_info = detect_headers(words)

        if header_info is None:
            raise FakturamaError(
                "Could not detect debtor selector table headers."
            )

        header_y = header_info["header_y"]
        centers = header_info["centers"]

        boundaries = [
            (
                centers[index]
                + centers[index + 1]
            ) / 2
            for index in range(
                len(centers) - 1
            )
        ]

        def column_index(center_x):
            for index, boundary in enumerate(boundaries):
                if center_x < boundary:
                    return index

            return len(centers) - 1

        # Find actual Customer ID OCR tokens anywhere below the header.
        id_anchors = []

        for word in words:
            if word["center_y"] <= header_y + 8:
                continue

            normalized = self._normalize_customer_id(
                word["text"]
            )

            if re.fullmatch(
                r"CUST\d+",
                normalized,
            ):
                id_anchors.append(
                    {
                        "customer_id": normalized,
                        "word": word,
                    }
                )

        if not id_anchors:
            # One more fallback: PSM 11 sometimes recognizes the ID when
            # PSM 6 does not.
            fallback_words = read_words(11)

            for word in fallback_words:
                normalized = self._normalize_customer_id(
                    word["text"]
                )

                if re.fullmatch(
                    r"CUST\d+",
                    normalized,
                ):
                    id_anchors.append(
                        {
                            "customer_id": normalized,
                            "word": word,
                        }
                    )

            if id_anchors:
                words = fallback_words

        rows = []

        for anchor in id_anchors:
            customer_id = anchor["customer_id"]
            id_word = anchor["word"]
            row_y = id_word["center_y"]

            # Collect all OCR words visually belonging to this row.
            row_words = [
                word
                for word in words
                if (
                    word["center_y"] > header_y + 8
                    and abs(
                        word["center_y"] - row_y
                    ) <= 18
                )
            ]

            columns = {
                0: [],
                1: [],
                2: [],
                3: [],
                4: [],
                5: [],
            }

            for word in row_words:
                idx = column_index(
                    word["center_x"]
                )

                columns[idx].append(word)

            values = []

            for idx in range(6):
                ordered = sorted(
                    columns[idx],
                    key=lambda item: item["left"],
                )

                values.append(
                    " ".join(
                        item["text"]
                        for item in ordered
                    ).strip()
                )

            # Customer ID is taken from the anchor itself, not from the
            # entire first column (which may also contain row number "1").
            rows.append(
                {
                    "customer_id": customer_id,
                    "first_name": values[1],
                    "last_name": values[2],
                    "company_display": values[3],
                    "zip_code": values[4],
                    "city": values[5],
                    "row_center_y": int(row_y),
                    "id_center_x": int(
                        id_word["center_x"]
                    ),
                }
            )

        # De-duplicate if both OCR modes saw the same Customer ID.
        unique = {}

        for row in rows:
            unique[row["customer_id"]] = row

        rows = list(
            unique.values()
        )

        if not rows:
            raw_debug = pytesseract.image_to_string(
                image,
                lang="eng",
                config="--oem 3 --psm 6",
            )

            log_path = Path(
                "artifacts/logs/address_selector_ocr_failure.txt"
            )
            log_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            log_path.write_text(
                raw_debug,
                encoding="utf-8",
            )

        return rows

    def _clear_selector_search(
        self,
        dialog,
    ):
        """
        Ensure the address selector is unfiltered before OCR.

        Fakturama remembers the previous Search value when the dialog
        is reopened, so failing to clear it can make a valid debtor
        appear to be missing.
        """
        search = self._selector_search_edit(
            dialog
        )

        current = search.window_text().strip()

        if current:
            search.set_focus()
            search.set_edit_text("")
            time.sleep(0.5)

        observed = search.window_text().strip()

        if observed:
            raise FakturamaError(
                "Could not clear the debtor selector Search field."
            )

        return search

    def _filter_exact_debtor_rows(
        self,
        rows,
        customer,
    ):
        """
        Apply the required exact debtor identity rules to already OCR-read
        selector rows. Keeping matching separate lets the caller retry OCR
        without accidentally entering the creation branch on one bad read.
        """
        expected_company = self._normalize_match_text(
            customer.company
        )
        expected_first = self._normalize_match_text(
            customer.first_name
        )
        expected_last = self._normalize_match_text(
            customer.last_name
        )
        expected_zip = self._normalize_match_text(
            customer.invoice_address.zip_code
        )
        expected_city = self._normalize_match_text(
            customer.invoice_address.city
        )

        candidates = []

        for row in rows:
            if (
                self._normalize_match_text(
                    row["first_name"]
                )
                != expected_first
            ):
                continue

            if (
                self._normalize_match_text(
                    row["last_name"]
                )
                != expected_last
            ):
                continue

            if (
                self._normalize_match_text(
                    row["zip_code"]
                )
                != expected_zip
            ):
                continue

            if (
                self._normalize_match_text(
                    row["city"]
                )
                != expected_city
            ):
                continue

            company_display = row[
                "company_display"
            ]

            normalized_company = (
                self._normalize_match_text(
                    company_display
                    .replace("...", "")
                    .rstrip(".")
                )
            )

            is_truncated = (
                "..." in company_display
            )

            if is_truncated:
                if not expected_company.startswith(
                    normalized_company
                ):
                    continue

                row[
                    "company_needs_post_verify"
                ] = True

            else:
                if normalized_company != expected_company:
                    continue

                row[
                    "company_needs_post_verify"
                ] = False

            candidates.append(row)

        return candidates

    def _find_exact_debtor_candidates(
        self,
        dialog,
        customer,
        *,
        clear_search: bool = True,
    ):
        """
        Find a single safe existing debtor.

        Primary path:
        exact Company/First Name/Last Name/ZIP/City using parsed OCR rows.

        Recovery path:
        Fakturama's SWT selector sometimes drops one text column from OCR.
        If that happens, allow one unique row only when Company, ZIP and
        City match and any visible First/Last Name values do not conflict.
        The selected Order is still post-verified before continuing.
        """
        if clear_search:
            self._clear_selector_search(
                dialog
            )

        expected_company = self._normalize_match_text(
            customer.company
        )
        expected_first = self._normalize_match_text(
            customer.first_name
        )
        expected_last = self._normalize_match_text(
            customer.last_name
        )
        expected_zip = self._normalize_match_text(
            customer.invoice_address.zip_code
        )
        expected_city = self._normalize_match_text(
            customer.invoice_address.city
        )

        best_rows = []

        for _ in range(3):
            rows = self._ocr_selector_rows(
                dialog
            )

            candidates = self._filter_exact_debtor_rows(
                rows,
                customer,
            )

            if candidates:
                return candidates

            if len(rows) > len(best_rows):
                best_rows = rows

            time.sleep(0.45)

        # Recovery for SWT OCR dropping a name/company column.
        recovery = []

        for row in best_rows:
            company_display = self._normalize_match_text(
                str(row.get("company_display", ""))
                .replace("...", "")
                .rstrip(".")
            )
            first = self._normalize_match_text(
                row.get("first_name", "")
            )
            last = self._normalize_match_text(
                row.get("last_name", "")
            )
            zip_code = self._normalize_match_text(
                row.get("zip_code", "")
            )
            city = self._normalize_match_text(
                row.get("city", "")
            )

            # Required location must be exact.
            if zip_code != expected_zip or city != expected_city:
                continue

            # Any visible name must agree. Missing OCR text is tolerated,
            # conflicting text is not.
            if first and first != expected_first:
                continue
            if last and last != expected_last:
                continue

            # Company must be exact, a visible prefix, or temporarily
            # missing from OCR. A conflicting visible company is rejected.
            company_ok = False

            if not company_display:
                company_ok = True
            elif company_display == expected_company:
                company_ok = True
            elif expected_company.startswith(company_display):
                company_ok = True
            else:
                expected_tokens = set(expected_company.split())
                visible_tokens = set(company_display.split())

                if (
                    expected_tokens
                    and visible_tokens
                    and len(expected_tokens & visible_tokens) >= 2
                ):
                    company_ok = True

            if not company_ok:
                continue

            recovered = dict(row)
            recovered["company_needs_post_verify"] = True
            recovery.append(recovered)

        if len(recovery) == 1:
            return recovery

        return recovery

    def _search_exact_debtor_candidates(
        self,
        dialog,
        customer,
    ):
        """
        Final existence-check retry using the selector's Search field, as
        required by the task. This is only used after unfiltered OCR could
        not establish a safe exact match.
        """
        search = self._selector_search_edit(
            dialog
        )

        search.set_focus()
        search.set_edit_text(
            customer.company
        )

        time.sleep(0.9)

        try:
            candidates = (
                self._find_exact_debtor_candidates(
                    dialog,
                    customer,
                    clear_search=False,
                )
            )
        finally:
            try:
                search.set_focus()
                search.set_edit_text("")
            except Exception:
                pass

        return candidates

    def _select_selector_row(
        self,
        dialog,
        row,
    ):
        """
        Select one OCR-grounded debtor row and confirm with a real mouse
        click on OK. SWT click_input() can report success without firing.
        """
        dialog_rect = dialog.rectangle()

        mouse.click(
            button="left",
            coords=(
                dialog_rect.left + int(row["id_center_x"]),
                dialog_rect.top + int(row["row_center_y"]),
            ),
        )

        buttons = []

        for control in dialog.descendants():
            try:
                if (
                    control.class_name() == "Button"
                    and control.window_text().strip() == "OK"
                ):
                    buttons.append(control)
            except Exception:
                continue

        if len(buttons) != 1:
            raise FakturamaError(
                "Could not uniquely locate OK in the address selector."
            )

        rect = buttons[0].rectangle()

        mouse.click(
            button="left",
            coords=(
                (rect.left + rect.right) // 2,
                (rect.top + rect.bottom) // 2,
            ),
        )

        time.sleep(0.8)

    def _verify_selected_order_company(
        self,
        expected_company: str,
    ):
        """
        Verify the selected debtor from the populated Order address.
        Fakturama can repaint slowly after the selector closes, so retry.
        """
        expected_normalized = self._normalize_match_text(
            expected_company
        )

        for _ in range(4):
            try:
                self._activate_open_order_editor()
            except Exception:
                pass

            for control in self.visible_controls():
                try:
                    if control.class_name() != "Edit":
                        continue

                    value = self._normalize_match_text(
                        control.window_text()
                    )

                    if (
                        expected_normalized
                        and expected_normalized in value
                    ):
                        return True
                except Exception:
                    continue

            try:
                addresses_label = self._find_visible_label("Addresses")
                items_label = self._find_visible_label("Items")

                window_rect = self.window.rectangle()
                addresses_rect = addresses_label.rectangle()
                items_rect = items_label.rectangle()

                top = max(
                    0,
                    addresses_rect.top - window_rect.top,
                )

                bottom = max(
                    top + 20,
                    items_rect.top - window_rect.top,
                )

                image = self.window.capture_as_image()

                region = image.crop(
                    (
                        0,
                        top,
                        image.width,
                        min(image.height, bottom),
                    )
                )

                raw = pytesseract.image_to_string(
                    region,
                    lang="eng",
                    config="--oem 3 --psm 6",
                )

                if expected_normalized in self._normalize_match_text(raw):
                    return True

            except Exception:
                pass

            time.sleep(0.4)

        raise FakturamaError(
            "MANUAL REVIEW REQUIRED: the debtor selector matched the "
            "required identity fields, but the populated Order address "
            f"could not confirm Company '{expected_company}'."
        )

    def _is_new_debtor_editor_open(self) -> bool:
        texts = self.visible_texts()

        return (
            "Customer ID" in texts
            and "Company" in texts
            and "Addresses" in texts
            and (
                "First Name Last Name" in texts
                or "Salutation" in texts
            )
        )

    def open_new_debtor_from_order(self):
        if not self.is_order_editor_open():
            raise FakturamaError(
                "Cannot create debtor: "
                "no verified Order editor is open."
            )

        try:
            self.window.set_focus()
        except Exception:
            pass

        # The lower green plus beside Addresses edits the current document's
        # address data; it is not the master-data creation action required by
        # the workflow.  Keep the Order open and use New Contact in the left
        # New panel to open a real New Debtor editor.
        clicked = False

        for control in self.visible_controls():
            try:
                if (
                    control.window_text().strip().casefold()
                    != "new contact"
                ):
                    continue

                control.click_input()
                clicked = True
                break

            except Exception:
                continue

        first_deadline = time.monotonic() + 4.0

        while time.monotonic() < first_deadline:
            if self._is_new_debtor_editor_open():
                return

            time.sleep(0.2)

        if not clicked or not self._is_new_debtor_editor_open():
            self._ocr_click_phrase(
                "New Contact"
            )

        deadline = time.monotonic() + 8.0

        while time.monotonic() < deadline:
            if self._is_new_debtor_editor_open():
                return

            time.sleep(0.2)

        self.capture_screenshot(
            "artifacts/screenshots/"
            "new_debtor_editor_failure.png"
        )

        raise FakturamaError(
            "New Contact was triggered, "
            "but the editor could not be verified."
        )

    def _set_address_role(
        self,
        role_text: str,
        desired: bool,
    ):
        for attempt in range(5):
            matches = []

            for control in self.visible_controls():
                try:
                    if (
                        control.class_name() == "Button"
                        and control.window_text().strip() == role_text
                    ):
                        matches.append(control)
                except Exception:
                    continue

            if matches:
                button = matches[0]

                try:
                    checked = bool(
                        button.get_check_state()
                    )
                except Exception:
                    checked = None

                if checked is None:
                    if desired:
                        button.click_input()
                    return

                if checked != desired:
                    button.click_input()

                return

            try:
                anchor = self._find_visible_label(
                    "address type"
                )

                point = anchor.rectangle().mid_point()

                mouse.scroll(
                    wheel_dist=-3,
                    coords=(point.x, point.y),
                )

                time.sleep(0.25)

            except Exception:
                break

        if desired:
            raise FakturamaError(
                f"Could not locate required '{role_text}' checkbox."
            )

    def _fill_current_address(
        self,
        address,
        email: str = "",
        telephone: str = "",
        invoice_role: bool = False,
        delivery_role: bool = False,
    ):
        self._write_edit(
            self._find_edit_for_label(
                "additional name"
            ),
            address.company or "",
        )

        self._write_edit(
            self._find_edit_for_label(
                "Street"
            ),
            address.street,
        )

        zip_edit, city_edit = self._find_edits_for_label(
            "ZIP - City",
            count=2,
        )

        self._write_edit(
            zip_edit,
            address.zip_code,
        )

        self._write_edit(
            city_edit,
            address.city,
        )

        country = self._find_combobox_for_label(
            "Country"
        )

        self._select_combo_value(
            country,
            address.country,
        )

        if email:
            self._write_edit(
                self._find_edit_for_label(
                    "E-Mail"
                ),
                email,
            )

        if telephone:
            self._write_edit(
                self._find_edit_for_label(
                    "Telephone"
                ),
                telephone,
            )

        self._set_address_role(
            "Invoice address",
            invoice_role,
        )

        self._set_address_role(
            "Delivery address",
            delivery_role,
        )

    def _activate_debtor_miscellaneous_tab(self):
        anchor = self._find_visible_label(
            "First Name Last Name"
        )

        self._ocr_click_phrase(
            "Miscellaneous",
            near_control=anchor,
        )

        deadline = time.monotonic() + 3.0

        while time.monotonic() < deadline:
            texts = self.visible_texts()

            if (
                "Alias name" in texts
                and "Payment" in texts
                and "Net or Gross" in texts
            ):
                return

            time.sleep(0.2)

        raise FakturamaError(
            "Could not activate debtor Miscellaneous tab."
        )

    def _activate_debtor_addresses_tab(self):
        anchor = self._find_visible_label(
            "First Name Last Name"
        )

        self._ocr_click_phrase(
            "Addresses",
            near_control=anchor,
        )

        deadline = time.monotonic() + 3.0

        while time.monotonic() < deadline:
            texts = self.visible_texts()

            if (
                "additional name" in texts
                and "Street" in texts
                and "ZIP - City" in texts
                and "Country" in texts
            ):
                return

            time.sleep(0.2)

        raise FakturamaError(
            "Could not activate debtor Addresses tab."
        )

    def _add_delivery_address_tab(self):
        plus_buttons = []

        for control in self._visible_controls_by_class(
            "Button"
        ):
            try:
                if control.window_text().strip() == "+":
                    plus_buttons.append(control)
            except Exception:
                continue

        if len(plus_buttons) != 1:
            raise FakturamaError(
                "Could not uniquely locate the debtor address '+' button."
            )

        plus_buttons[0].click_input()
        time.sleep(0.5)

    def _fill_debtor_identity(self, customer):
        self._write_edit(
            self._find_edit_for_label(
                "Company"
            ),
            customer.company,
        )

        first_name, last_name = self._find_edits_for_label(
            "First Name Last Name",
            count=2,
        )

        self._write_edit(
            first_name,
            customer.first_name or "",
        )

        self._write_edit(
            last_name,
            customer.last_name or "",
        )

    def _fill_debtor_miscellaneous(
        self,
        customer,
        source_payment_method: str,
    ):
        payment_value = self.PAYMENT_METHOD_MAP.get(
            source_payment_method
        )

        if payment_value is None:
            raise FakturamaError(
                "Unsupported payment method: "
                f"'{source_payment_method}'."
            )

        self._activate_debtor_miscellaneous_tab()

        alias_edit = self._find_edit_for_label(
            "Alias name"
        )

        self._write_edit(
            alias_edit,
            customer.alias or "",
        )

        discount_edit = self._find_edit_for_label(
            "Discount"
        )

        self._write_edit(
            discount_edit,
            "0%",
        )

        price_mode = self._find_combobox_for_label(
            "Net or Gross"
        )

        self._select_combo_value(
            price_mode,
            "Net",
        )

        payment_combo = self._find_combobox_for_label(
            "Payment"
        )

        try:
            self._select_combo_value(
                payment_combo,
                payment_value,
            )

        except FakturamaError as exc:
            raise FakturamaError(
                f"Required payment term '{payment_value}' "
                "is not configured in Fakturama. "
                "Create it under Data > terms of payment "
                "before running the debtor workflow."
            ) from exc

    def create_debtor(
        self,
        customer,
        source_payment_method: str,
    ) -> str:
        self.open_new_debtor_from_order()

        customer_id_edit = self._find_edit_for_label(
            "Customer ID"
        )

        generated_customer_id = (
            customer_id_edit.window_text().strip()
        )

        if not generated_customer_id:
            raise FakturamaError(
                "Fakturama did not provide a generated Customer ID."
            )

        self._fill_debtor_identity(
            customer
        )

        self._activate_debtor_addresses_tab()

        self._fill_current_address(
            customer.invoice_address,
            email=customer.email or "",
            telephone=customer.phone or "",
            invoice_role=True,
            delivery_role=False,
        )

        self._fill_debtor_miscellaneous(
            customer,
            source_payment_method,
        )

        self._activate_debtor_addresses_tab()

        self._add_delivery_address_tab()

        self._fill_current_address(
            customer.delivery_address,
            invoice_role=False,
            delivery_role=True,
        )

        save_button = self._find_toolbar_button(
            "Save"
        )

        try:
            save_button.click_input()
        except Exception as exc:
            raise FakturamaError(
                "Could not save the newly created debtor."
            ) from exc

        time.sleep(0.8)

        return generated_customer_id

    def _activate_open_order_editor(self):
        """
        Reactivate an already-open Order editor without relying on OCR.

        Fakturama's editor tabs are SWT controls and their small captions
        are not consistently exposed through Win32/OCR. Ctrl+PageUp cycles
        through open editor tabs and is layout-independent, so we verify
        after each switch and stop as soon as the Order editor is active.
        """
        if self.is_order_editor_open():
            return

        try:
            self.window.set_focus()
        except Exception:
            pass

        # Usually the newly saved Product editor is immediately beside
        # the Order tab, but cycle enough times to cover several open tabs.
        for _ in range(12):
            send_keys(
                "^{PGUP}",
                pause=0.12,
            )

            time.sleep(0.25)

            if self.is_order_editor_open():
                return

        # If tab ordering/focus behaves differently, try the other
        # direction before giving up.
        for _ in range(12):
            send_keys(
                "^{PGDN}",
                pause=0.12,
            )

            time.sleep(0.25)

            if self.is_order_editor_open():
                return

        # Preserve a screenshot for debugging instead of making an
        # unverified click.
        try:
            Path(
                "artifacts/screenshots"
            ).mkdir(
                parents=True,
                exist_ok=True,
            )

            self.window.capture_as_image().save(
                "artifacts/screenshots/"
                "order_reactivation_failure.png"
            )
        except Exception:
            pass

        raise FakturamaError(
            "Could not reactivate the open Order editor "
            "by cycling Fakturama editor tabs."
        )

    def resolve_debtor(
        self,
        customer,
        source_payment_method: str,
    ):
        """
        Resolve the debtor safely.

        The SWT selector truncates long Company names, so the automation:
        1. OCRs the unfiltered table.
        2. Requires exact First Name, Name, ZIP and City.
        3. Requires exact Company when fully visible.
        4. For a truncated Company, requires a matching visible prefix,
           selects the single candidate, then verifies the full Company
           from the populated Order address before continuing.
        5. 0 candidates -> create debtor, save once, re-resolve.
        6. >1 candidates -> manual review.
        """
        dialog = self.open_existing_debtor_selector()

        candidates = self._find_exact_debtor_candidates(
            dialog,
            customer,
        )

        # One OCR miss must not send an existing customer into the
        # New Debtor branch. Retry using the selector Search field.
        if len(candidates) == 0:
            candidates = (
                self._search_exact_debtor_candidates(
                    dialog,
                    customer,
                )
            )

        if len(candidates) > 1:
            self._cancel_address_selector(
                dialog
            )

            raise FakturamaError(
                "MANUAL REVIEW REQUIRED: "
                "multiple debtors match the required identity fields. "
                "Candidate IDs: "
                f"{[row['customer_id'] for row in candidates]}"
            )

        if len(candidates) == 1:
            row = candidates[0]

            self._select_selector_row(
                dialog,
                row,
            )

            self._verify_selected_order_company(
                customer.company
            )

            return {
                "action": "selected_existing",
                "customer_id": row["customer_id"],
            }

        # No exact candidate was parsed. Before creating anything,
        # check whether existing customer rows are visibly present. If so,
        # this is an ambiguous OCR miss and we MUST NOT create a duplicate.
        try:
            self._clear_selector_search(
                dialog
            )

            selector_image = dialog.capture_as_image()

            selector_raw = pytesseract.image_to_string(
                selector_image,
                lang="eng",
                config="--oem 3 --psm 6",
            )

            normalized_selector = re.sub(
                r"[^A-Z0-9]",
                "",
                selector_raw.upper(),
            )

            visible_customer_ids = re.findall(
                r"CUST[0-9OIL]+",
                normalized_selector,
            )

        except Exception:
            visible_customer_ids = []

        if visible_customer_ids:
            self._cancel_address_selector(
                dialog
            )

            raise FakturamaError(
                "MANUAL REVIEW REQUIRED: existing debtor rows are visible "
                "but no single exact match could be proven. Refusing to "
                "create a duplicate customer."
            )

        # Only a selector with no visible existing customer IDs may enter
        # the missing-debtor creation branch.
        self._cancel_address_selector(
            dialog
        )

        generated_customer_id = self.create_debtor(
            customer,
            source_payment_method,
        )

        self._activate_open_order_editor()

        dialog = self.open_existing_debtor_selector()

        candidates = self._find_exact_debtor_candidates(
            dialog,
            customer,
        )

        if len(candidates) != 1:
            self._cancel_address_selector(
                dialog
            )

            raise FakturamaError(
                "Debtor was created but could not be "
                "re-resolved to exactly one safe candidate. "
                "Candidate IDs: "
                f"{[row['customer_id'] for row in candidates]}"
            )

        row = candidates[0]

        if (
            row["customer_id"]
            != generated_customer_id.upper()
        ):
            self._cancel_address_selector(
                dialog
            )

            raise FakturamaError(
                "Re-resolved debtor ID does not match the "
                "Customer ID generated during creation: "
                f"created '{generated_customer_id}', "
                f"resolved '{row['customer_id']}'."
            )

        self._select_selector_row(
            dialog,
            row,
        )

        self._verify_selected_order_company(
            customer.company
        )

        return {
            "action": "created_and_selected",
            "customer_id": row["customer_id"],
        }



    # ============================================================
    # VAT resolution / creation
    # ============================================================

    def _vat_list_is_active(self) -> bool:
        """
        Confirm that the active lower data list is the VAT list.

        The SWT table rows themselves are not exposed reliably through
        Win32, so the list is grounded by its visible table headers.
        """
        image = self.window.capture_as_image()

        raw = pytesseract.image_to_string(
            image,
            lang="eng",
            config="--oem 3 --psm 6",
        )

        normalized = " ".join(
            raw.split()
        ).casefold()

        return all(
            token in normalized
            for token in (
                "standard",
                "name",
                "description",
                "value",
            )
        )

    def _open_vat_list(self):
        """
        Open Data > VATs using OCR-grounded visible text.

        When more than one VATs caption is visible (left navigation plus an
        already-open lower tab), prefer the left-most candidate, which is
        the Data navigation entry.  This is relative visual grounding, not
        a hardcoded screen coordinate.
        """
        if self._vat_list_is_active():
            return

        candidates = self._ocr_phrase_candidates(
            "VATs"
        )

        if not candidates:
            raise FakturamaError(
                "Could not locate Data > VATs."
            )

        window_rect = self.window.rectangle()

        candidates.sort(
            key=lambda candidate: (
                candidate["left"],
                candidate["top"],
            )
        )

        target = candidates[0]

        x = (
            window_rect.left
            + (
                target["left"]
                + target["right"]
            ) // 2
        )

        y = (
            window_rect.top
            + (
                target["top"]
                + target["bottom"]
            ) // 2
        )

        mouse.click(
            button="left",
            coords=(x, y),
        )

        deadline = time.monotonic() + 6.0

        while time.monotonic() < deadline:
            if self._vat_list_is_active():
                return

            time.sleep(0.25)

        raise FakturamaError(
            "Data > VATs was clicked, "
            "but the VAT list could not be verified."
        )

    def _vat_search_rows(
        self,
        vat_name: str,
    ):
        """
        Search the exact VAT name and OCR only the list rows below Search.

        Returns rows whose visible Name matches the requested VAT name.
        Each row contains its displayed numeric VAT value and click box.
        """
        search = self._find_edit_for_label(
            "Search:"
        )

        search.set_focus()
        search.set_edit_text(
            vat_name
        )

        time.sleep(0.8)

        image = self.window.capture_as_image()

        evidence_dir = Path(
            "artifacts/screenshots"
        )
        evidence_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        image.save(
            evidence_dir
            / "vat_search_latest.png"
        )

        gray = ImageOps.autocontrast(
            ImageOps.grayscale(image)
        )

        gray = ImageEnhance.Contrast(
            gray
        ).enhance(2.0)

        scale = 3

        enlarged = gray.resize(
            (
                gray.width * scale,
                gray.height * scale,
            )
        )

        data = pytesseract.image_to_data(
            enlarged,
            lang="eng",
            config="--oem 3 --psm 6",
            output_type=Output.DICT,
        )

        window_rect = self.window.rectangle()
        search_rect = search.rectangle()

        min_row_y = (
            search_rect.bottom
            - window_rect.top
            + 4
        ) * scale

        lines = {}

        for index, raw_text in enumerate(
            data["text"]
        ):
            value = raw_text.strip()

            if not value:
                continue

            left = int(
                data["left"][index]
            )
            top = int(
                data["top"][index]
            )
            width = int(
                data["width"][index]
            )
            height = int(
                data["height"][index]
            )

            cy = top + height / 2

            if cy < min_row_y:
                continue

            key = (
                data["block_num"][index],
                data["par_num"][index],
                data["line_num"][index],
            )

            lines.setdefault(
                key,
                [],
            ).append(
                {
                    "text": value,
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                    "cx": left + width / 2,
                    "cy": cy,
                }
            )

        expected_compact = re.sub(
            r"[^A-Z0-9]",
            "",
            vat_name.upper(),
        )

        matches = []

        for line_words in lines.values():
            ordered = sorted(
                line_words,
                key=lambda word: word["left"],
            )

            line_text = " ".join(
                word["text"]
                for word in ordered
            )

            line_compact = re.sub(
                r"[^A-Z0-9]",
                "",
                line_text.upper(),
            )

            if expected_compact not in line_compact:
                continue

            numeric_values = []

            for token in re.findall(
                r"-?\d+(?:[.,]\d+)?",
                line_text,
            ):
                try:
                    numeric_values.append(
                        Decimal(
                            token.replace(
                                ",",
                                ".",
                            )
                        )
                    )
                except Exception:
                    pass

            # The first number is normally the percentage inside the Name
            # ("VAT 19%").  The final number is the Value column.
            displayed_value = (
                numeric_values[-1]
                if numeric_values
                else None
            )

            left = min(
                word["left"]
                for word in ordered
            )
            top = min(
                word["top"]
                for word in ordered
            )
            right = max(
                word["left"]
                + word["width"]
                for word in ordered
            )
            bottom = max(
                word["top"]
                + word["height"]
                for word in ordered
            )

            matches.append(
                {
                    "line_text": line_text,
                    "value": displayed_value,
                    "left": int(
                        left / scale
                    ),
                    "top": int(
                        top / scale
                    ),
                    "right": int(
                        right / scale
                    ),
                    "bottom": int(
                        bottom / scale
                    ),
                }
            )

        return matches

    def _clear_vat_search(self):
        try:
            search = self._find_edit_for_label(
                "Search:"
            )

            search.set_focus()
            search.set_edit_text("")
        except Exception:
            pass

    def _is_vat_editor_open(self) -> bool:
        texts = self.visible_texts()

        return (
            "VAT code (E-Invoice)" in texts
            and "Value" in texts
            and "Name" in texts
        )

    def _open_vat_row(
        self,
        row,
    ):
        window_rect = self.window.rectangle()

        x = (
            window_rect.left
            + (
                row["left"]
                + row["right"]
            ) // 2
        )

        y = (
            window_rect.top
            + (
                row["top"]
                + row["bottom"]
            ) // 2
        )

        mouse.double_click(
            button="left",
            coords=(x, y),
        )

        deadline = time.monotonic() + 6.0

        while time.monotonic() < deadline:
            if self._is_vat_editor_open():
                return

            time.sleep(0.25)

        raise FakturamaError(
            "VAT row was opened, but the VAT editor "
            "could not be verified."
        )

    def _verify_open_vat(
        self,
        vat_name: str,
        vat_percent,
    ):
        expected_percent = Decimal(
            str(vat_percent)
        ).quantize(
            Decimal("0.01")
        )

        name = self._find_edit_for_label(
            "Name"
        ).window_text().strip()

        if name != vat_name:
            raise FakturamaError(
                "MANUAL REVIEW REQUIRED: "
                f"VAT Name conflict: expected '{vat_name}', "
                f"observed '{name}'."
            )

        value_control = self._find_edit_for_label(
            "Value"
        )

        observed_value = (
            self._decimal_from_control_text(
                value_control.window_text()
            ).quantize(
                Decimal("0.01")
            )
        )

        if observed_value != expected_percent:
            raise FakturamaError(
                "MANUAL REVIEW REQUIRED: "
                f"VAT Value conflict for '{vat_name}': "
                f"expected {expected_percent}, "
                f"observed {observed_value}."
            )

        code_combo = self._find_combobox_for_label(
            "VAT code (E-Invoice)"
        )

        observed_code = (
            code_combo.window_text().strip()
        )

        expected_code = "S (Standard rate)"

        if observed_code != expected_code:
            raise FakturamaError(
                "MANUAL REVIEW REQUIRED: "
                f"VAT E-Invoice code conflict for '{vat_name}': "
                f"expected '{expected_code}', "
                f"observed '{observed_code}'."
            )

        return {
            "name": name,
            "value": observed_value,
            "code": observed_code,
        }

    def _click_green_plus_near_list(
        self,
        search_control,
    ):
        """
        Find and click the green + action in the active SWT data list.

        SWT does not expose this image button semantically.  We ground it
        visually by detecting a small green connected component in the
        list-toolbar band around the Search control.
        """
        image = self.window.capture_as_image()
        array = np.array(image.convert("RGB"))

        window_rect = self.window.rectangle()
        search_rect = search_control.rectangle()

        local_search_top = (
            search_rect.top
            - window_rect.top
        )

        # Search a toolbar band around Search rather than using an absolute
        # coordinate.  This keeps the interaction layout-relative.
        y0 = max(
            0,
            local_search_top - 55,
        )
        y1 = min(
            array.shape[0],
            local_search_top + 55,
        )

        region = array[
            y0:y1,
            :,
            :,
        ]

        r = region[:, :, 0].astype(
            np.int16
        )
        g = region[:, :, 1].astype(
            np.int16
        )
        b = region[:, :, 2].astype(
            np.int16
        )

        mask = (
            (g > 90)
            & (g - r > 35)
            & (g - b > 20)
        )

        ys, xs = np.where(mask)

        if len(xs) < 12:
            raise FakturamaError(
                "Could not visually locate the green + "
                "control in the VAT list."
            )

        points = set(
            zip(
                xs.tolist(),
                ys.tolist(),
            )
        )

        components = []

        while points:
            seed = points.pop()
            stack = [seed]
            component = [seed]

            while stack:
                x, y = stack.pop()

                for nx in (
                    x - 1,
                    x,
                    x + 1,
                ):
                    for ny in (
                        y - 1,
                        y,
                        y + 1,
                    ):
                        neighbor = (
                            nx,
                            ny,
                        )

                        if neighbor in points:
                            points.remove(
                                neighbor
                            )
                            stack.append(
                                neighbor
                            )
                            component.append(
                                neighbor
                            )

            components.append(
                component
            )

        candidates = []

        for component in components:
            if len(component) < 10:
                continue

            comp_x = [
                point[0]
                for point in component
            ]
            comp_y = [
                point[1]
                for point in component
            ]

            left = min(comp_x)
            right = max(comp_x)
            top = min(comp_y)
            bottom = max(comp_y)

            width = right - left + 1
            height = bottom - top + 1

            if not (
                6 <= width <= 45
                and 6 <= height <= 45
            ):
                continue

            center_x = (
                left + right
            ) / 2

            center_y = (
                top + bottom
            ) / 2

            # Exclude anything inside the Search edit itself.
            screen_x = (
                window_rect.left
                + center_x
            )
            screen_y = (
                window_rect.top
                + y0
                + center_y
            )

            if (
                search_rect.left
                <= screen_x
                <= search_rect.right
                and search_rect.top
                <= screen_y
                <= search_rect.bottom
            ):
                continue

            candidates.append(
                (
                    len(component),
                    screen_x,
                    screen_y,
                )
            )

        if not candidates:
            raise FakturamaError(
                "Green pixels were detected, but no "
                "small green + control could be isolated."
            )

        # The plus icon should be the strongest small green component.
        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        _, x, y = candidates[0]

        mouse.click(
            button="left",
            coords=(
                int(x),
                int(y),
            ),
        )

    def _write_vat_percent(
        self,
        control,
        vat_percent,
    ):
        expected = Decimal(
            str(vat_percent)
        ).quantize(
            Decimal("0.01")
        )

        expected_text = (
            f"{expected:.2f}"
        )

        control.set_focus()

        send_keys(
            "^a",
            pause=0.05,
        )

        send_keys(
            expected_text,
            pause=0.05,
        )

        send_keys(
            "{TAB}",
            pause=0.05,
        )

        time.sleep(0.4)

        observed = (
            self._decimal_from_control_text(
                control.window_text()
            ).quantize(
                Decimal("0.01")
            )
        )

        if observed != expected:
            raise FakturamaError(
                "VAT Value verification failed: "
                f"expected {expected}, observed {observed}."
            )

    def _create_vat(
        self,
        vat_name: str,
        vat_percent,
    ):
        search = self._find_edit_for_label(
            "Search:"
        )

        self._clear_vat_search()

        self._click_green_plus_near_list(
            search
        )

        deadline = time.monotonic() + 6.0

        while time.monotonic() < deadline:
            if self._is_vat_editor_open():
                break

            time.sleep(0.25)
        else:
            raise FakturamaError(
                "Green + was clicked, but the New VAT "
                "editor could not be verified."
            )

        self._write_edit(
            self._find_edit_for_label(
                "Name"
            ),
            vat_name,
        )

        self._write_edit(
            self._find_edit_for_label(
                "Description"
            ),
            vat_name,
        )

        code_combo = self._find_combobox_for_label(
            "VAT code (E-Invoice)"
        )

        self._select_combo_value(
            code_combo,
            "S (Standard rate)",
        )

        self._write_vat_percent(
            self._find_edit_for_label(
                "Value"
            ),
            vat_percent,
        )

        # Deliberately do not interact with Set as standard / Standard VAT.
        save_button = self._find_toolbar_button(
            "Save"
        )

        save_button.click_input()

        time.sleep(0.8)

        verified = self._verify_open_vat(
            vat_name,
            vat_percent,
        )

        return {
            "action": "created",
            **verified,
        }

    def ensure_vat(
        self,
        vat_percent,
    ):
        """
        Ensure the exact VAT master required by a missing Product exists.

        Reuse only when:
          Name  == "VAT X%"
          Value == X
          VAT code (E-Invoice) == "S (Standard rate)"

        Conflicting or ambiguous definitions require manual review.
        """
        vat_name = self._format_vat_name(
            vat_percent
        )

        expected_percent = Decimal(
            str(vat_percent)
        ).quantize(
            Decimal("0.01")
        )

        self._open_vat_list()

        rows = self._vat_search_rows(
            vat_name
        )

        if len(rows) > 1:
            self._clear_vat_search()

            raise FakturamaError(
                "MANUAL REVIEW REQUIRED: "
                f"multiple VAT rows match '{vat_name}'."
            )

        if len(rows) == 1:
            row = rows[0]

            if (
                row["value"] is None
                or row["value"].quantize(
                    Decimal("0.01")
                )
                != expected_percent
            ):
                self._clear_vat_search()

                raise FakturamaError(
                    "MANUAL REVIEW REQUIRED: "
                    f"VAT '{vat_name}' exists but its visible "
                    f"Value conflicts with {expected_percent}%."
                )

            self._open_vat_row(
                row
            )

            verified = self._verify_open_vat(
                vat_name,
                vat_percent,
            )

            return {
                "action": "selected_existing",
                **verified,
            }

        # No exact VAT row: create it.
        return self._create_vat(
            vat_name,
            vat_percent,
        )

    # ============================================================
    # Product resolution / creation
    # ============================================================

    def _find_order_item_action_controls(self):
        """
        Locate the verified Order item actions.

        Fakturama exposes four empty Static icons under "Items".
        Reconnaissance confirmed:
            icon 1 -> Select a product
            icon 2 -> New Product
        """
        label = self._find_visible_label("Items")
        label_rect = label.rectangle()

        candidates = []

        for control in self._visible_controls_by_class("Static"):
            try:
                if control.window_text().strip():
                    continue

                rect = control.rectangle()

                if not (10 <= rect.width() <= 30):
                    continue

                if not (10 <= rect.height() <= 30):
                    continue

                if rect.right < label_rect.right - 8:
                    continue

                if rect.right > label_rect.right + 8:
                    continue

                if rect.top < label_rect.bottom:
                    continue

                if rect.top > label_rect.bottom + 130:
                    continue

                candidates.append(
                    (
                        rect.top,
                        control,
                    )
                )

            except Exception:
                continue

        candidates.sort(
            key=lambda item: item[0]
        )

        if len(candidates) < 2:
            raise FakturamaError(
                "Could not locate the verified Order item actions."
            )

        return [
            candidates[0][1],
            candidates[1][1],
        ]

    def _product_selector_dialog(
        self,
        timeout: float = 8.0,
    ):
        dialog_spec = Desktop(
            backend="win32"
        ).window(
            title_re=r"(?i)^Select a product\s*$"
        )

        try:
            dialog_spec.wait(
                "exists visible",
                timeout=timeout,
            )

            # Resolve the title query once and retain the real HWND wrapper.
            # Returning WindowSpecification makes every later operation search
            # by title again, which can lose an SWT modal between keystrokes.
            dialog = dialog_spec.wrapper_object()

        except Exception as exc:
            raise FakturamaError(
                "The 'Select a product' dialog did not appear."
            ) from exc

        return dialog

    def _visible_product_selector(self):
        """Return the live Product selector, including one behind Fakturama."""
        try:
            dialog_spec = Desktop(
                backend="win32"
            ).window(
                title_re=r"(?i)^Select a product\s*$"
            )

            if not (
                dialog_spec.exists(timeout=0.2)
            ):
                return None

            # Pin this exact top-level HWND.  Do not return the lazy title
            # query, because a later capture_as_image() would re-run the
            # search and can raise ElementNotFoundError even though this same
            # selector was already verified.
            dialog = dialog_spec.wrapper_object()

            if not dialog.is_visible():
                return None

            try:
                if dialog.is_minimized():
                    dialog.restore()
            except Exception:
                pass

            try:
                dialog.set_focus()
            except Exception:
                pass

            return dialog

        except Exception:
            return None

    def open_existing_product_selector(self):
        """
        Open the upper Product selector beside the Order Items table.

        Fakturama's SWT empty-icon wrapper can accept click_input() without
        actually firing the action. Always use a real mouse click on the
        dynamically discovered upper item-action control and retry after
        re-grounding the Order editor.
        """
        already_open = self._visible_product_selector()

        if already_open is not None:
            return already_open

        last_error = None

        for attempt in range(3):
            try:
                self._activate_open_order_editor()

                if not self.is_order_editor_open():
                    raise FakturamaError(
                        "Cannot open product selector: "
                        "no verified Order editor is open."
                    )

                controls = (
                    self._find_order_item_action_controls()
                )

                # controls[0] is the upper existing-product selector.
                selector_control = controls[0]
                rect = selector_control.rectangle()

                x = (
                    rect.left + rect.right
                ) // 2

                y = (
                    rect.top + rect.bottom
                ) // 2

                if attempt == 0:
                    # First use the verified native wrapper.  On systems
                    # where SWT exposes the action correctly this is the
                    # most reliable path and does not depend on DPI scaling.
                    selector_control.click_input()

                elif attempt == 1:
                    # Some SWT wrappers accept click_input() without firing
                    # their listener.  A real mouse click at the wrapper's
                    # live screen rectangle then triggers the same action.
                    mouse.click(
                        button="left",
                        coords=(x, y),
                    )

                else:
                    # Last fallback is grounded from the semantic Items
                    # label itself.  The selector icon is the first action
                    # directly below that label.  This avoids a stale or
                    # incorrectly offset empty Static wrapper.
                    items_rect = self._find_visible_label(
                        "Items"
                    ).rectangle()

                    semantic_x = int(
                        round(items_rect.right - 7)
                    )
                    semantic_y = int(
                        round(items_rect.bottom + 21)
                    )

                    mouse.click(
                        button="left",
                        coords=(semantic_x, semantic_y),
                    )

                deadline = time.monotonic() + 3.5

                while time.monotonic() < deadline:
                    dialog = self._visible_product_selector()

                    if dialog is not None:
                        return dialog

                    time.sleep(0.15)

                raise FakturamaError(
                    "The Product selector did not appear after "
                    f"opening attempt {attempt + 1}."
                )

            except Exception as exc:
                last_error = exc

                # A failed SWT click must not fall through to Product
                # creation. Re-ground and retry the upper selector.
                time.sleep(0.45)

        try:
            self.window.capture_as_image().save(
                "artifacts/screenshots/"
                "product_selector_open_failure.png"
            )
        except Exception:
            pass

        raise FakturamaError(
            "Could not open 'Select a product' after trying the "
            "native Items action, its physical screen position, and "
            "the Items-label-relative selector position."
        ) from last_error

    @staticmethod
    def _product_selector_search_edit(dialog):
        edits = []

        for control in dialog.descendants():
            try:
                if (
                    control.class_name() == "Edit"
                    and control.is_visible()
                ):
                    edits.append(control)
            except Exception:
                continue

        if len(edits) != 1:
            raise FakturamaError(
                "Could not uniquely locate the Product Search field."
            )

        return edits[0]

    def _capture_product_selector_image(self, dialog):
        """
        Capture the SWT Product selector as a real PIL image.

        HwndWrapper.capture_as_image() can return None for this Java/SWT
        modal even while its HWND is valid and visible.  Fall back to a
        direct Windows desktop-region capture using the verified dialog
        rectangle so OCR never receives None.
        """
        image = None

        try:
            image = dialog.capture_as_image()
        except Exception:
            image = None

        if (
            image is not None
            and image.width > 20
            and image.height > 20
        ):
            return image.convert("RGB")

        try:
            rect = dialog.rectangle()
        except Exception:
            rect = None

        if (
            rect is None
            or rect.width() <= 20
            or rect.height() <= 20
        ):
            live_dialog = self._visible_product_selector()

            if live_dialog is None:
                raise FakturamaError(
                    "The Product selector closed before it could be read."
                )

            dialog = live_dialog
            rect = dialog.rectangle()

        if rect.width() <= 20 or rect.height() <= 20:
            raise FakturamaError(
                "The Product selector has an invalid empty screen rectangle."
            )

        bbox = (
            int(rect.left),
            int(rect.top),
            int(rect.right),
            int(rect.bottom),
        )

        try:
            image = ImageGrab.grab(
                bbox=bbox,
                all_screens=True,
            )
        except TypeError:
            image = ImageGrab.grab(
                bbox=bbox,
            )
        except Exception as exc:
            raise FakturamaError(
                "Could not capture the visible Product selector for OCR."
            ) from exc

        if (
            image is None
            or image.width <= 20
            or image.height <= 20
        ):
            raise FakturamaError(
                "Windows returned an empty image for the Product selector."
            )

        return image.convert("RGB")

    @staticmethod
    def _normalize_sku_ocr(value: str) -> str:
        value = re.sub(
            r"[^A-Z0-9_-]",
            "",
            str(value or "").upper(),
        )

        parts = value.split("-")

        if len(parts) >= 2:
            suffix = parts[-1]

            if re.fullmatch(
                r"[0-9OIL]+",
                suffix,
            ):
                parts[-1] = (
                    suffix
                    .replace("O", "0")
                    .replace("I", "1")
                    .replace("L", "1")
                )

                value = "-".join(parts)

        return value

    def _product_selector_matches(
        self,
        dialog,
        sku: str,
    ):
        """
        Resolve an exact SKU directly from the visible Product table.

        The selector screenshot is enlarged 3x before OCR because the SWT
        table text is very small. Returned coordinates are converted back
        to the original dialog coordinate system for clicking.
        """
        # Do not type into SWT Search.  On this Fakturama build, physical
        # typing can dismiss/recreate the modal HWND and leave pywinauto with
        # an empty capture.  The unfiltered Product table already exposes the
        # exact Item No. values, and OCR below validates the required SKU.
        try:
            dialog.set_focus()
        except Exception:
            pass

        time.sleep(0.45)

        image = self._capture_product_selector_image(
            dialog
        )

        debug_path = Path(
            "artifacts/screenshots/"
            "product_selector_latest.png"
        )
        debug_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        try:
            image.save(debug_path)
        except Exception:
            # Diagnostic evidence must never abort Product selection.
            pass

        gray = ImageOps.grayscale(
            image
        )

        gray = ImageOps.autocontrast(
            gray
        )

        gray = ImageEnhance.Contrast(
            gray
        ).enhance(2.0)

        scale = 3

        enlarged = gray.resize(
            (
                gray.width * scale,
                gray.height * scale,
            )
        )

        enlarged_path = Path(
            "artifacts/screenshots/"
            "product_selector_latest_enlarged.png"
        )
        enlarged.save(
            enlarged_path
        )

        data = pytesseract.image_to_data(
            enlarged,
            lang="eng",
            config="--oem 3 --psm 6",
            output_type=Output.DICT,
        )

        words = []

        for index, raw_text in enumerate(
            data["text"]
        ):
            value = raw_text.strip()

            if not value:
                continue

            left = int(
                data["left"][index]
            )
            top = int(
                data["top"][index]
            )
            width = int(
                data["width"][index]
            )
            height = int(
                data["height"][index]
            )

            words.append(
                {
                    "text": value,
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                    "center_x": (
                        left + width / 2
                    ),
                    "center_y": (
                        top + height / 2
                    ),
                    "line_key": (
                        data["block_num"][index],
                        data["par_num"][index],
                        data["line_num"][index],
                    ),
                }
            )

        expected = self._normalize_sku_ocr(
            sku
        )

        expected_compact = re.sub(
            r"[^A-Z0-9]",
            "",
            expected,
        )

        # Find the Name column header. Product SKU cells are to its left.
        name_headers = [
            word
            for word in words
            if (
                word["text"].casefold()
                == "name"
            )
        ]

        if name_headers:
            first_column_right = min(
                word["center_x"]
                for word in name_headers
            )
        else:
            # Safe fallback for the known selector layout: use the left
            # third of the dialog as the Item No. area.
            first_column_right = (
                enlarged.width * 0.36
            )

        lines = {}

        for word in words:
            lines.setdefault(
                word["line_key"],
                [],
            ).append(
                word
            )

        matches = []

        for line_words in lines.values():
            ordered = sorted(
                line_words,
                key=lambda item: item["left"],
            )

            sku_words = [
                word
                for word in ordered
                if (
                    word["center_x"]
                    < first_column_right
                )
            ]

            if not sku_words:
                continue

            # Tesseract normally reads the SKU as one word, but we also
            # try short adjacent combinations in case it split the token.
            for start_index in range(
                len(sku_words)
            ):
                for count in range(
                    1,
                    min(
                        3,
                        len(sku_words)
                        - start_index,
                    )
                    + 1,
                ):
                    candidate_words = (
                        sku_words[
                            start_index:
                            start_index + count
                        ]
                    )

                    candidate_text = "".join(
                        word["text"]
                        for word in candidate_words
                    )

                    observed = (
                        self._normalize_sku_ocr(
                            candidate_text
                        )
                    )

                    observed_compact = re.sub(
                        r"[^A-Z0-9]",
                        "",
                        observed,
                    )

                    if not (
                        observed == expected
                        or (
                            observed_compact
                            == expected_compact
                        )
                    ):
                        continue

                    left = min(
                        word["left"]
                        for word in candidate_words
                    )
                    top = min(
                        word["top"]
                        for word in candidate_words
                    )
                    right = max(
                        word["left"]
                        + word["width"]
                        for word in candidate_words
                    )
                    bottom = max(
                        word["top"]
                        + word["height"]
                        for word in candidate_words
                    )

                    matches.append(
                        {
                            "sku": expected,
                            "left": int(
                                left / scale
                            ),
                            "top": int(
                                top / scale
                            ),
                            "width": max(
                                1,
                                int(
                                    (right - left)
                                    / scale
                                ),
                            ),
                            "height": max(
                                1,
                                int(
                                    (bottom - top)
                                    / scale
                                ),
                            ),
                        }
                    )

                    break

                else:
                    continue

                break

        # Multiple OCR word windows on the same visual row should count
        # as a single product match.
        unique = []

        for match in sorted(
            matches,
            key=lambda item: (
                item["top"],
                item["left"],
            ),
        ):
            duplicate = any(
                abs(
                    match["top"]
                    - existing["top"]
                ) <= 8
                for existing in unique
            )

            if not duplicate:
                unique.append(
                    match
                )

        if not unique:
            raw_debug = (
                pytesseract.image_to_string(
                    enlarged,
                    lang="eng",
                    config="--oem 3 --psm 6",
                )
            )

            log_path = Path(
                "artifacts/logs/"
                "product_selector_ocr_latest.txt"
            )
            log_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            log_path.write_text(
                raw_debug,
                encoding="utf-8",
            )

        return unique

    def _cancel_product_selector(
        self,
        dialog,
    ):
        buttons = []

        for control in dialog.descendants():
            try:
                if (
                    control.class_name() == "Button"
                    and control.window_text().strip() == "Cancel"
                ):
                    buttons.append(control)
            except Exception:
                continue

        if len(buttons) != 1:
            raise FakturamaError(
                "Could not uniquely locate Cancel "
                "in the product selector."
            )

        buttons[0].click_input()
        time.sleep(0.3)

    def _select_product_match(
        self,
        dialog,
        match,
    ):
        """
        Select the exact OCR-grounded SKU row and press OK using physical
        mouse clicks, then verify that the selector actually closes.
        """
        dialog_rect = dialog.rectangle()

        image_x = (
            match["left"]
            + match["width"] / 2
        )
        image_y = (
            match["top"]
            + match["height"] / 2
        )

        target_x = int(
            round(
                dialog_rect.left
                + image_x
            )
        )
        target_y = int(
            round(
                dialog_rect.top
                + image_y
            )
        )

        # A double-click can dismiss this SWT dialog without committing its
        # row.  Select with one click, verify the target row is visibly blue,
        # and only then press OK.  The blue check is restricted to the table
        # area so the selected left-side "all" node cannot pass verification.
        def target_row_is_selected(image):
            pixels = np.asarray(
                image.convert("RGB")
            )

            row_top = max(
                0,
                int(image_y - 10),
            )
            row_bottom = min(
                image.height,
                int(image_y + 11),
            )
            table_left = max(
                0,
                int(match["left"] - 8),
            )
            table_right = max(
                table_left + 1,
                image.width - 8,
            )

            band = pixels[
                row_top:row_bottom,
                table_left:table_right,
            ]

            if band.size == 0:
                return False

            red = band[:, :, 0].astype(np.int16)
            green = band[:, :, 1].astype(np.int16)
            blue = band[:, :, 2].astype(np.int16)

            selected_blue = (
                (blue >= 135)
                & (blue >= red + 35)
                & (blue >= green + 20)
            )

            return float(selected_blue.mean()) >= 0.08

        name_cell_x = int(
            min(
                dialog_rect.right - 20,
                target_x + 145,
            )
        )

        click_attempts = [
            (
                "absolute SKU cell",
                lambda: mouse.click(
                    button="left",
                    coords=(target_x, target_y),
                ),
            ),
            (
                "absolute Name cell",
                lambda: mouse.click(
                    button="left",
                    coords=(name_cell_x, target_y),
                ),
            ),
            (
                "client-relative row",
                lambda: dialog.click_input(
                    coords=(
                        int(image_x + 145),
                        max(1, int(image_y - 30)),
                    )
                ),
            ),
        ]

        selected = False
        after_click_image = None

        try:
            dialog.set_focus()
        except Exception:
            pass

        for _, click_action in click_attempts:
            try:
                click_action()
                time.sleep(0.35)

                after_click_image = (
                    self._capture_product_selector_image(
                        dialog
                    )
                )

                if target_row_is_selected(
                    after_click_image
                ):
                    selected = True
                    break

            except Exception:
                continue

        if after_click_image is not None:
            try:
                after_click_image.save(
                    "artifacts/screenshots/"
                    "product_selector_after_row_click.png"
                )
            except Exception:
                pass

        if not selected:
            raise FakturamaError(
                f"Could not visibly select Product row '{match['sku']}'; "
                "OK was not clicked."
            )

        buttons = []

        for control in dialog.descendants():
            try:
                if (
                    control.class_name() == "Button"
                    and control.is_visible()
                    and control.window_text().strip() == "OK"
                ):
                    buttons.append(control)
            except Exception:
                continue

        if len(buttons) != 1:
            raise FakturamaError(
                "Could not uniquely locate OK "
                "in the product selector."
            )

        # Physically click OK only after the exact row passed the blue-row
        # selection check above.
        try:
            ok_rect = buttons[0].rectangle()
            ok_point = ok_rect.mid_point()

            mouse.click(
                button="left",
                coords=(ok_point.x, ok_point.y),
            )
        except Exception as exc:
            try:
                buttons[0].click_input()
            except Exception as fallback_exc:
                raise FakturamaError(
                    "Could not confirm the selected Product with OK."
                ) from fallback_exc

        deadline = time.monotonic() + 4.0

        while time.monotonic() < deadline:
            try:
                if self._visible_product_selector() is None:
                    time.sleep(0.45)
                    self._activate_open_order_editor()
                    return
            except Exception:
                time.sleep(0.45)
                self._activate_open_order_editor()
                return

            time.sleep(0.15)

        raise FakturamaError(
            "Product selector remained open after OK; "
            "the Product was not committed to the Order."
        )

    def _is_new_product_editor_open(self) -> bool:
        """
        Verify the New Product editor using distinctive semantic labels.
        """
        texts = self.visible_texts()

        distinctive = {
            "Item Number",
            "Price (gross)",
            "cost price (net)",
            "Stock",
        }

        return distinctive.issubset(
            texts
        )

    def open_new_product_from_order(self):
        """
        Open Fakturama's real New Product editor.

        The green plus beside Order Items adds a blank transaction line;
        it is NOT the product-master creation action.  Missing products
        therefore use the semantic top toolbar "Product" action.
        """
        if not self.is_order_editor_open():
            raise FakturamaError(
                "Cannot create product: "
                "no verified Order editor is open."
            )

        product_button = self._find_toolbar_button(
            "Product"
        )

        try:
            product_button.click_input()
        except Exception:
            point = product_button.rectangle().mid_point()
            mouse.click(
                button="left",
                coords=(point.x, point.y),
            )

        deadline = time.monotonic() + 8.0

        while time.monotonic() < deadline:
            if self._is_new_product_editor_open():
                return

            time.sleep(0.2)

        try:
            self.window.capture_as_image().save(
                "artifacts/screenshots/"
                "new_product_action_failure.png"
            )
        except Exception:
            pass

        raise FakturamaError(
            "Toolbar Product action was triggered, "
            "but the New Product editor could not be verified."
        )

    @staticmethod
    def _format_vat_name(vat_percent) -> str:
        value = Decimal(str(vat_percent))

        normalized = format(
            value.normalize(),
            "f",
        )

        return f"VAT {normalized}%"

    @staticmethod
    def _decimal_from_control_text(value: str) -> Decimal:
        cleaned = re.sub(
            r"[^0-9,.\-]",
            "",
            str(value),
        )

        if "," in cleaned and "." not in cleaned:
            cleaned = cleaned.replace(
                ",",
                ".",
            )

        try:
            return Decimal(cleaned)

        except InvalidOperation as exc:
            raise FakturamaError(
                f"Could not parse numeric control value '{value}'."
            ) from exc

    def _write_decimal_edit(
        self,
        control,
        value: Decimal,
    ):
        """
        Write an SWT numeric/currency field and verify it.

        WM_SETTEXT/set_edit_text can visually fail with Fakturama's
        data-bound currency editors.  We try it first, then fall back to
        real keyboard input so SWT receives the same events as a user.
        """
        expected = Decimal(str(value)).quantize(
            Decimal("0.01")
        )

        expected_text = f"{expected:.2f}"

        def observed_value():
            try:
                return self._decimal_from_control_text(
                    control.window_text()
                ).quantize(
                    Decimal("0.01")
                )
            except Exception:
                return None

        # Attempt 1: normal edit API.
        try:
            control.set_focus()
            control.set_edit_text(
                expected_text
            )

            try:
                control.type_keys(
                    "{TAB}",
                    set_foreground=True,
                )
            except Exception:
                pass

            time.sleep(0.35)

            observed = observed_value()

            if observed == expected:
                return observed

        except Exception:
            pass

        # Attempt 2: physical keyboard events.  This is important for
        # SWT currency fields such as "Price (gross)".
        try:
            control.set_focus()
            time.sleep(0.1)

            control.type_keys(
                "^a",
                set_foreground=True,
            )

            control.type_keys(
                "{BACKSPACE}",
                set_foreground=True,
            )

            control.type_keys(
                expected_text,
                with_spaces=True,
                set_foreground=True,
                pause=0.05,
            )

            control.type_keys(
                "{TAB}",
                set_foreground=True,
            )

            time.sleep(0.5)

            observed = observed_value()

            if observed == expected:
                return observed

        except Exception:
            pass

        # Attempt 3: some masked currency fields behave better if the
        # value is entered without the decimal separator (cents).
        try:
            control.set_focus()
            time.sleep(0.1)

            control.type_keys(
                "^a{BACKSPACE}",
                set_foreground=True,
            )

            cents_text = str(
                int(
                    expected
                    * Decimal("100")
                )
            )

            control.type_keys(
                cents_text,
                set_foreground=True,
                pause=0.05,
            )

            control.type_keys(
                "{TAB}",
                set_foreground=True,
            )

            time.sleep(0.5)

            observed = observed_value()

            if observed == expected:
                return observed

        except Exception:
            pass

        observed = observed_value()

        raise FakturamaError(
            "Numeric field verification failed: "
            f"expected {expected:.2f}, "
            f"observed "
            f"{observed if observed is not None else 'unreadable'}."
        )

    def create_product(
        self,
        item,
    ):
        """
        Create one missing product from an extracted OrderItem.

        Product master gross price intentionally ignores transaction
        discount and is calculated from unit net price + VAT.
        """
        self.open_new_product_from_order()

        self._write_edit(
            self._find_edit_for_label(
                "Item Number"
            ),
            item.sku,
        )

        self._write_edit(
            self._find_edit_for_label(
                "Name"
            ),
            item.description,
        )

        self._write_edit(
            self._find_edit_for_label(
                "Description"
            ),
            item.description,
        )

        gross_price = (
            item.product_master_gross_price()
        )

        self._write_decimal_edit(
            self._find_edit_for_label(
                "Price (gross)"
            ),
            gross_price,
        )

        self._write_decimal_edit(
            self._find_edit_for_label(
                "cost price (net)"
            ),
            Decimal("0.00"),
        )

        vat_name = self._format_vat_name(
            item.vat_percent
        )

        vat_combo = self._find_combobox_for_label(
            "VAT"
        )

        try:
            self._select_combo_value(
                vat_combo,
                vat_name,
            )

        except FakturamaError as exc:
            raise FakturamaError(
                f"Required VAT '{vat_name}' does not exist. "
                "Create/resolve the VAT master before creating products."
            ) from exc

        self._write_decimal_edit(
            self._find_edit_for_label(
                "Stock"
            ),
            Decimal("0.00"),
        )

        save_button = self._find_toolbar_button(
            "Save"
        )

        try:
            save_button.click_input()

        except Exception as exc:
            raise FakturamaError(
                f"Could not save new product '{item.sku}'."
            ) from exc

        time.sleep(0.8)

        return {
            "sku": item.sku,
            "gross_price": gross_price,
            "vat_name": vat_name,
        }

    def _count_order_sku_lines(
        self,
        sku: str,
    ) -> int:
        """
        Count occurrences of SKU inside the Order Items grid only.

        The main window can also contain the Products master table, so an
        SKU OCR token counts as an Order line only when the same visual row
        has a numeric Qty to its left and at least two money values to its
        right (U.Price and line Price).
        """
        words, scale, _ = self._order_ocr_words()

        expected = self._compact_ocr_token(
            sku
        )

        window_rect = self.window.rectangle()
        items_rect = self._find_visible_label(
            "Items"
        ).rectangle()
        remarks_rect = self._find_visible_label(
            "Remarks"
        ).rectangle()

        top_bound = (
            items_rect.top
            - window_rect.top
            - 8
        ) * scale
        bottom_bound = (
            remarks_rect.top
            - window_rect.top
            - 3
        ) * scale

        matched_rows = []

        for sku_word in words:
            if not (
                top_bound
                <= sku_word["cy"]
                <= bottom_bound
            ):
                continue

            compact_value = self._compact_ocr_token(
                sku_word["text"]
            )

            # Accept both a standalone SKU and SWT's merged Qty|SKU token.
            if not compact_value.endswith(expected):
                continue

            if not any(
                abs(sku_word["cy"] - row_y) <= 24
                for row_y in matched_rows
            ):
                matched_rows.append(
                    sku_word["cy"]
                )

        return len(matched_rows)

    def resolve_product(
        self,
        item,
    ):
        """
        Ensure exactly one Order line exists for this SKU.

        If the current Order already contains the SKU exactly once (for
        example after a partial failed run), do not add it again.
        Otherwise resolve the product master by exact SKU:
            1 match -> select existing
            0 matches -> create, save once, re-search, select
            >1 matches -> manual review
        """
        existing_order_lines = (
            self._count_order_sku_lines(
                item.sku
            )
        )

        if existing_order_lines == 1:
            return {
                "action": "already_in_order",
                "sku": item.sku,
            }

        if existing_order_lines > 1:
            raise FakturamaError(
                "MANUAL REVIEW REQUIRED: "
                f"Order already contains {existing_order_lines} "
                f"lines for SKU '{item.sku}'."
            )

        dialog = self.open_existing_product_selector()

        matches = self._product_selector_matches(
            dialog,
            item.sku,
        )

        if len(matches) > 1:
            self._cancel_product_selector(
                dialog
            )

            raise FakturamaError(
                "MANUAL REVIEW REQUIRED: "
                f"multiple products match SKU '{item.sku}'."
            )

        if len(matches) == 1:
            self._select_product_match(
                dialog,
                matches[0],
            )

            added_lines = self._count_order_sku_lines(
                item.sku
            )

            if added_lines == 0:
                # Do not claim selected_existing merely because the selector
                # closed.  Retry the same exact SKU once; this handles an SWT
                # row that received focus but did not activate on the first
                # physical input.
                retry_dialog = self.open_existing_product_selector()
                retry_matches = self._product_selector_matches(
                    retry_dialog,
                    item.sku,
                )

                if len(retry_matches) != 1:
                    self._cancel_product_selector(
                        retry_dialog
                    )

                    raise FakturamaError(
                        "Product selection did not add "
                        f"'{item.sku}', and retry did not resolve "
                        "exactly one Product row."
                    )

                self._select_product_match(
                    retry_dialog,
                    retry_matches[0],
                )

                added_lines = self._count_order_sku_lines(
                    item.sku
                )

            if added_lines != 1:
                raise FakturamaError(
                    "The Product selector closed, but the Order grid "
                    f"still does not contain exactly one '{item.sku}' row."
                )

            return {
                "action": "selected_existing",
                "sku": item.sku,
            }

        self._cancel_product_selector(
            dialog
        )

        # Requirement: before creating a missing Product, resolve/create
        # its exact VAT master while keeping the Order open.
        self.ensure_vat(
            item.vat_percent
        )

        self._activate_open_order_editor()

        created = self.create_product(
            item
        )

        self._activate_open_order_editor()

        dialog = self.open_existing_product_selector()

        matches = self._product_selector_matches(
            dialog,
            item.sku,
        )

        if len(matches) != 1:
            self._cancel_product_selector(
                dialog
            )

            raise FakturamaError(
                "Product was created but could not be "
                f"re-resolved to exactly one SKU '{item.sku}'."
            )

        self._select_product_match(
            dialog,
            matches[0],
        )

        return {
            "action": "created_and_selected",
            "sku": item.sku,
            "gross_price": created["gross_price"],
            "vat_name": created["vat_name"],
        }


    # ============================================================
    # Order line completion / verification
    # ============================================================

    @staticmethod
    def _ocr_decimal(value: str) -> Decimal:
        cleaned = re.sub(
            r"[^0-9,.\-]",
            "",
            str(value or ""),
        )

        if not cleaned:
            raise FakturamaError(
                f"Could not parse numeric OCR value {value!r}."
            )

        if "," in cleaned and "." not in cleaned:
            cleaned = cleaned.replace(",", ".")

        try:
            return Decimal(cleaned).quantize(
                Decimal("0.01")
            )
        except Exception as exc:
            raise FakturamaError(
                f"Could not parse numeric OCR value {value!r}."
            ) from exc

    @staticmethod
    def _compact_ocr_token(value: str) -> str:
        return re.sub(
            r"[^A-Z0-9]",
            "",
            str(value or "").upper(),
        )

    def _order_ocr_words(self):
        """
        OCR the active Order window at 3x scale.

        Returns:
            words, scale, raw_text
        """
        if not self.is_order_editor_open():
            raise FakturamaError(
                "Cannot inspect Order lines: "
                "no verified Order editor is open."
            )

        image = self.window.capture_as_image()

        evidence_dir = Path(
            "artifacts/screenshots"
        )
        evidence_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        image.save(
            evidence_dir
            / "order_lines_latest.png"
        )

        gray = ImageOps.autocontrast(
            ImageOps.grayscale(image)
        )

        gray = ImageEnhance.Contrast(
            gray
        ).enhance(2.0)

        scale = 3

        enlarged = gray.resize(
            (
                gray.width * scale,
                gray.height * scale,
            )
        )

        enlarged.save(
            evidence_dir
            / "order_lines_latest_enlarged.png"
        )

        data = pytesseract.image_to_data(
            enlarged,
            lang="eng",
            config="--oem 3 --psm 6",
            output_type=Output.DICT,
        )

        words = []

        for index, raw_text in enumerate(
            data["text"]
        ):
            value = raw_text.strip()

            if not value:
                continue

            left = int(data["left"][index])
            top = int(data["top"][index])
            width = int(data["width"][index])
            height = int(data["height"][index])

            words.append(
                {
                    "text": value,
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                    "cx": left + width / 2,
                    "cy": top + height / 2,
                }
            )

        raw_text = pytesseract.image_to_string(
            enlarged,
            lang="eng",
            config="--oem 3 --psm 6",
        )

        return words, scale, raw_text

    def _find_order_row_cells(
        self,
        sku: str,
        expected_position: int | None = None,
    ):
        """
        Locate one Order transaction row.

        OCR is restricted to the semantic Items -> Remarks vertical region,
        so the same SKU in the Products master table cannot be mistaken for
        an Order line.

        A selected SWT row can render white-on-blue and Tesseract may miss
        its SKU and/or Qty.  In that case we fall back to the source-order
        position (Pos. 1, Pos. 2, ...) while still verifying the row's
        U.Price / Discount / Price and VAT.
        """
        words, scale, _ = self._order_ocr_words()

        window_rect = self.window.rectangle()
        items_rect = self._find_visible_label(
            "Items"
        ).rectangle()
        remarks_rect = self._find_visible_label(
            "Remarks"
        ).rectangle()

        top_bound = (
            items_rect.top
            - window_rect.top
            - 8
        ) * scale

        bottom_bound = (
            remarks_rect.top
            - window_rect.top
            - 3
        ) * scale

        items_words = [
            word
            for word in words
            if (
                top_bound
                <= word["cy"]
                <= bottom_bound
            )
        ]

        expected_sku = self._compact_ocr_token(
            sku
        )

        def decimal_token(word):
            value = word["text"].strip()

            if "%" in value:
                return False

            if "(" in value or ")" in value:
                return False

            return bool(
                re.fullmatch(
                    r"[$€£]?-?\d+(?:[.,]\d+)",
                    value,
                )
            )

        def row_words_at(y):
            row = [
                word
                for word in items_words
                if abs(
                    word["cy"] - y
                ) <= 30
            ]

            row.sort(
                key=lambda word: word["left"]
            )

            return row

        # ------------------------------------------------------------
        # 1. Prefer an exact SKU OCR anchor inside the Items grid.
        # ------------------------------------------------------------
        sku_candidates = []

        for word in items_words:
            raw_value = word["text"].strip()
            compact_value = self._compact_ocr_token(
                raw_value
            )

            if compact_value == expected_sku:
                sku_candidates.append(
                    (word, None)
                )
                continue

            # On selected blue SWT rows Tesseract commonly merges the Qty
            # and Item No. cells into one token, e.g.
            # "1.00|CHR-ERG-01".  Split that live OCR box into a Qty segment
            # and an SKU segment instead of searching for a missing Qty
            # header or guessing the green-plus column.
            if not compact_value.endswith(
                expected_sku
            ):
                continue

            upper_value = raw_value.upper()
            sku_start = upper_value.find(
                sku.upper()
            )

            if sku_start <= 0:
                continue

            qty_match = re.search(
                r"\d+(?:[.,]\d+)?",
                raw_value[:sku_start],
            )

            if qty_match is None:
                continue

            sku_ratio = (
                sku_start
                / max(1, len(raw_value))
            )

            sku_left = (
                word["left"]
                + word["width"] * sku_ratio
            )

            adjusted_sku = {
                **word,
                "text": sku,
                "left": sku_left,
                "width": max(
                    1,
                    word["left"]
                    + word["width"]
                    - sku_left,
                ),
                "cx": (
                    sku_left
                    + word["left"]
                    + word["width"]
                ) / 2,
                "merged_qty_sku": True,
            }

            qty_left = word["left"]
            qty_right = sku_left

            qty_override = {
                "text": qty_match.group(0),
                "left": qty_left,
                "top": word["top"],
                "width": max(
                    1,
                    qty_right - qty_left,
                ),
                "height": word["height"],
                "cx": (
                    qty_left + qty_right
                ) / 2,
                "cy": word["cy"],
                "inferred": False,
                "merged_qty_sku": True,
            }

            sku_candidates.append(
                (
                    adjusted_sku,
                    qty_override,
                )
            )

        selected = None

        for sku_word, merged_qty in sku_candidates:
            row_words = row_words_at(
                sku_word["cy"]
            )

            decimal_right = [
                word
                for word in row_words
                if (
                    word["cx"] > sku_word["cx"]
                    and decimal_token(word)
                )
            ]

            decimal_right.sort(
                key=lambda word: word["cx"]
            )

            if len(decimal_right) >= 3:
                if selected is not None:
                    raise FakturamaError(
                        f"Multiple Order rows matched SKU '{sku}'."
                    )

                selected = {
                    "sku_word": sku_word,
                    "row_words": row_words,
                    "decimal_right": decimal_right,
                    "qty_word_override": merged_qty,
                }

        # ------------------------------------------------------------
        # 2. Selected-row fallback: locate the row by Pos. number.
        # ------------------------------------------------------------
        if (
            selected is None
            and expected_position is not None
        ):
            pos_headers = [
                word
                for word in items_words
                if self._compact_ocr_token(
                    word["text"]
                ) == "POS"
            ]

            position_words = [
                word
                for word in items_words
                if word["text"].strip()
                == str(expected_position)
            ]

            if pos_headers:
                pos_x = min(
                    pos_headers,
                    key=lambda word: word["cy"],
                )["cx"]

                position_words.sort(
                    key=lambda word: abs(
                        word["cx"] - pos_x
                    )
                )
            else:
                # Pos. is the left-most numeric column in the Items grid.
                position_words.sort(
                    key=lambda word: word["cx"]
                )

            for pos_word in position_words:
                row_words = row_words_at(
                    pos_word["cy"]
                )

                # Infer Item No. column X from another readable SKU in
                # the Items grid.  If all SKU text is selected/unreadable,
                # use the "Item No." header location.
                other_sku_words = [
                    word
                    for word in items_words
                    if re.fullmatch(
                        r"[A-Z0-9]+(?:-[A-Z0-9]+)+",
                        word["text"].strip().upper(),
                    )
                ]

                if other_sku_words:
                    item_no_x = min(
                        other_sku_words,
                        key=lambda word: abs(
                            word["cy"] - pos_word["cy"]
                        ),
                    )["cx"]
                else:
                    item_headers = [
                        word
                        for word in items_words
                        if self._compact_ocr_token(
                            word["text"]
                        ) == "ITEM"
                    ]

                    no_headers = [
                        word
                        for word in items_words
                        if self._compact_ocr_token(
                            word["text"]
                        ) == "NO"
                    ]

                    if (
                        not item_headers
                        or not no_headers
                    ):
                        continue

                    item_header = min(
                        item_headers,
                        key=lambda word: word["cy"],
                    )

                    no_header = min(
                        no_headers,
                        key=lambda word: abs(
                            word["cy"]
                            - item_header["cy"]
                        ),
                    )

                    item_no_x = (
                        item_header["cx"]
                        + no_header["cx"]
                    ) / 2

                decimal_right = [
                    word
                    for word in row_words
                    if (
                        word["cx"] > item_no_x
                        and decimal_token(word)
                    )
                ]

                decimal_right.sort(
                    key=lambda word: word["cx"]
                )

                if len(decimal_right) < 3:
                    continue

                synthetic_sku = {
                    "text": sku,
                    "left": item_no_x - 30,
                    "top": pos_word["top"],
                    "width": 60,
                    "height": pos_word["height"],
                    "cx": item_no_x,
                    "cy": pos_word["cy"],
                    "inferred": True,
                }

                selected = {
                    "sku_word": synthetic_sku,
                    "row_words": row_words,
                    "decimal_right": decimal_right,
                }

                break

        # ------------------------------------------------------------
        # 3. Source-order fallback: derive the Nth visible transaction row.
        # ------------------------------------------------------------
        # SWT selected rows can make both the SKU and Pos. text unreadable
        # to Tesseract.  Product selection itself has already been verified
        # exactly before line completion, and the task requires processing
        # source rows in order.  Therefore, when the SKU/Pos anchors are
        # unavailable, identify transaction rows from their numeric cells
        # and use expected_position as the row ordinal.  No screen
        # coordinates are hardcoded: row/column positions come from OCR.
        if (
            selected is None
            and expected_position is not None
        ):
            # Cluster OCR words into horizontal lines inside the Items grid.
            ordered_words = sorted(
                items_words,
                key=lambda word: (
                    word["cy"],
                    word["left"],
                ),
            )

            clusters = []

            for word in ordered_words:
                target_cluster = None

                for cluster in clusters:
                    if abs(
                        word["cy"]
                        - cluster["cy"]
                    ) <= 24:
                        target_cluster = cluster
                        break

                if target_cluster is None:
                    clusters.append(
                        {
                            "cy": word["cy"],
                            "words": [word],
                        }
                    )
                else:
                    target_cluster["words"].append(
                        word
                    )
                    target_cluster["cy"] = sum(
                        item["cy"]
                        for item in target_cluster["words"]
                    ) / len(
                        target_cluster["words"]
                    )

            transaction_rows = []

            for cluster in clusters:
                row = sorted(
                    cluster["words"],
                    key=lambda word: word["left"],
                )

                decimals = [
                    word
                    for word in row
                    if decimal_token(word)
                ]

                decimals.sort(
                    key=lambda word: word["cx"]
                )

                # A transaction row normally exposes Qty, U.Price,
                # Discount and line Price as plain decimal tokens.  On a
                # selected row Qty may disappear, leaving the final three.
                if len(decimals) < 3:
                    continue

                transaction_rows.append(
                    {
                        "cy": cluster["cy"],
                        "row_words": row,
                        "decimals": decimals,
                    }
                )

            transaction_rows.sort(
                key=lambda row: row["cy"]
            )

            row_index = expected_position - 1

            if (
                0 <= row_index
                < len(transaction_rows)
            ):
                chosen = transaction_rows[
                    row_index
                ]

                decimals = chosen["decimals"]

                # Final three plain decimal cells are always
                # U.Price -> Discount -> line Price.
                decimal_right = decimals[-3:]

                # Use a real readable SKU token when possible; otherwise
                # create a synthetic Item-No. anchor from the header.
                readable_skus = [
                    word
                    for word in chosen["row_words"]
                    if re.fullmatch(
                        r"[A-Z0-9]+(?:-[A-Z0-9]+)+",
                        word["text"].strip().upper(),
                    )
                ]

                if readable_skus:
                    synthetic_sku = readable_skus[0]
                else:
                    item_headers = [
                        word
                        for word in items_words
                        if self._compact_ocr_token(
                            word["text"]
                        ) == "ITEM"
                    ]

                    no_headers = [
                        word
                        for word in items_words
                        if self._compact_ocr_token(
                            word["text"]
                        ) == "NO"
                    ]

                    if item_headers and no_headers:
                        item_header = min(
                            item_headers,
                            key=lambda word: word["cy"],
                        )

                        no_header = min(
                            no_headers,
                            key=lambda word: abs(
                                word["cy"]
                                - item_header["cy"]
                            ),
                        )

                        item_no_x = (
                            item_header["cx"]
                            + no_header["cx"]
                        ) / 2
                    else:
                        # Fall back to the left-most readable amount as a
                        # row-local anchor.  Qty will be resolved separately.
                        item_no_x = max(
                            0,
                            decimals[0]["cx"] + 60,
                        )

                    synthetic_sku = {
                        "text": sku,
                        "left": item_no_x - 30,
                        "top": int(
                            chosen["cy"] - 10
                        ),
                        "width": 60,
                        "height": 20,
                        "cx": item_no_x,
                        "cy": chosen["cy"],
                        "inferred": True,
                    }

                # Resolve Qty independently from SKU visibility.  When
                # four plain decimal cells are readable, the left-most of
                # the final four is Qty.  If selected-row rendering hides
                # Qty, infer only its X position from another row or the Qty
                # header and let final verification infer its effective value
                # from line = qty * unit * (1 - discount/100).
                if len(decimals) >= 4:
                    qty_override = decimals[-4]
                else:
                    qty_x_candidates = []

                    for other in transaction_rows:
                        if other is chosen:
                            continue

                        other_decimals = other["decimals"]

                        if len(other_decimals) >= 4:
                            qty_x_candidates.append(
                                other_decimals[-4]["cx"]
                            )

                    if not qty_x_candidates:
                        qty_headers = [
                            word
                            for word in items_words
                            if self._compact_ocr_token(
                                word["text"]
                            ) == "QTY"
                        ]

                        qty_x_candidates.extend(
                            word["cx"]
                            for word in qty_headers
                        )

                    if qty_x_candidates:
                        qty_x_candidates.sort()
                        qty_cx = qty_x_candidates[
                            len(qty_x_candidates) // 2
                        ]

                        qty_override = {
                            "text": None,
                            "left": qty_cx - 20,
                            "top": int(chosen["cy"] - 10),
                            "width": 40,
                            "height": 20,
                            "cx": qty_cx,
                            "cy": chosen["cy"],
                            "inferred": True,
                        }
                    else:
                        qty_override = None

                selected = {
                    "sku_word": synthetic_sku,
                    "row_words": chosen["row_words"],
                    "decimal_right": decimal_right,
                    "qty_word_override": qty_override,
                    "ordinal_fallback": True,
                }

        if selected is None:
            # Include every OCR line in the Items region in the error so a
            # future failure is diagnosable without another special script.
            debug_lines = []

            seen_y = []

            for word in sorted(
                items_words,
                key=lambda item: (
                    item["cy"],
                    item["left"],
                ),
            ):
                if any(
                    abs(word["cy"] - y) <= 24
                    for y in seen_y
                ):
                    continue

                seen_y.append(
                    word["cy"]
                )

                debug_lines.append(
                    " | ".join(
                        item["text"]
                        for item in row_words_at(
                            word["cy"]
                        )
                    )
                )

            debug = " || ".join(
                debug_lines
            )

            raise FakturamaError(
                f"Could not identify Order row for SKU '{sku}'"
                + (
                    f" at Pos. {expected_position}"
                    if expected_position is not None
                    else ""
                )
                + f". Items-grid OCR: {debug}"
            )

        sku_word = selected["sku_word"]
        row_words = selected["row_words"]
        decimal_right = selected[
            "decimal_right"
        ]

        # Last three plain decimal cells are:
        # U.Price -> Discount -> line Price.
        transaction_values = (
            decimal_right[-3:]
        )

        unit_price_word = (
            transaction_values[0]
        )
        discount_word = (
            transaction_values[1]
        )
        line_price_word = (
            transaction_values[2]
        )

        # ------------------------------------------------------------
        # Qty: normally OCR the numeric cell immediately left of Item No.
        # ------------------------------------------------------------
        numeric_left = [
            word
            for word in row_words
            if (
                word["cx"] < sku_word["left"]
                and re.fullmatch(
                    r"\d+(?:[.,]\d+)?",
                    word["text"].strip(),
                )
            )
        ]

        qty_word = selected.get(
            "qty_word_override"
        )

        # Pos. is an integer; Qty normally has decimals. Prefer decimals.
        decimal_qty = [
            word
            for word in numeric_left
            if (
                "." in word["text"]
                or "," in word["text"]
            )
        ]

        if qty_word is None and decimal_qty:
            qty_word = max(
                decimal_qty,
                key=lambda word: word["cx"],
            )

        if qty_word is None:
            # Infer Qty-column X from another readable Order row.
            qty_x_candidates = []

            for other_sku in [
                word
                for word in items_words
                if re.fullmatch(
                    r"[A-Z0-9]+(?:-[A-Z0-9]+)+",
                    word["text"].strip().upper(),
                )
            ]:
                if abs(
                    other_sku["cy"]
                    - sku_word["cy"]
                ) <= 10:
                    continue

                other_row = row_words_at(
                    other_sku["cy"]
                )

                candidates = [
                    word
                    for word in other_row
                    if (
                        word["cx"]
                        < other_sku["left"]
                        and re.fullmatch(
                            r"\d+[.,]\d+",
                            word["text"].strip(),
                        )
                    )
                ]

                if candidates:
                    qty_x_candidates.append(
                        max(
                            candidates,
                            key=lambda word: word["cx"],
                        )["cx"]
                    )

            if not qty_x_candidates:
                qty_headers = [
                    word
                    for word in items_words
                    if self._compact_ocr_token(
                        word["text"]
                    ) == "QTY"
                ]

                qty_x_candidates.extend(
                    word["cx"]
                    for word in qty_headers
                )

            if not qty_x_candidates:
                # Qty is the column immediately between Pos. and Item No.
                # When SWT paints a selected row, Tesseract can miss both
                # the Qty value and the tiny "Qty." header.  The row's Pos.
                # integer and exact SKU are still sufficient to derive the
                # live column centre without any fixed screen coordinate.
                position_candidates = [
                    word
                    for word in numeric_left
                    if re.fullmatch(
                        r"\d+",
                        word["text"].strip(),
                    )
                ]

                if position_candidates:
                    position_word = min(
                        position_candidates,
                        key=lambda word: word["cx"],
                    )

                    left_boundary = position_word["cx"]
                    right_boundary = sku_word["left"]

                    if right_boundary > left_boundary:
                        qty_x_candidates.append(
                            (
                                left_boundary
                                + right_boundary
                            ) / 2
                        )

            if not qty_x_candidates:
                # Last structural fallback: use the painted Pos. and Item No.
                # header locations.  This still adapts to window size and DPI
                # because both boundaries come from the current screenshot.
                pos_headers = [
                    word
                    for word in items_words
                    if self._compact_ocr_token(
                        word["text"]
                    ) == "POS"
                ]

                item_headers = [
                    word
                    for word in items_words
                    if self._compact_ocr_token(
                        word["text"]
                    ) == "ITEM"
                ]

                if pos_headers and item_headers:
                    pos_header = min(
                        pos_headers,
                        key=lambda word: word["cy"],
                    )

                    item_header = min(
                        item_headers,
                        key=lambda word: abs(
                            word["cy"]
                            - pos_header["cy"]
                        ),
                    )

                    if item_header["left"] > pos_header["cx"]:
                        qty_x_candidates.append(
                            (
                                pos_header["cx"]
                                + item_header["left"]
                            ) / 2
                        )

            if not qty_x_candidates:
                raise FakturamaError(
                    f"Order row for '{sku}' was found, "
                    "but the Qty column could not be located."
                )

            qty_x_candidates.sort()

            qty_cx = qty_x_candidates[
                len(qty_x_candidates) // 2
            ]

            qty_word = {
                "text": None,
                "left": qty_cx - 20,
                "top": sku_word["top"],
                "width": 40,
                "height": sku_word["height"],
                "cx": qty_cx,
                "cy": sku_word["cy"],
                "inferred": True,
            }

        row_text = " ".join(
            word["text"]
            for word in row_words
        )

        return {
            "scale": scale,
            "sku_word": sku_word,
            "row_words": row_words,
            "row_text": row_text,
            "qty_word": qty_word,
            "unit_price_word": unit_price_word,
            "discount_word": discount_word,
            "line_price_word": line_price_word,
        }

    def _screen_point_for_ocr_word(
        self,
        word,
        scale: int,
    ):
        window_rect = self.window.rectangle()

        # OCR coordinates belong to the captured image, while pywinauto's
        # rectangle belongs to the Windows desktop coordinate system.  They
        # differ when display scaling is enabled.  Convert through the live
        # capture dimensions before clicking the Qty/U.Price/Discount cell;
        # otherwise the calculated point can land on the green plus beside
        # the Items grid.
        image = self.window.capture_as_image()

        scale_x = (
            window_rect.width()
            / image.width
        )
        scale_y = (
            window_rect.height()
            / image.height
        )

        image_x = word["cx"] / scale
        image_y = word["cy"] / scale

        return (
            int(
                round(
                    window_rect.left
                    + image_x * scale_x
                )
            ),
            int(
                round(
                    window_rect.top
                    + image_y * scale_y
                )
            ),
        )

    def _find_edit_at_screen_point(
        self,
        x: int,
        y: int,
    ):
        candidates = []

        for control in self.window.descendants():
            try:
                if (
                    control.class_name() != "Edit"
                    or not control.is_visible()
                ):
                    continue

                rect = control.rectangle()

                if (
                    rect.left - 8 <= x <= rect.right + 8
                    and rect.top - 8 <= y <= rect.bottom + 8
                ):
                    candidates.append(
                        control
                    )

            except Exception:
                continue

        if len(candidates) != 1:
            raise FakturamaError(
                "Could not uniquely locate the active "
                "Order cell editor after double-click."
            )

        return candidates[0]

    def _set_order_numeric_cell(
        self,
        word,
        scale: int,
        value: Decimal,
    ):
        """
        Edit one numeric Order-grid cell.

        Fakturama creates a short-lived SWT Edit control after double-click.
        pywinauto can discover that control, but the wrapper may already be
        reported invisible by the time type_keys() verifies it.  Use the
        keyboard globally after the double-click instead: the SWT editor
        has focus even when its Win32 wrapper is transient.

        The committed result is verified later by fresh OCR of the Order
        row, so this method does not trust the temporary editor value.
        """
        x, y = self._screen_point_for_ocr_word(
            word,
            scale,
        )

        expected = Decimal(
            str(value)
        ).quantize(
            Decimal("0.01")
        )

        expected_text = f"{expected:.2f}"

        try:
            self.window.set_focus()
        except Exception:
            pass

        mouse.double_click(
            button="left",
            coords=(x, y),
        )

        # Give Fakturama time to create and focus its temporary in-grid
        # SWT editor.  Do not obtain a wrapper for it: that wrapper can
        # become stale/invisible almost immediately.
        time.sleep(0.55)

        send_keys(
            "^a",
            pause=0.05,
        )

        send_keys(
            expected_text,
            pause=0.05,
            with_spaces=True,
        )

        send_keys(
            "{TAB}",
            pause=0.05,
        )

        # Allow recalculation of line price / totals before re-OCR.
        time.sleep(0.8)

        return expected

    def _vat_matches_row(
        self,
        row_text: str,
        vat_percent: Decimal,
    ) -> bool:
        expected = Decimal(
            str(vat_percent)
        ).normalize()

        candidates = re.findall(
            r"(\d+(?:[.,]\d+)?)\s*%",
            row_text,
        )

        for candidate in candidates:
            try:
                value = Decimal(
                    candidate.replace(
                        ",",
                        ".",
                    )
                ).normalize()
            except Exception:
                continue

            if value == expected:
                return True

        return False

    def complete_order_item_line(
        self,
        item,
        expected_position: int | None = None,
    ):
        """
        Set Qty and line Discount from extraction, confirm U.Price and VAT,
        then verify the calculated line Price.
        """
        cells = self._find_order_row_cells(
            item.sku,
            expected_position,
        )

        qty_text = cells["qty_word"].get(
            "text"
        )

        current_qty = (
            self._ocr_decimal(qty_text)
            if qty_text
            else None
        )

        expected_qty = Decimal(
            str(item.quantity)
        ).quantize(
            Decimal("0.01")
        )

        if current_qty != expected_qty:
            self._set_order_numeric_cell(
                cells["qty_word"],
                cells["scale"],
                expected_qty,
            )

        # Refresh OCR after Qty change because line Price recalculates.
        cells = self._find_order_row_cells(
            item.sku,
            expected_position,
        )

        current_unit_price = self._ocr_decimal(
            cells["unit_price_word"]["text"]
        )

        expected_unit_price = Decimal(
            str(item.unit_net_price)
        ).quantize(
            Decimal("0.01")
        )

        if current_unit_price != expected_unit_price:
            self._set_order_numeric_cell(
                cells["unit_price_word"],
                cells["scale"],
                expected_unit_price,
            )

        cells = self._find_order_row_cells(
            item.sku,
            expected_position,
        )

        if not self._vat_matches_row(
            cells["row_text"],
            Decimal(str(item.vat_percent)),
        ):
            raise FakturamaError(
                f"VAT verification failed for '{item.sku}': "
                f"expected {item.vat_percent}%."
            )

        current_discount = abs(
            self._ocr_decimal(
                cells["discount_word"]["text"]
            )
        )

        expected_discount = abs(
            Decimal(
                str(item.discount_percent)
            ).quantize(
                Decimal("0.01")
            )
        )

        if current_discount != expected_discount:
            self._set_order_numeric_cell(
                cells["discount_word"],
                cells["scale"],
                expected_discount,
            )

        # Final row verification.
        cells = self._find_order_row_cells(
            item.sku,
            expected_position,
        )

        final_qty_text = cells["qty_word"].get(
            "text"
        )

        observed_unit = self._ocr_decimal(
            cells["unit_price_word"]["text"]
        )

        observed_discount = abs(
            self._ocr_decimal(
                cells["discount_word"]["text"]
            )
        )

        observed_line = self._ocr_decimal(
            cells["line_price_word"]["text"]
        )

        if final_qty_text:
            observed_qty = self._ocr_decimal(
                final_qty_text
            )
        else:
            # Selected SWT rows occasionally make Qty unreadable to OCR.
            # Verify the effective Qty from the other three displayed
            # transaction values:
            # line = qty * unit * (1 - discount/100).
            multiplier = (
                Decimal("1")
                - (
                    observed_discount
                    / Decimal("100")
                )
            )

            denominator = (
                observed_unit
                * multiplier
            )

            if denominator == 0:
                raise FakturamaError(
                    f"Could not infer Qty for '{item.sku}' "
                    "because the line denominator is zero."
                )

            observed_qty = (
                observed_line
                / denominator
            ).quantize(
                Decimal("0.01")
            )

        expected_line = Decimal(
            str(item.calculated_net())
        ).quantize(
            Decimal("0.01")
        )

        problems = []

        if observed_qty != expected_qty:
            problems.append(
                f"Qty expected {expected_qty}, "
                f"observed {observed_qty}"
            )

        if observed_unit != expected_unit_price:
            problems.append(
                f"U.Price expected {expected_unit_price}, "
                f"observed {observed_unit}"
            )

        if observed_discount != expected_discount:
            problems.append(
                f"Discount expected {expected_discount}, "
                f"observed {observed_discount}"
            )

        if not self._vat_matches_row(
            cells["row_text"],
            Decimal(str(item.vat_percent)),
        ):
            problems.append(
                f"VAT expected {item.vat_percent}%"
            )

        if observed_line != expected_line:
            problems.append(
                f"Line Price expected {expected_line}, "
                f"observed {observed_line}"
            )

        if problems:
            raise FakturamaError(
                f"Order line verification failed for "
                f"'{item.sku}': "
                + "; ".join(problems)
            )

        return {
            "sku": item.sku,
            "quantity": observed_qty,
            "unit_net_price": observed_unit,
            "vat_percent": Decimal(
                str(item.vat_percent)
            ),
            "discount_percent": observed_discount,
            "line_net": observed_line,
        }

    def complete_order_lines(
        self,
        order,
    ):
        results = []

        for position, item in enumerate(
            order.items,
            start=1,
        ):
            results.append(
                self.complete_order_item_line(
                    item,
                    expected_position=position,
                )
            )

        return results

    # ============================================================
    # Order save + persisted Documents verification
    # ============================================================

    def _click_toolbar_button_physically(
        self,
        button_name: str,
    ):
        """
        Click a visible top-toolbar action using OCR grounding.

        Fakturama's Win32 wrapper for the toolbar can expose geometry that
        does not correspond to the painted SWT toolbar. During live testing,
        the semantically resolved Save control pointed to the menu-bar area
        and did not persist the document. OCR grounding of the visible
        toolbar label reproduced the successful manual Save.

        This remains layout-independent: the target is discovered from the
        currently rendered toolbar text and constrained to the top portion
        of the Fakturama window.
        """
        self.require_connection()

        image = self.window.capture_as_image()

        # Do not define the toolbar as a fixed percentage of the whole
        # window.  On a short/restored Fakturama window the painted toolbar
        # can be below 16% of the image height even though it is fully
        # visible.  Use a generous top band, then choose the highest matching
        # label; toolbar captions are always above document/editor tabs.
        top_limit = min(
            320,
            max(
                180,
                int(image.height * 0.40),
            ),
        )

        def normalized(value: str) -> str:
            return re.sub(
                r"[^a-z0-9]",
                "",
                value.casefold(),
            ).replace("0", "o")

        def edit_distance(left: str, right: str) -> int:
            previous = list(
                range(len(right) + 1)
            )

            for row, left_char in enumerate(
                left,
                start=1,
            ):
                current = [row]

                for column, right_char in enumerate(
                    right,
                    start=1,
                ):
                    current.append(
                        min(
                            current[-1] + 1,
                            previous[column] + 1,
                            previous[column - 1]
                            + (left_char != right_char),
                        )
                    )

                previous = current

            return previous[-1]

        target_name = normalized(button_name)
        matches = []

        # SWT toolbar text can be segmented differently depending on display
        # scaling.  Try both a normal block pass and sparse-text pass, then
        # repeat against a high-contrast 2x image.
        enhanced = ImageOps.autocontrast(
            ImageOps.grayscale(image)
        ).resize(
            (
                image.width * 2,
                image.height * 2,
            )
        )

        ocr_passes = (
            (image, 1, 6),
            (image, 1, 11),
            (enhanced, 2, 6),
            (enhanced, 2, 11),
        )

        for ocr_image, scale, page_mode in ocr_passes:
            try:
                data = pytesseract.image_to_data(
                    ocr_image,
                    lang="eng",
                    config=(
                        f"--oem 3 --psm {page_mode}"
                    ),
                    output_type=Output.DICT,
                )
            except Exception:
                continue

            for index, raw_text in enumerate(data["text"]):
                value = normalized(raw_text.strip())

                if not value:
                    continue

                distance = edit_distance(
                    value,
                    target_name,
                )

                # Permit one OCR-character error (for example 0rder), but
                # only for similarly sized words inside the top band.
                if (
                    distance > 1
                    or abs(len(value) - len(target_name)) > 1
                ):
                    continue

                left = int(data["left"][index]) / scale
                top = int(data["top"][index]) / scale
                width = int(data["width"][index]) / scale
                height = int(data["height"][index]) / scale

                if top > top_limit:
                    continue

                matches.append(
                    {
                        "left": left,
                        "top": top,
                        "width": width,
                        "height": height,
                        "distance": distance,
                    }
                )

        if not matches:
            screenshot = (
                "artifacts/screenshots/"
                f"toolbar_{button_name.casefold()}_failure.png"
            )

            try:
                Path(screenshot).parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                image.save(screenshot)
            except Exception:
                pass

            raise FakturamaError(
                f"Could not OCR-locate the visible top-toolbar "
                f"'{button_name}' label. Diagnostic screenshot: "
                f"{screenshot}"
            )

        target = min(
            matches,
            key=lambda match: (
                match["distance"],
                match["top"],
            ),
        )
        window_rect = self.window.rectangle()

        # capture_as_image() pixels and Win32 window coordinates are not
        # guaranteed to use the same DPI scale.  Map the OCR point from image
        # space into the actual window rectangle before sending the click.
        scale_x = window_rect.width() / image.width
        scale_y = window_rect.height() / image.height

        image_x = (
            target["left"]
            + target["width"] / 2
        )
        image_y = (
            target["top"]
            + target["height"] / 2
        )

        x = int(
            round(
                window_rect.left
                + image_x * scale_x
            )
        )
        y = int(
            round(
                window_rect.top
                + image_y * scale_y
            )
        )

        try:
            mouse.click(
                button="left",
                coords=(x, y),
            )
            return

        except Exception as physical_exc:
            # Some Windows/DPI combinations reject SetCursorPos even for a
            # visible target.  Fall back to a message-based click on the
            # smallest visible child control containing the OCR point; this
            # does not move the system cursor.
            containing_controls = []

            for control in self.visible_controls():
                try:
                    rect = control.rectangle()

                    if not (
                        rect.left <= x <= rect.right
                        and rect.top <= y <= rect.bottom
                    ):
                        continue

                    area = max(
                        1,
                        rect.width() * rect.height(),
                    )

                    containing_controls.append(
                        (area, control, rect)
                    )

                except Exception:
                    continue

            containing_controls.sort(
                key=lambda item: item[0]
            )

            for _, control, rect in containing_controls:
                try:
                    control.click(
                        button="left",
                        coords=(
                            x - rect.left,
                            y - rect.top,
                        ),
                    )
                    return
                except Exception:
                    continue

            raise FakturamaError(
                f"Located top-toolbar '{button_name}', but Windows "
                f"rejected the click at mapped point ({x}, {y})."
            ) from physical_exc


    def _open_documents_list(self):
        """
        Open Data > Documents without fixed screen coordinates.

        Strategy:
        1) Prefer OCR grounding of the visible 'Documents' label.
        2) If OCR misses the label, use the SWT navigation hierarchy:
           find the left navigation container and click its top-most
           contained navigation entry. In Fakturama, Documents is the
           first entry in the Data navigation group.

        The fallback is structural/layout-relative, not a fixed x/y click.
        """
        image = self.window.capture_as_image()
        window_rect = self.window.rectangle()

        # --------------------------------------------------------
        # 1. OCR-grounded navigation
        # --------------------------------------------------------
        try:
            data = pytesseract.image_to_data(
                image,
                lang="eng",
                config="--oem 3 --psm 6",
                output_type=Output.DICT,
            )

            hits = []

            for i, raw in enumerate(data["text"]):
                value = raw.strip()

                if value.casefold() != "documents":
                    continue

                hits.append(
                    {
                        "left": int(data["left"][i]),
                        "top": int(data["top"][i]),
                        "width": int(data["width"][i]),
                        "height": int(data["height"][i]),
                    }
                )

            if hits:
                target = min(
                    hits,
                    key=lambda hit: hit["left"],
                )

                screen_x = (
                    window_rect.left
                    + target["left"]
                    + target["width"] // 2
                )
                screen_y = (
                    window_rect.top
                    + target["top"]
                    + target["height"] // 2
                )

                candidates = []

                for control in self.window.descendants():
                    try:
                        if not control.is_visible():
                            continue

                        if control.class_name() != "SWT_Window0":
                            continue

                        rect = control.rectangle()

                        if not (
                            rect.left <= screen_x <= rect.right
                            and rect.top <= screen_y <= rect.bottom
                        ):
                            continue

                        area = max(
                            1,
                            (rect.right - rect.left)
                            * (rect.bottom - rect.top),
                        )

                        candidates.append(
                            (area, control)
                        )

                    except Exception:
                        continue

                if candidates:
                    candidates.sort(
                        key=lambda item: item[0]
                    )

                    candidates[0][1].click_input()
                    time.sleep(0.8)
                    return

        except Exception:
            pass

        # --------------------------------------------------------
        # 2. Structural SWT fallback
        # --------------------------------------------------------
        swt_controls = []

        for control in self.window.descendants():
            try:
                if not control.is_visible():
                    continue

                if control.class_name() != "SWT_Window0":
                    continue

                rect = control.rectangle()

                width = rect.right - rect.left
                height = rect.bottom - rect.top

                if width <= 0 or height <= 0:
                    continue

                swt_controls.append(
                    (control, rect)
                )

            except Exception:
                continue

        # Candidate navigation containers live in the left portion of the
        # window, are relatively tall, and contain several smaller SWT
        # navigation entries.
        container_candidates = []

        window_width = max(
            1,
            window_rect.right - window_rect.left,
        )

        for parent, parent_rect in swt_controls:
            parent_width = (
                parent_rect.right - parent_rect.left
            )
            parent_height = (
                parent_rect.bottom - parent_rect.top
            )

            if parent_height < 180:
                continue

            if parent_width > window_width * 0.30:
                continue

            if parent_rect.left > (
                window_rect.left + window_width * 0.30
            ):
                continue

            children = []

            for child, child_rect in swt_controls:
                if child is parent:
                    continue

                if not (
                    parent_rect.left <= child_rect.left
                    and child_rect.right <= parent_rect.right
                    and parent_rect.top <= child_rect.top
                    and child_rect.bottom <= parent_rect.bottom
                ):
                    continue

                child_height = (
                    child_rect.bottom - child_rect.top
                )

                # Navigation entries are short horizontal rows.
                if not (18 <= child_height <= 45):
                    continue

                children.append(
                    (child, child_rect)
                )

            if len(children) >= 3:
                container_candidates.append(
                    (
                        len(children),
                        parent_height,
                        children,
                    )
                )

        if not container_candidates:
            raise FakturamaError(
                "Could not locate the Data navigation structure "
                "needed to open Documents."
            )

        # Prefer the container with the most navigation rows, then the
        # taller one. Documents is the first/top-most row in this group.
        container_candidates.sort(
            key=lambda item: (
                item[0],
                item[1],
            ),
            reverse=True,
        )

        children = container_candidates[0][2]

        children.sort(
            key=lambda item: (
                item[1].top,
                item[1].left,
            )
        )

        documents_control = children[0][0]

        documents_control.click_input()
        time.sleep(0.8)


    @staticmethod
    def _compact_ocr(value: str) -> str:
        return re.sub(
            r"[^A-Z0-9]",
            "",
            str(value).upper(),
        )

    def _document_number_present(
        self,
        raw: str,
        document_number: str,
    ) -> bool:
        compact_raw = self._compact_ocr(
            raw
        )

        expected = self._compact_ocr(
            document_number
        )

        if expected in compact_raw:
            return True

        # Tesseract commonly confuses the letter O with zero in PO/INV
        # document numbers.
        alternate = expected.replace(
            "O",
            "0",
        )

        if alternate in compact_raw:
            return True

        # In the saved Documents row, Tesseract can drop the leading P and
        # read the O in PO as another zero.  For example:
        #
        #     PO000008 -> 0000008
        #
        # Accept that exact OCR token only.  The caller still requires the
        # same filtered row to match Date, Cust.Ref., state and Total, so this
        # does not weaken persisted-document identity verification.
        prefix_match = re.match(
            r"^[A-Z]+",
            expected,
        )
        prefix = (
            prefix_match.group(0)
            if prefix_match
            else ""
        )
        digits = re.sub(
            r"\D",
            "",
            expected,
        )

        tolerated_tokens = {
            expected,
            alternate,
        }

        if digits:
            tolerated_tokens.add(digits)

            if "O" in prefix:
                tolerated_tokens.add(
                    "0" + digits
                )

        observed_tokens = {
            self._compact_ocr(token)
            for token in re.findall(
                r"[A-Z0-9]+",
                str(raw).upper(),
            )
        }

        return bool(
            tolerated_tokens
            & observed_tokens
        )

    def _filtered_documents_ocr(
        self,
        document_number: str,
    ):
        """
        Search the currently active Documents list and OCR only its lower
        list region, anchored to that list's Search control.
        """
        search = self._find_edit_for_label(
            "Search:"
        )

        search.set_focus()
        search.set_edit_text(
            document_number
        )

        time.sleep(0.8)

        image = self.window.capture_as_image()

        window_rect = self.window.rectangle()
        search_rect = search.rectangle()

        crop_top = max(
            0,
            search_rect.bottom
            - window_rect.top
            + 8,
        )

        lower = image.crop(
            (
                0,
                crop_top,
                image.width,
                image.height,
            )
        )

        raw = pytesseract.image_to_string(
            lower,
            lang="eng",
            config="--oem 3 --psm 6",
        )

        return search, image, raw

    def verify_saved_order_in_documents(
        self,
        order,
        order_number: str,
    ):
        """
        Verify the saved source Order in Data > Documents.
        """
        self._open_documents_list()

        search, image, raw = (
            self._filtered_documents_ocr(
                order_number
            )
        )

        compact = self._compact_ocr(
            raw
        )

        expected_date = self._compact_ocr(
            order.order_date.strftime(
                "%b %d, %Y"
            )
        )

        expected_ref = self._compact_ocr(
            order.external_reference
        )

        expected_total = self._compact_ocr(
            f"{Decimal(str(order.source_gross_total)):.2f}"
        )

        checks = {
            "Order number": self._document_number_present(
                raw,
                order_number,
            ),
            "Date": expected_date in compact,
            "Cust.Ref.": expected_ref in compact,
            "Open state": "OPEN" in compact,
            "Total": expected_total in compact,
        }

        Path(
            "artifacts/screenshots"
        ).mkdir(
            parents=True,
            exist_ok=True,
        )

        image.save(
            "artifacts/screenshots/"
            "final_order_documents_verified.png"
        )

        try:
            search.set_focus()
            search.set_edit_text("")
        except Exception:
            pass

        failed = [
            label
            for label, passed in checks.items()
            if not passed
        ]

        if failed:
            raise FakturamaError(
                "Order was not fully verified in Data > Documents. "
                "Missing checks: "
                + ", ".join(failed)
                + ". OCR was: "
                + repr(raw)
            )

        return {
            "order_number": order_number,
            "date": order.order_date,
            "cust_ref": order.external_reference,
            "state": "open",
            "total": Decimal(
                str(order.source_gross_total)
            ).quantize(
                Decimal("0.01")
            ),
        }

    def save_and_verify_order(
        self,
        order,
        order_number: str,
    ):
        """
        Save the current Order exactly once, then prove that it persisted.

        The save is considered successful only if the exact generated Order
        can subsequently be found in Data > Documents with the expected
        Date, Cust.Ref., open state, and Total.
        """
        self._activate_open_order_editor()

        observed_number = (
            self.find_generated_order_number()
        )

        if observed_number != order_number:
            raise FakturamaError(
                "Refusing to save: active Order number changed. "
                f"Expected {order_number}, observed {observed_number}."
            )

        observed_ref = self._find_edit_for_label(
            "Cust.Ref."
        ).window_text().strip()

        if observed_ref != order.external_reference:
            raise FakturamaError(
                "Refusing to save: Cust.Ref. does not match source. "
                f"Expected '{order.external_reference}', "
                f"observed '{observed_ref}'."
            )

        # Exactly one toolbar Save action.
        self._click_toolbar_button_physically(
            "Save"
        )

        time.sleep(2.0)

        # The editor must remain the same generated Order.
        if not self.is_order_editor_open():
            raise FakturamaError(
                "Order editor disappeared after Save."
            )

        after_save_number = (
            self.find_generated_order_number()
        )

        if after_save_number != order_number:
            raise FakturamaError(
                "Order number changed after Save. "
                f"Expected {order_number}, "
                f"observed {after_save_number}."
            )

        verified = (
            self.verify_saved_order_in_documents(
                order,
                order_number,
            )
        )

        # Requirement 4.6 starts from the still-open saved Order.
        self._activate_open_order_editor()

        return verified


    def _find_numeric_edit_for_label(
        self,
        label_text: str,
        *,
        prefer_bottom: bool = False,
    ):
        """
        Resolve a displayed numeric value by its visible label.

        Fakturama exposes the totals as real SWT Static/Edit controls, so
        use those semantic controls instead of OCRing the totals area.
        """
        labels = []

        for control in self.window.descendants():
            try:
                if not control.is_visible():
                    continue

                if control.class_name() != "Static":
                    continue

                if control.window_text().strip() != label_text:
                    continue

                labels.append(control)

            except Exception:
                continue

        if not labels:
            raise FakturamaError(
                f"Could not find label '{label_text}'."
            )

        labels.sort(
            key=lambda control: control.rectangle().top,
            reverse=prefer_bottom,
        )

        for label in labels:
            label_rect = label.rectangle()
            label_center_y = (
                label_rect.top + label_rect.bottom
            ) / 2

            candidates = []

            for control in self.window.descendants():
                try:
                    if not control.is_visible():
                        continue

                    if control.class_name() != "Edit":
                        continue

                    rect = control.rectangle()

                    if rect.left < label_rect.right:
                        continue

                    center_y = (
                        rect.top + rect.bottom
                    ) / 2

                    vertical_distance = abs(
                        center_y - label_center_y
                    )

                    if vertical_distance > 18:
                        continue

                    horizontal_distance = (
                        rect.left - label_rect.right
                    )

                    candidates.append(
                        (
                            vertical_distance,
                            horizontal_distance,
                            control,
                        )
                    )

                except Exception:
                    continue

            if candidates:
                candidates.sort(
                    key=lambda item: (
                        item[0],
                        item[1],
                    )
                )

                return candidates[0][2]

        raise FakturamaError(
            f"Could not find numeric value beside '{label_text}'."
        )

    def verify_order_totals(
        self,
        order,
    ):
        """
        Verify Order Total Net, VAT and Total from Fakturama's exposed
        SWT Edit controls. OCR is deliberately not used here because
        selected rows can make the totals-area OCR unstable.
        """
        total_net_control = (
            self._find_numeric_edit_for_label(
                "Total Net"
            )
        )

        # There are two visible VAT labels in an Order: the VAT-mode label
        # near the header and the monetary VAT total near the bottom.
        # Select the lower one.
        vat_control = (
            self._find_numeric_edit_for_label(
                "VAT",
                prefer_bottom=True,
            )
        )

        total_control = (
            self._find_numeric_edit_for_label(
                "Total"
            )
        )

        observed_net = (
            self._decimal_from_control_text(
                total_net_control.window_text()
            ).quantize(
                Decimal("0.01")
            )
        )

        observed_vat = (
            self._decimal_from_control_text(
                vat_control.window_text()
            ).quantize(
                Decimal("0.01")
            )
        )

        observed_total = (
            self._decimal_from_control_text(
                total_control.window_text()
            ).quantize(
                Decimal("0.01")
            )
        )

        expected_net = Decimal(
            str(order.source_net_total)
        ).quantize(
            Decimal("0.01")
        )

        expected_vat = Decimal(
            str(order.source_vat_total)
        ).quantize(
            Decimal("0.01")
        )

        expected_total = Decimal(
            str(order.source_gross_total)
        ).quantize(
            Decimal("0.01")
        )

        if (
            observed_net != expected_net
            or observed_vat != expected_vat
            or observed_total != expected_total
        ):
            raise FakturamaError(
                "Order total verification failed: "
                f"Net {observed_net}/{expected_net}, "
                f"VAT {observed_vat}/{expected_vat}, "
                f"Total {observed_total}/{expected_total}."
            )

        return {
            "net": observed_net,
            "vat": observed_vat,
            "total": observed_total,
        }

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
