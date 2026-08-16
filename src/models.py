from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from pydantic import BaseModel, Field, model_validator


MONEY = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    """Round monetary values to two decimal places."""
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


class AddressData(BaseModel):
    company: Optional[str] = None
    street: str
    zip_code: str
    city: str
    country: str


class CustomerData(BaseModel):
    # ID from the source document.
    # This must NOT overwrite Fakturama's generated Customer ID.
    source_customer_id: Optional[str] = None

    company: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    alias: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    invoice_address: AddressData
    delivery_address: AddressData


class PaymentData(BaseModel):
    method: str
    status: str
    payment_date: Optional[date] = None

    @model_validator(mode="after")
    def validate_paid_date(self):
        if self.status.upper() == "PAID" and self.payment_date is None:
            raise ValueError("A PAID order must include a payment date.")
        return self


class OrderItem(BaseModel):
    sku: str
    description: str

    quantity: Decimal = Field(gt=0)
    unit_net_price: Decimal = Field(ge=0)
    vat_percent: Decimal = Field(ge=0)
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)

    def calculated_net(self) -> Decimal:
        discount_multiplier = Decimal("1") - (
            self.discount_percent / Decimal("100")
        )

        return money(
            self.quantity
            * self.unit_net_price
            * discount_multiplier
        )

    def calculated_vat(self) -> Decimal:
        return money(
            self.calculated_net()
            * self.vat_percent
            / Decimal("100")
        )

    def calculated_gross(self) -> Decimal:
        return money(
            self.calculated_net()
            + self.calculated_vat()
        )

    def product_master_gross_price(self) -> Decimal:
        """
        Gross Product master price.

        Transaction-level discounts are deliberately excluded.
        """
        return money(
            self.unit_net_price
            * (
                Decimal("1")
                + self.vat_percent / Decimal("100")
            )
        )


class OrderData(BaseModel):
    order_date: date
    external_reference: str
    currency: str

    customer: CustomerData
    payment: PaymentData
    items: list[OrderItem]

    source_net_total: Decimal
    source_vat_total: Decimal
    source_gross_total: Decimal

    @model_validator(mode="after")
    def require_items(self):
        if not self.items:
            raise ValueError("An order must contain at least one item.")
        return self

    def calculated_net_total(self) -> Decimal:
        return money(
            sum(
                (item.calculated_net() for item in self.items),
                Decimal("0")
            )
        )

    def calculated_vat_total(self) -> Decimal:
        return money(
            sum(
                (item.calculated_vat() for item in self.items),
                Decimal("0")
            )
        )

    def calculated_gross_total(self) -> Decimal:
        return money(
            self.calculated_net_total()
            + self.calculated_vat_total()
        )

    def validate_financials(self) -> list[str]:
        """
        Compare values calculated from the line items with
        totals stated in the source document.

        Returns a list of problems.
        An empty list means the order is financially consistent.
        """
        problems = []

        calculated_net = self.calculated_net_total()
        calculated_vat = self.calculated_vat_total()
        calculated_gross = self.calculated_gross_total()

        if calculated_net != money(self.source_net_total):
            problems.append(
                f"Net mismatch: calculated {calculated_net}, "
                f"source {money(self.source_net_total)}"
            )

        if calculated_vat != money(self.source_vat_total):
            problems.append(
                f"VAT mismatch: calculated {calculated_vat}, "
                f"source {money(self.source_vat_total)}"
            )

        if calculated_gross != money(self.source_gross_total):
            problems.append(
                f"Gross mismatch: calculated {calculated_gross}, "
                f"source {money(self.source_gross_total)}"
            )

        return problems