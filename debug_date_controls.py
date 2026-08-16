from src.fakturama import FakturamaAutomation


fakturama = FakturamaAutomation().connect()

date_label = fakturama._find_visible_label("Date")
date_field = fakturama._find_edit_for_label("Date")

label_rect = date_label.rectangle()
field_rect = date_field.rectangle()

print("DATE LABEL")
print(
    "text =", repr(date_label.window_text()),
    "class =", date_label.class_name(),
    "rect =", label_rect,
)

print("\nDATE FIELD")
print(
    "text =", repr(date_field.window_text()),
    "class =", date_field.class_name(),
    "rect =", field_rect,
)

print("\nCONTROLS AROUND DATE FIELD")

for control in fakturama.visible_controls():
    try:
        rect = control.rectangle()

        # Look only near the visible Date area.
        nearby_x = (
            rect.left <= field_rect.right + 150
            and rect.right >= field_rect.left - 100
        )

        nearby_y = (
            rect.top <= field_rect.bottom + 80
            and rect.bottom >= field_rect.top - 80
        )

        if not (nearby_x and nearby_y):
            continue

        print(
            "class =",
            repr(control.class_name()),
            "| text =",
            repr(control.window_text()),
            "| rect =",
            rect,
        )

    except Exception:
        pass