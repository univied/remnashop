import hmac
import re
from decimal import Decimal
from typing import Final
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import orjson
from aiogram import Bot
from fastapi import Request
from msgspec.json import Encoder

from src.application.dto import PaymentGatewayDto, PaymentResultDto, UserDto
from src.application.dto.payment_gateway import AnoreGatewaySettingsDto
from src.core.config import AppConfig
from src.core.enums import TransactionStatus

from .base import BasePaymentGateway


class AnoreGateway(BasePaymentGateway):
    API_BASE: Final[str] = "https://api.anore.cc/v1/"
    _encoder = Encoder(decimal_format="number")

    def __init__(self, gateway: PaymentGatewayDto, bot: Bot, config: AppConfig) -> None:
        super().__init__(gateway, bot, config)
        settings = gateway.settings
        if (
            not isinstance(settings, AnoreGatewaySettingsDto)
            or not settings.is_configured
            or settings.api_key is None
            or settings.secret_key is None
        ):
            raise ValueError("Anore credentials are incomplete")

        self._shop_id = settings.shop_id
        self._h2h_enabled = settings.h2h_enabled
        self._api_key = settings.api_key.get_secret_value()
        self._secret = settings.secret_key.get_secret_value().encode()
        self._franchise = self._get_franchise(settings)
        self._client = self._make_client(
            base_url=self.API_BASE, headers={"Authorization": f"Bearer {self._api_key}"}
        )

    async def handle_create_payment(self, amount: Decimal, details: str) -> PaymentResultDto:
        raise ValueError("Anore requires a payer identity")

    async def create_payment_for_user(
        self, amount: Decimal, details: str, user: UserDto, *, is_test: bool = False
    ) -> PaymentResultDto:
        if not is_test and self._api_key.startswith("an_test_"):
            raise ValueError("Use an Anore live key for customer payments")
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Invalid Anore payment amount")

        identity = self._get_identity(user)
        receipt_email = identity["value"]
        if identity["type"] == "telegram_id":
            receipt_email = f"{receipt_email}@telegram.org"
        redirect_url = await self._get_bot_redirect_url()
        payload = {
            "amount": amount,
            "currency": "RUB",
            "description": details,
            "shopId": self._shop_id,
            "orderId": str(uuid4()),
            "user_identity": identity,
            "email": receipt_email,
            "getbackurl": redirect_url,
            "successurl": redirect_url,
            "failurl": redirect_url,
            "callbackUrl": self.config.get_webhook(self.data.type),
        }
        if self._h2h_enabled:
            payload["methods"] = ["sbp"]
        if self._franchise is not None:
            payload["franchise"] = self._franchise

        response = await self._client.post(
            "payments",
            content=self._encoder.encode(payload),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("success") is not True:
            raise ValueError("Anore did not create the invoice")

        payment_id = UUID(data["id"])
        direct_url = data.get("sbpUrl") if self._h2h_enabled else None
        payment_url = direct_url or data["paymentUrl"]
        url = urlsplit(payment_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Anore returned an invalid checkout URL")
        return PaymentResultDto(id=payment_id, url=payment_url)

    async def handle_webhook(self, request: Request) -> tuple[UUID, TransactionStatus] | None:
        body = await request.body()
        signature = request.headers.get("Anore-Signature", "")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", signature) or not hmac.compare_digest(
            hmac.digest(self._secret, body, "sha256").hex(), signature.lower()
        ):
            raise PermissionError("Invalid Anore webhook signature")

        data = orjson.loads(body)
        if not isinstance(data, dict):
            raise ValueError("Invalid Anore webhook payload")
        match data.get("event"), data.get("status"):
            case "payment.succeeded", "paid":
                return UUID(data["id"]), TransactionStatus.COMPLETED
            case "payment.expired", "expired":
                return UUID(data["id"]), TransactionStatus.CANCELED
            case "payment.succeeded" | "payment.expired", _:
                raise ValueError("Unexpected Anore payment status")
            case _:
                return None

    @staticmethod
    def _get_identity(user: UserDto) -> dict[str, str]:
        if user.telegram_id is not None and user.telegram_id > 0:
            identity = {"type": "telegram_id", "value": str(user.telegram_id)}
            username = (user.username or "").removeprefix("@")
            if re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
                identity["telegram_username"] = username
            return identity
        if user.is_email_verified and user.email:
            return {"type": "email", "value": user.email}
        raise ValueError("Anore requires a Telegram ID or a verified email")

    @staticmethod
    def _get_franchise(settings: AnoreGatewaySettingsDto) -> dict[str, str] | None:
        if settings.franchise_type is None:
            return None

        address = settings.franchise_address or ""
        owner_id = settings.franchise_owner_telegram_id or ""
        if not re.fullmatch(r"[1-9][0-9]{0,18}", owner_id) or int(owner_id) > 2**63 - 1:
            raise ValueError("Invalid Anore franchise owner Telegram ID")

        match settings.franchise_type:
            case "telegram_bot":
                address = address.removeprefix("@").lower()
                if not re.fullmatch(r"[a-z][a-z0-9_]{4,31}", address):
                    raise ValueError("Invalid Anore franchise Telegram bot username")
            case "website":
                origin = urlsplit(address)
                if (
                    origin.scheme != "https"
                    or not origin.hostname
                    or origin.netloc != origin.hostname
                    or origin.path not in ("", "/")
                    or address.rstrip("/") != f"https://{origin.hostname}"
                    or not re.fullmatch(r"[a-z0-9.-]+", origin.hostname)
                ):
                    raise ValueError("Anore franchise website must be a canonical HTTPS origin")
                address = f"https://{origin.hostname}"
            case _:
                raise ValueError("Invalid Anore franchise type")

        franchise = {
            "type": settings.franchise_type,
            "address": address,
            "owner_telegram_id": owner_id,
        }
        if settings.franchise_owner_telegram_username:
            username = settings.franchise_owner_telegram_username.removeprefix("@").lower()
            if not re.fullmatch(r"[a-z][a-z0-9_]{4,31}", username):
                raise ValueError("Invalid Anore franchise owner Telegram username")
            franchise["owner_telegram_username"] = username
        return franchise
