import time

import win32gui
import win32process
from pywinauto import mouse

from src.fakturama import FakturamaAutomation


fakturama = FakturamaAutomation().connect()

main_handle = fakturama.window.handle

_, fakturama_pid = win32process.GetWindowThreadProcessId(
    main_handle
)

print("FAKTURAMA PID:", fakturama_pid)


def fakturama_top_windows():
    results = []

    def callback(hwnd, _):
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)

            if pid != fakturama_pid:
                return

            rect = win32gui.GetWindowRect(hwnd)
            class_name = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)
            visible = win32gui.IsWindowVisible(hwnd)

            results.append(
                {
                    "handle": hwnd,
                    "class": class_name,
                    "title": title,
                    "rect": rect,
                    "visible": visible,
                }
            )

        except Exception:
            pass

    win32gui.EnumWindows(callback, None)

    return results


field = fakturama._find_edit_for_label("Date")
field_rect = field.rectangle()

print("\nDATE FIELD")
print(
    "text =",
    repr(field.window_text()),
    "| rect =",
    field_rect,
)


# Find the smallest SWT container containing the Date Edit.
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


containers.sort(key=lambda item: item[0])

container_rect = containers[0][1]

print("\nDATE CONTAINER")
print(container_rect)


before = fakturama_top_windows()

print("\nFAKTURAMA WINDOWS BEFORE:")

for window in before:
    print(window)


before_handles = {
    window["handle"]
    for window in before
}


# Click the dynamically discovered calendar area.
click_x = int(
    (field_rect.right + container_rect.right) / 2
)

click_y = int(
    (container_rect.top + container_rect.bottom) / 2
)

print(
    "\nOPENING CALENDAR:",
    click_x,
    click_y,
)

mouse.click(
    button="left",
    coords=(click_x, click_y),
)

time.sleep(1)


after = fakturama_top_windows()

print("\nFAKTURAMA WINDOWS AFTER:")

for window in after:
    marker = ""

    if window["handle"] not in before_handles:
        marker = "  <--- NEW"

    print(window, marker)