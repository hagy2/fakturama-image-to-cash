import time

import win32gui
import win32process
from pywinauto import Desktop, mouse
from pywinauto.keyboard import send_keys

from src.fakturama import FakturamaAutomation


fakturama = FakturamaAutomation().connect()

main_handle = fakturama.window.handle

_, fakturama_pid = win32process.GetWindowThreadProcessId(
    main_handle
)


def fakturama_windows():
    results = []

    def callback(hwnd, _):
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)

            if pid != fakturama_pid:
                return

            results.append(
                {
                    "handle": hwnd,
                    "class": win32gui.GetClassName(hwnd),
                    "title": win32gui.GetWindowText(hwnd),
                    "rect": win32gui.GetWindowRect(hwnd),
                    "visible": bool(
                        win32gui.IsWindowVisible(hwnd)
                    ),
                }
            )

        except Exception:
            pass

    win32gui.EnumWindows(callback, None)

    return results


# Close an already-open calendar popup if one exists.
try:
    fakturama.window.set_focus()
    send_keys("{ESC}")
    time.sleep(0.3)
except Exception:
    pass


# Locate Date field dynamically.
field = fakturama._find_edit_for_label("Date")
field_rect = field.rectangle()

print("DATE FIELD:")
print(
    "text =", repr(field.window_text()),
    "| rect =", field_rect,
)


# Find smallest SWT container containing the Date Edit.
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
            containers.append(
                (area, rect)
            )

    except Exception:
        continue


if not containers:
    raise RuntimeError(
        "Could not locate Date SWT container."
    )


containers.sort(
    key=lambda item: item[0]
)

container_rect = containers[0][1]

print("\nDATE CONTAINER:")
print(container_rect)


# Click calendar area dynamically.
click_x = int(
    (field_rect.right + container_rect.right) / 2
)

click_y = int(
    (container_rect.top + container_rect.bottom) / 2
)

print(
    "\nOPENING CALENDAR AT:",
    click_x,
    click_y,
)

mouse.click(
    button="left",
    coords=(click_x, click_y),
)

time.sleep(1)


# Find visible calendar-like Fakturama dialog.
windows = fakturama_windows()

popup_candidates = []

for window in windows:
    if not window["visible"]:
        continue

    if window["class"] != "#32770":
        continue

    left, top, right, bottom = window["rect"]

    width = right - left
    height = bottom - top

    # Popup should be a relatively small window.
    if width <= 0 or height <= 0:
        continue

    if width > 600 or height > 700:
        continue

    # Prefer a popup positioned beside/below the Date widget.
    horizontal_overlap = (
        right >= container_rect.left
        and left <= container_rect.right
    )

    near_date = (
        top >= container_rect.bottom - 20
        and top <= container_rect.bottom + 150
    )

    score = 0

    if horizontal_overlap:
        score += 10

    if near_date:
        score += 10

    popup_candidates.append(
        (score, window)
    )


if not popup_candidates:
    print("\nNO #32770 POPUP FOUND.")
    print("\nVISIBLE FAKTURAMA WINDOWS:")

    for window in windows:
        if window["visible"]:
            print(window)

    raise RuntimeError(
        "Calendar is visible, but its popup window "
        "could not be identified."
    )


popup_candidates.sort(
    key=lambda item: item[0],
    reverse=True,
)

popup_info = popup_candidates[0][1]

print("\nCALENDAR POPUP FOUND:")
print(popup_info)

popup_handle = popup_info["handle"]


print("\nWIN32 DESCENDANTS:")

popup = Desktop(
    backend="win32"
).window(handle=popup_handle)

try:
    descendants = popup.descendants()
except Exception as exc:
    descendants = []
    print(
        "Could not inspect Win32 descendants:",
        exc,
    )


if not descendants:
    print("No Win32 descendants exposed.")


for child in descendants:
    try:
        print(
            "class =",
            repr(child.class_name()),
            "| text =",
            repr(child.window_text()),
            "| rect =",
            child.rectangle(),
        )
    except Exception:
        pass


print("\nUIA DESCENDANTS:")

try:
    popup_uia = Desktop(
        backend="uia"
    ).window(handle=popup_handle)

    uia_descendants = popup_uia.descendants()

    if not uia_descendants:
        print("No UIA descendants exposed.")

    for child in uia_descendants:
        try:
            info = child.element_info

            print(
                "type =",
                repr(info.control_type),
                "| name =",
                repr(info.name),
                "| rect =",
                info.rectangle,
            )

        except Exception:
            pass

except Exception as exc:
    print(
        "Could not inspect popup using UIA:",
        exc,
    )