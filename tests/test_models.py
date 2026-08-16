from datetime import date
from decimal import Decimal

from src.models import (
    AddressData,
    CustomerData,
    OrderData,
    OrderItem,
    PaymentData,
)


def test_reference_order_financials():
    invoice_address = AddressData(
        company="Northstar Office GmbH",
        street="Friedrichstrasse 88",
        zip_code="10117",
        city="Berlin",
        country="Germany",
    )

    delivery_address = AddressData(
        company="Northstar Office Warehouse",
        street="Beusselstrasse 44",
        zip_code="10553",
        city="Berlin",
        country="Germany",
    )

    customer = CustomerData(
        source_customer_id="CUST-1007",
        company="Northstar Office GmbH",
        first_name="Marta",
        last_name="Klein",
        alias="NORTHSTAR-BERLIN",
        email="marta.klein@example.test",
        phone="+49 30 5550 1420",
        invoice_address=invoice_address,
        delivery_address=delivery_address,
    )

    payment = PaymentData(
        method="Bank Transfer",
        status="PAID",
        payment_date=date(2026, 7, 18),
    )

    items = [
        OrderItem(
            sku="CHR-ERG-01",
            description="Ergonomic Desk Chair",
            quantity=Decimal("2"),
            unit_net_price=Decimal("250.00"),
            vat_percent=Decimal("19"),
            discount_percent=Decimal("10"),
        ),
        OrderItem(
            sku="MAT-DESK-02",
            description="Anti-Fatigue Desk Mat",
            quantity=Decimal("3"),
            unit_net_price=Decimal("40.00"),
            vat_percent=Decimal("19"),
            discount_percent=Decimal("0"),
        ),
    ]

    order = OrderData(
        order_date=date(2026, 7, 14),
        external_reference="WEB-2026-0714-A17",
        currency="EUR",
        customer=customer,
        payment=payment,
        items=items,
        source_net_total=Decimal("570.00"),
        source_vat_total=Decimal("108.30"),
        source_gross_total=Decimal("678.30"),
    )

    assert items[0].calculated_net() == Decimal("450.00")
    assert items[1].calculated_net() == Decimal("120.00")

    assert items[0].product_master_gross_price() == Decimal("297.50")
    assert items[1].product_master_gross_price() == Decimal("47.60")

    assert order.calculated_net_total() == Decimal("570.00")
    assert order.calculated_vat_total() == Decimal("108.30")
    assert order.calculated_gross_total() == Decimal("678.30")

    assert order.validate_financials() == []