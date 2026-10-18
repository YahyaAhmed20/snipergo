import json
import logging
from decimal import Decimal, InvalidOperation
from django.http import HttpResponse, JsonResponse
from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.models import WalletTopUpRequest
from accounts.services.paymob import verify_processed_callback_hmac
from trips.services.wallet import credit_wallet

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def paymob_transaction_webhook(request):
    secret = getattr(settings, "PAYMOB_HMAC_SECRET", "")

    if not secret:
        logger.error("Paymob HMAC secret is not configured.")
        return JsonResponse(
            {"detail": "Payment callback is not configured."},
            status=503,
        )

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"detail": "Invalid JSON payload."},
            status=400,
        )

    if not isinstance(payload, dict):
        return JsonResponse(
            {"detail": "Invalid callback payload."},
            status=400,
        )

    received_hmac = request.GET.get("hmac", "")

    if not verify_processed_callback_hmac(
        payload,
        received_hmac,
        secret,
    ):
        logger.warning("Rejected Paymob callback with invalid HMAC.")
        return JsonResponse(
            {"detail": "Invalid callback signature."},
            status=403,
        )

    if payload.get("type") != "TRANSACTION":
        return JsonResponse(
            {"detail": "Unsupported callback type."},
            status=400,
        )

    obj = payload.get("obj")
    if not isinstance(obj, dict):
        return JsonResponse(
            {"detail": "Missing transaction object."},
            status=400,
        )

    # Only successful, completed, non-refunded payments can credit a wallet.
    if (
        obj.get("success") is not True
        or obj.get("pending") is not False
        or obj.get("error_occured") is not False
        or obj.get("is_refunded") is not False
        or obj.get("is_voided") is not False
    ):
        return JsonResponse(
            {"detail": "Payment is not eligible for wallet credit."},
            status=200,
        )

    if obj.get("currency") != "EGP":
        return JsonResponse(
            {"detail": "Unexpected payment currency."},
            status=400,
        )

    configured_integration = str(
        getattr(settings, "PAYMOB_INTEGRATION_ID", "")
    )
    if (
        not configured_integration
        or str(obj.get("integration_id"))
        != configured_integration
    ):
        return JsonResponse(
            {"detail": "Unexpected payment integration."},
            status=400,
        )

    order = obj.get("order")
    if not isinstance(order, dict) or order.get("id") is None:
        return JsonResponse(
            {"detail": "Missing Paymob order ID."},
            status=400,
        )

    if obj.get("id") is None:
        return JsonResponse(
            {"detail": "Missing transaction ID."},
            status=400,
        )

    order_id = str(order["id"])
    transaction_id = str(obj["id"])

    # ─────────────────────────────────────────────────────────────
    # Strict amount validation
    # ─────────────────────────────────────────────────────────────
    raw_amount_cents = obj.get("amount_cents")

    # JSON booleans are Python integers subclasses, so reject them explicitly.
    if (
        type(raw_amount_cents) is not int
        or raw_amount_cents <= 0
    ):
        return JsonResponse(
            {"detail": "Invalid payment amount."},
            status=400,
        )

    amount_cents = raw_amount_cents
    # ─────────────────────────────────────────────────────────────

    with transaction.atomic():
        topups = WalletTopUpRequest.objects.select_for_update().filter(
            method=WalletTopUpRequest.Method.PAYMOB,
            payment_order_id=order_id,
        )

        if topups.count() != 1:
            logger.warning(
                "Paymob callback did not match exactly one top-up."
            )
            return JsonResponse(
                {"detail": "Top-up order not found or ambiguous."},
                status=404,
            )

        topup = topups.get()

        expected_cents = int(
            Decimal(str(topup.amount)) * 100
        )

        if amount_cents != expected_cents:
            logger.error(
                "Paymob amount mismatch for top-up %s.",
                topup.reference,
            )
            return JsonResponse(
                {"detail": "Payment amount mismatch."},
                status=400,
            )

        if topup.status == WalletTopUpRequest.Status.COMPLETED:
            if topup.payment_transaction_id == transaction_id:
                return JsonResponse(
                    {"detail": "Payment already processed."},
                    status=200,
                )

            return JsonResponse(
                {"detail": "Top-up already completed."},
                status=409,
            )

        if topup.status != WalletTopUpRequest.Status.PENDING:
            return JsonResponse(
                {"detail": "Top-up is not pending."},
                status=409,
            )

        transaction_already_used = (
            WalletTopUpRequest.objects
            .exclude(pk=topup.pk)
            .filter(payment_transaction_id=transaction_id)
            .exists()
        )

        if transaction_already_used:
            logger.error("Paymob transaction ID was already used.")
            return JsonResponse(
                {"detail": "Transaction already associated with a top-up."},
                status=409,
            )

        credit_wallet(
            captain=topup.captain,
            amount=topup.amount,
            reference=f"wallet-topup:{topup.reference}",
            description=f"Paymob wallet top-up: {topup.reference}",
        )

        topup.status = WalletTopUpRequest.Status.COMPLETED
        topup.payment_transaction_id = transaction_id
        topup.payment_verified_at = timezone.now()
        topup.save(
            update_fields=[
                "status",
                "payment_transaction_id",
                "payment_verified_at",
                "updated_at",
            ]
        )

    return JsonResponse(
        {"detail": "Payment processed successfully."},
        status=200,
    )



def paymob_return(request):
    return HttpResponse(
        """
        <!DOCTYPE html>
        <html lang="ar" dir="rtl">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>نتيجة الدفع | SNIPER GO</title>
        </head>
        <body>
            <main>
                <h1>SNIPER GO</h1>
                <h2>تمت العودة من صفحة الدفع</h2>
                <p>
                    جارٍ التحقق من حالة الدفع.
                    سيتم تحديث رصيد المحفظة بعد تأكيد العملية.
                </p>
                <p>إذا لم يتحدث الرصيد، تحقق من حالة المعاملة لاحقًا.</p>
            </main>
        </body>
        </html>
        """,
        content_type="text/html; charset=utf-8",
    )