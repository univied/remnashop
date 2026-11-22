from typing import Optional
from uuid import UUID

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Request, Response, status
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import EventPublisher
from src.application.common.dao import TransactionDao
from src.application.common.uow import UnitOfWork
from src.application.events import ErrorEvent
from src.application.use_cases.gateways.queries.providers import GetPaymentGatewayInstance
from src.core.config import AppConfig
from src.core.constants import API_V1, PAYMENTS_WEBHOOK_PATH
from src.core.enums import PaymentGatewayType, TransactionStatus
from src.core.exceptions import GatewayNotConfiguredError
from src.infrastructure.payment_gateways import PlategaGateway
from src.infrastructure.payment_gateways.base import BasePaymentGateway
from src.infrastructure.taskiq.tasks.payments import handle_payment_transaction_task

router = APIRouter(prefix=API_V1 + PAYMENTS_WEBHOOK_PATH, include_in_schema=False)


async def _build_response(
    gateway: Optional[BasePaymentGateway], request: Request, gateway_type: str
) -> Response:
    if gateway is not None:
        try:
            return await gateway.build_webhook_response(request)
        except Exception:
            logger.exception(f"Failed to build webhook response for '{gateway_type}'")
    return Response(status_code=status.HTTP_200_OK)


async def _enqueue_payment_task(
    payment_id: UUID,
    payment_status: TransactionStatus,
    gateway_enum: PaymentGatewayType,
    gateway_type: str,
    config: AppConfig,
    event_publisher: EventPublisher,
) -> Optional[Response]:
    try:
        await handle_payment_transaction_task.kiq(payment_id, payment_status, gateway_enum)  # type: ignore[call-overload]
        return None
    except Exception as e:
        logger.exception(f"Failed to enqueue payment task for '{gateway_type}'")
        error_event = ErrorEvent(**config.build.data, exception=e)
        await event_publisher.publish(error_event)
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


async def _sync_platega_payment_method(
    gateway: BasePaymentGateway,
    payment_id: UUID,
    transaction_dao: TransactionDao,
    uow: UnitOfWork,
) -> None:
    if not isinstance(gateway, PlategaGateway) or gateway.selected_payment_method is None:
        return

    async with uow:
        transaction = await transaction_dao.get_by_payment_id(payment_id)
        if transaction is None:
            logger.warning(f"Transaction '{payment_id}' not found for Platega payment method sync")
            return

        transaction.payment_method = gateway.selected_payment_method
        await transaction_dao.update(transaction)
        await uow.commit()


async def _enqueue_anore_webhook(
    request: Request,
    payment_id: UUID,
    payment_status: TransactionStatus,
    config: AppConfig,
    event_publisher: EventPublisher,
    transaction_dao: TransactionDao,
    session: AsyncSession,
    uow: UnitOfWork,
) -> Response:
    delivery_id = request.headers.get("Anore-Delivery-Id")
    if not delivery_id:
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    async with uow:
        transaction = await transaction_dao.get_by_payment_id(payment_id)
        if transaction is None:
            # Checkout creation and its database commit can race the first webhook.
            return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        if transaction.gateway_type != PaymentGatewayType.ANORE:
            return Response(status_code=status.HTTP_400_BAD_REQUEST)

        inserted = await session.scalar(
            text("""
                INSERT INTO anore_webhook_deliveries (delivery_id) VALUES (:delivery_id)
                ON CONFLICT DO NOTHING
                RETURNING delivery_id
            """),
            {"delivery_id": delivery_id},
        )
        if inserted is None:
            return Response(status_code=status.HTTP_200_OK)

        enqueue_error = await _enqueue_payment_task(
            payment_id, payment_status, PaymentGatewayType.ANORE, "anore", config, event_publisher
        )
        if enqueue_error is not None:
            await uow.rollback()
            return enqueue_error
        await uow.commit()

    return Response(status_code=status.HTTP_200_OK)


async def _process_payment_webhook(
    gateway_type: str,
    request: Request,
    config: AppConfig,
    event_publisher: EventPublisher,
    get_payment_gateway_instance: GetPaymentGatewayInstance,
    transaction_dao: TransactionDao,
    uow: UnitOfWork,
    session: AsyncSession,
) -> Response:
    try:
        gateway_enum = PaymentGatewayType(gateway_type.upper())
    except ValueError:
        logger.exception(f"Invalid gateway type received: '{gateway_type}'")
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    gateway: Optional[BasePaymentGateway] = None
    try:
        gateway = await get_payment_gateway_instance.system(gateway_enum)
        result = await gateway.handle_webhook(request)
    except GatewayNotConfiguredError:
        logger.warning(f"Webhook received for inactive/unconfigured gateway '{gateway_enum}'")
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    except PermissionError:
        logger.warning(f"Webhook signature verification failed for '{gateway_enum}'")
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    except Exception as e:
        if gateway_enum == PaymentGatewayType.ANORE and isinstance(
            e, (ValueError, KeyError, TypeError)
        ):
            return Response(status_code=status.HTTP_400_BAD_REQUEST)
        logger.exception(f"Error processing webhook for '{gateway_type}': {e}")
        error_event = ErrorEvent(**config.build.data, exception=e)
        await event_publisher.publish(error_event)
        return await _build_response(gateway, request, gateway_type)

    if result is not None:
        payment_id, payment_status = result
        if gateway_enum == PaymentGatewayType.ANORE:
            return await _enqueue_anore_webhook(
                request,
                payment_id,
                payment_status,
                config,
                event_publisher,
                transaction_dao,
                session,
                uow,
            )
        if gateway_enum == PaymentGatewayType.PLATEGA:
            await _sync_platega_payment_method(gateway, payment_id, transaction_dao, uow)
        enqueue_error = await _enqueue_payment_task(
            payment_id, payment_status, gateway_enum, gateway_type, config, event_publisher
        )
        if enqueue_error is not None:
            return enqueue_error

    return await _build_response(gateway, request, gateway_type)


@router.post("/{gateway_type}")
@inject
async def payments_webhook(
    gateway_type: str,
    request: Request,
    config: FromDishka[AppConfig],
    event_publisher: FromDishka[EventPublisher],
    get_payment_gateway_instance: FromDishka[GetPaymentGatewayInstance],
    transaction_dao: FromDishka[TransactionDao],
    uow: FromDishka[UnitOfWork],
    session: FromDishka[AsyncSession],
) -> Response:
    return await _process_payment_webhook(
        gateway_type=gateway_type,
        request=request,
        config=config,
        event_publisher=event_publisher,
        get_payment_gateway_instance=get_payment_gateway_instance,
        transaction_dao=transaction_dao,
        uow=uow,
        session=session,
    )
