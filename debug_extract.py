from src.extraction import extract_order


order = extract_order(
    "samples/order_input.png"
)

print("\nEXTRACTION SUCCESS\n")

print(
    order.model_dump_json(
        indent=2
    )
)

print(
    "\nFinancial problems:",
    order.validate_financials(),
)