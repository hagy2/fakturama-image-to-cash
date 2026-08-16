import time
from datetime import date

from pywinauto import mouse
from pywinauto.keyboard import send_keys

from src.fakturama import FakturamaAutomation


TARGET_DATE = date(2026, 7, 14)

fakturama = FakturamaAutomation().connect()

field = fakturama._find_edit_for_label("Date")
field_rect = field.rectangle()

print("BEFORE:", field.window_text())


# Find smallest SWT container containing the Date field.
containers = []

for control in fakturama.visible_controls():
    try:
        if control.class_name() != "SWT_Window0":
            continue

        rect = control.rectangle()

        contains = (
            rect.left <= field_rect.left
            and rect.top <= field_rect.top
            and rect.right >= field_rect.right
            and rect.bottom >= field_rect.bottom
        )

        extends_right = rect.right > field_rect.right

        if contains and extends_right:
            area = rect.width() * rect.height()
            containers.append((area, rect))

    except Exception:
        continue


if not containers:
    raise RuntimeError("Could not find Date container.")


containers.sort(key=lambda item: item[0])
container_rect = containers[0][1]


# Open calendar dynamically.
click_x = int(
    (field_rect.right + container_rect.right) / 2
)

click_y = int(
    (container_rect.top + container_rect.bottom) / 2
)

mouse.click(
    button="left",
    coords=(click_x, click_y),
)

time.sleep(0.5)


# Starting from Aug 16, 2026:
#
# PageUp  -> Jul 16, 2026
# Left x2 -> Jul 14, 2026
# Enter   -> commit
#
# This is a diagnostic to confirm Fakturama's calendar
# supports standard keyboard navigation.
send_keys("{PGUP}")
time.sleep(0.2)

send_keys("{LEFT 2}")
time.sleep(0.2)

send_keys("{ENTER}")
time.sleep(0.7)


observed = field.window_text().strip()

print("AFTER:", observed)

expected = "Jul 14, 2026"

if observed == expected:
    print("PASS: calendar keyboard navigation works.")
else:
    print(
        "FAIL:",
        f"expected {expected!r}, observed {observed!r}"
    )