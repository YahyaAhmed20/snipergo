
import logging
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils.crypto import get_random_string

from accounts.models import WalletTopUpRequest

logger = logging.getLogger(__name__)

PAYMOB_INTENTION_URL = "https://accept.paymob.com/v1/intention/"


def _amount_to_cents(amount):
    amount = Decimal(str(amount))

    if not amount.is_finite() or amount < Decimal("200.00"):
        raise ValidationError("Minimum Paymob top-up is 200.00 EGP.")

    rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(rounded * 100)


def _required_setting(name):
    value = getattr(settings, name, "")
    if not value:
        raise ImproperlyConfigured(
            f"Missing required setting: {name}"
        )
    return value


@transaction.atomic
def create_paymob_topup(*, captain, amount):
    """
    Create a pending Paymob wallet top-up and request a payment intention.
    Does not credit the wallet.
    """

    secret_key = _required_setting("PAYMOB_SECRET_KEY")
    public_key = _required_setting("PAYMOB_PUBLIC_KEY")
    integration_id = _required_setting("PAYMOB_INTEGRATION_ID")
    currency = getattr(settings, "PAYMOB_CURRENCY", "EGP")

    if currency != "EGP":
        raise ValidationError("Only EGP top-ups are currently supported.")

    amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    amount_cents = _amount_to_cents(amount)

    user = captain.user
    phone = captain.phone_number.strip()

    if not user.first_name.strip() or not user.last_name.strip():
        raise ValidationError(
            "Add your first and last name before paying."
        )

    if not user.email.strip() or not phone:
        raise ValidationError(
            "Add your email and phone number before paying."
        )

    reference = f"PAYMOB-{get_random_string(24).upper()}"

    topup = WalletTopUpRequest.objects.create(
        captain=captain,
        amount=amount,
        method=WalletTopUpRequest.Method.PAYMOB,
        status=WalletTopUpRequest.Status.PENDING,
        reference=reference,
    )

    payload = {
        "amount": amount_cents,
        "currency": currency,
        "payment_methods": [int(integration_id)],
        "items": [
            {
                "name": "SNIPER GO Captain Wallet Top-up",
                "amount": amount_cents,
                "description": f"Wallet top-up {reference}",
                "quantity": 1,
            }
        ],
        "billing_data": {
            "first_name": user.first_name.strip(),
            "last_name": user.last_name.strip(),
            "email": user.email.strip(),
            "phone_number": phone,
            "country": "EG",
            "city": "Suez",
            "street": "NA",
            "building": "NA",
            "floor": "NA",
            "apartment": "NA",
            "postal_code": "NA",
        },
        "special_reference": reference,
        "extras": {
            "wallet_topup_reference": reference,
            "captain_id": captain.pk,
        },
        "notification_url": getattr(
            settings, "PAYMOB_NOTIFICATION_URL", ""
        ),
        "redirection_url": getattr(
            settings, "PAYMOB_REDIRECTION_URL", ""
        ),
    }

    if not payload["notification_url"] or not payload["redirection_url"]:
        raise ImproperlyConfigured(
            "Configure PAYMOB_NOTIFICATION_URL and "
            "PAYMOB_REDIRECTION_URL before creating payments."
        )

    try:
        response = requests.post(
            PAYMOB_INTENTION_URL,
            headers={"Authorization": f"Token {secret_key}"},
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError):
        logger.exception(
            "Paymob intention creation failed for top-up %s",
            topup.reference,
        )
        raise ValidationError(
            "Unable to start payment right now. Please try again."
        )

    order_id = result.get("intention_order_id")
    client_secret = result.get("client_secret")

    if not order_id or not client_secret:
        logger.error(
            "Paymob intention response missing required fields for %s",
            topup.reference,
        )
        raise ValidationError(
            "Payment provider returned an incomplete response."
        )

    topup.payment_order_id = str(order_id)
    topup.payment_reference = str(result.get("id", ""))
    topup.save(
        update_fields=[
            "payment_order_id",
            "payment_reference",
            "updated_at",
        ]
    )

    checkout_url = (
        "https://accept.paymob.com/unifiedcheckout/"
        f"?publicKey={public_key}&clientSecret={client_secret}"
    )

    return topup, checkout_url





import hashlib
import hmac


HMAC_CALLBACK_FIELDS = (
    "amount_cents",
    "created_at",
    "currency",
    "error_occured",
    "has_parent_transaction",
    "obj.id",
    "integration_id",
    "is_3d_secure",
    "is_auth",
    "is_capture",
    "is_refunded",
    "is_standalone_payment",
    "is_voided",
    "order.id",
    "owner",
    "pending",
    "source_data.pan",
    "source_data.sub_type",
    "source_data.type",
    "success",
)




def _nested_value(data, dotted_key):
    # All transaction fields in a processed callback are inside "obj".
    value = data.get("obj") if isinstance(data, dict) else None

    if not isinstance(value, dict):
        raise ValueError("Missing callback obj")

    # The HMAC field "obj.id" means the "id" inside obj.
    parts = dotted_key.split(".")
    if parts[0] == "obj":
        parts = parts[1:]

    for part in parts:
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"Missing HMAC field: {dotted_key}")
        value = value[part]

    if value is None:
        raise ValueError(f"Null HMAC field: {dotted_key}")

    if isinstance(value, bool):
        return "true" if value else "false"

    return str(value)




def verify_processed_callback_hmac(payload, received_hmac, secret):
    if not isinstance(payload, dict) or not received_hmac or not secret:
        return False

    obj = payload.get("obj")
    if not isinstance(obj, dict):
        return False

    normalized = {
        **payload,
        "obj": obj,
    }

    try:
        message = "".join(
            _nested_value(normalized, key)
            for key in HMAC_CALLBACK_FIELDS
        )
    except ValueError:
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()

    return hmac.compare_digest(expected, str(received_hmac))
