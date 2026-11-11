"""Payment gateways. A paid plan starts only after the server has verified a payment; nothing a
browser reports is trusted on its own.

Only a mock gateway exists so far. It moves no money, and it is refused outside demo mode, so a
misconfigured deployment cannot give paid plans away.
"""

import hashlib
import hmac
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from ..config import settings


class PaymentError(Exception):
    """The payment could not be verified. Nothing has been activated."""


@dataclass(frozen=True)
class VerifiedPayment:
    payment_id: str
    status: str  # paid | failed
    amount_minor: int
    currency: str


class PaymentGateway(Protocol):
    name: str
    test_mode: bool

    def create_order(self, amount_minor: int, currency: str) -> str: ...

    def verify(self, order_id: str, payload: dict[str, str]) -> VerifiedPayment: ...


class MockGateway:
    """Stands in for a hosted checkout, signing with a key the browser never sees."""

    name = "mock"
    test_mode = True

    @staticmethod
    def _signature(order_id: str, payment_id: str, status: str, amount_minor: int, currency: str) -> str:
        key = hashlib.sha256(f"mock-gateway:{settings.jwt_secret}".encode()).digest()
        return hmac.new(key, f"{order_id}|{payment_id}|{status}|{amount_minor}|{currency}".encode(), hashlib.sha256).hexdigest()

    def create_order(self, amount_minor: int, currency: str) -> str:
        return f"mock_order_{uuid4().hex}"

    def simulate(self, order_id: str, outcome: str, amount_minor: int, currency: str) -> dict[str, str]:
        """What a real gateway would return to the browser after its own checkout page."""
        payment_id = f"mock_pay_{uuid4().hex}"
        return {
            "payment_id": payment_id,
            "status": outcome,
            "amount_minor": str(amount_minor),
            "currency": currency,
            "signature": self._signature(order_id, payment_id, outcome, amount_minor, currency),
        }

    def verify(self, order_id: str, payload: dict[str, str]) -> VerifiedPayment:
        try:
            payment_id, status, currency = payload["payment_id"], payload["status"], payload["currency"]
            amount_minor = int(payload["amount_minor"])
            supplied = payload["signature"]
        except (KeyError, TypeError, ValueError) as exc:
            raise PaymentError("The payment response is incomplete.") from exc
        if status not in {"paid", "failed"}:
            raise PaymentError("The payment response has an unknown status.")
        if not hmac.compare_digest(supplied, self._signature(order_id, payment_id, status, amount_minor, currency)):
            raise PaymentError("The payment signature is not valid.")
        return VerifiedPayment(payment_id, status, amount_minor, currency)


def get_gateway() -> PaymentGateway | None:
    """The configured gateway, or None when payments are not available."""
    name = settings.payment_gateway.strip().lower()
    if name == "mock" and settings.demo_mode:
        return MockGateway()
    # Razorpay and Stripe have no adapter yet, so naming one must not appear to work.
    return None
