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

from PIL import ImageEnhance, ImageOps
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

        Previous versions returned the currently active Order when one was
        already open.  That made reruns append products to an old unsaved
        Order.  We now click the Order toolbar every time and verify that
        the Order-number Edit control itself has changed, which proves a
        different editor tab became active even if Fakturama temporarily
        reuses the same unsaved document number.
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
                        # Verify that the new editor has not inherited
                        # the previous transaction's customer reference.
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

    def _find_exact_debtor_candidates(
        self,
        dialog,
        customer,
    ):
        """
        Find safe debtor candidates from the unfiltered table.

        First Name, Name, ZIP and City must match exactly.

        If Company is fully visible, it must also match exactly.
        If Fakturama truncates Company with "...", only a prefix match is
        accepted at this stage and the full company is verified immediately
        after selection, before the Order can be saved.
        """
        # Fakturama can retain a previous Search value across dialog
        # openings. Always clear it before reading the full table.
        self._clear_selector_search(
            dialog
        )

        rows = self._ocr_selector_rows(
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

                row["company_needs_post_verify"] = True

            else:
                if normalized_company != expected_company:
                    continue

                row["company_needs_post_verify"] = False

            candidates.append(row)

        return candidates

    def _select_selector_row(
        self,
        dialog,
        row,
    ):
        """
        Click one OCR-detected row and confirm it with the native OK button.
        """
        dialog_rect = dialog.rectangle()

        mouse.click(
            button="left",
            coords=(
                dialog_rect.left
                + int(row["id_center_x"]),
                dialog_rect.top
                + int(row["row_center_y"]),
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
                "Could not uniquely locate OK "
                "in the address selector."
            )

        buttons[0].click_input()
        time.sleep(0.6)

    def _verify_selected_order_company(
        self,
        expected_company: str,
    ):
        """
        Verify the full company after debtor selection.

        Fakturama truncates the Company column in its SWT selector table.
        The selected Order address, however, contains the full company.
        Native control text is checked first; OCR of the semantic Addresses
        region is used as a fallback.
        """
        expected_normalized = (
            self._normalize_match_text(
                expected_company
            )
        )

        # Prefer native control text when available.
        for control in self.visible_controls():
            try:
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

        # OCR only the Order's Addresses region.
        addresses_label = self._find_visible_label(
            "Addresses"
        )

        items_label = self._find_visible_label(
            "Items"
        )

        window_rect = self.window.rectangle()
        addresses_rect = addresses_label.rectangle()
        items_rect = items_label.rectangle()

        top = max(
            0,
            addresses_rect.top
            - window_rect.top
        )

        bottom = max(
            top + 20,
            items_rect.top
            - window_rect.top
        )

        image = self.window.capture_as_image()

        region = image.crop(
            (
                0,
                top,
                image.width,
                min(
                    image.height,
                    bottom,
                ),
            )
        )

        text = pytesseract.image_to_string(
            region,
            lang="eng",
            config="--oem 3 --psm 6",
        )

        observed = self._normalize_match_text(
            text
        )

        if expected_normalized not in observed:
            raise FakturamaError(
                "MANUAL REVIEW REQUIRED: "
                "debtor row matched First Name, Name, ZIP and City, "
                "but the full selected Company could not be verified "
                f"as '{expected_company}'."
            )

        return True

    def _is_new_debtor_editor_open(self) -> bool:
        texts = self.visible_texts()

        return (
            "Customer ID" in texts
            and "Company" in texts
            and "First Name Last Name" in texts
            and "Addresses" in texts
        )

    def open_new_debtor_from_order(self):
        if not self.is_order_editor_open():
            raise FakturamaError(
                "Cannot create debtor: "
                "no verified Order editor is open."
            )

        controls = self._find_order_address_action_controls()
        create_icon = controls[1]

        # SWT image controls may accept click_input() without actually
        # firing the mouse listener. Use a real screen click at the
        # verified icon center instead.
        point = create_icon.rectangle().mid_point()

        try:
            self.window.set_focus()
        except Exception:
            pass

        mouse.click(
            button="left",
            coords=(point.x, point.y),
        )

        deadline = time.monotonic() + 8.0

        while time.monotonic() < deadline:
            if self._is_new_debtor_editor_open():
                return

            time.sleep(0.2)

        raise FakturamaError(
            "New Debtor icon was clicked, "
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

        # No safe candidate -> create.
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
        dialog = Desktop(
            backend="win32"
        ).window(
            title="Select a product"
        )

        try:
            dialog.wait(
                "exists visible",
                timeout=timeout,
            )

        except Exception as exc:
            raise FakturamaError(
                "The 'Select a product' dialog did not appear."
            ) from exc

        return dialog

    def open_existing_product_selector(self):
        if not self.is_order_editor_open():
            raise FakturamaError(
                "Cannot open product selector: "
                "no verified Order editor is open."
            )

        controls = self._find_order_item_action_controls()

        try:
            controls[0].click_input()

        except Exception:
            point = controls[0].rectangle().mid_point()

            mouse.click(
                button="left",
                coords=(point.x, point.y),
            )

        return self._product_selector_dialog()

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
        Resolve an exact SKU from the UNFILTERED product selector.

        Fakturama's Search field is not reliable for these SKU values, so
        the automation deliberately clears Search and performs its own OCR
        over the visible product table.

        The selector screenshot is enlarged 3x before OCR because the SWT
        table text is very small. Returned coordinates are converted back
        to the original dialog coordinate system for clicking.
        """
        search = self._product_selector_search_edit(
            dialog
        )

        # Do not depend on Fakturama's built-in Search filtering.
        search.set_focus()

        try:
            search.set_edit_text("")
        except Exception:
            search.type_keys(
                "^a{BACKSPACE}",
                set_foreground=True,
            )

        time.sleep(0.7)

        image = dialog.capture_as_image()

        debug_path = Path(
            "artifacts/screenshots/"
            "product_selector_latest.png"
        )
        debug_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        image.save(debug_path)

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
        dialog_rect = dialog.rectangle()

        mouse.click(
            button="left",
            coords=(
                dialog_rect.left
                + match["left"]
                + match["width"] // 2,
                dialog_rect.top
                + match["top"]
                + match["height"] // 2,
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
                "Could not uniquely locate OK "
                "in the product selector."
            )

        buttons[0].click_input()
        time.sleep(0.6)

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
        words, _, _ = self._order_ocr_words()

        expected = self._compact_ocr_token(
            sku
        )

        count = 0

        for sku_word in words:
            if (
                self._compact_ocr_token(
                    sku_word["text"]
                )
                != expected
            ):
                continue

            row_words = [
                word
                for word in words
                if abs(
                    word["cy"]
                    - sku_word["cy"]
                ) <= 30
            ]

            numeric_left = [
                word
                for word in row_words
                if (
                    word["cx"]
                    < sku_word["left"]
                    and re.fullmatch(
                        r"\d+(?:[.,]\d+)?",
                        word["text"].strip(),
                    )
                )
            ]

            money_right = [
                word
                for word in row_words
                if (
                    word["cx"] > sku_word["cx"]
                    and re.fullmatch(
                        r"[$€£]\d+[.,]\d{2}",
                        word["text"].strip(),
                    )
                )
            ]

            if (
                numeric_left
                and len(money_right) >= 2
            ):
                count += 1

        return count

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
        sku_candidates = [
            word
            for word in items_words
            if (
                self._compact_ocr_token(
                    word["text"]
                )
                == expected_sku
            )
        ]

        selected = None

        for sku_word in sku_candidates:
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

        if selected is None:
            debug = " || ".join(
                " | ".join(
                    word["text"]
                    for word in row_words_at(
                        candidate["cy"]
                    )
                )
                for candidate in sku_candidates
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

        qty_word = None

        # Pos. is an integer; Qty normally has decimals. Prefer decimals.
        decimal_qty = [
            word
            for word in numeric_left
            if (
                "." in word["text"]
                or "," in word["text"]
            )
        ]

        if decimal_qty:
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

        return (
            window_rect.left
            + int(word["cx"] / scale),
            window_rect.top
            + int(word["cy"] / scale),
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

    def verify_order_totals(
        self,
        order,
    ):
        """
        Verify Order Total Net, VAT and Total using OCR of the totals area.
        """
        _, _, raw_text = self._order_ocr_words()

        normalized = re.sub(
            r"[ \t]+",
            " ",
            raw_text,
        )

        total_net_match = re.search(
            r"Total Net\s*[$€£]?\s*"
            r"([0-9]+[.,][0-9]{2})",
            normalized,
            re.IGNORECASE,
        )

        if not total_net_match:
            raise FakturamaError(
                "Could not read Order Total Net."
            )

        after_net = normalized[
            total_net_match.end():
        ]

        vat_match = re.search(
            r"(?:^|\n)\s*VAT\s*[$€£]?\s*"
            r"([0-9]+[.,][0-9]{2})",
            after_net,
            re.IGNORECASE,
        )

        total_match = re.search(
            r"(?:^|\n)\s*Total\s*[$€£]?\s*"
            r"([0-9]+[.,][0-9]{2})",
            after_net,
            re.IGNORECASE,
        )

        if not vat_match or not total_match:
            raise FakturamaError(
                "Could not read Order VAT/Total values."
            )

        observed_net = self._ocr_decimal(
            total_net_match.group(1)
        )

        observed_vat = self._ocr_decimal(
            vat_match.group(1)
        )

        observed_total = self._ocr_decimal(
            total_match.group(1)
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