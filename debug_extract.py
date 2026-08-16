from src.extraction import extract_order


order = extract_order(
    "samples/high_res_test_order.png"
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