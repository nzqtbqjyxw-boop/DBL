import logging

from aiohttp import web

import sheets

log = logging.getLogger(__name__)

routes = web.RouteTableDef()


@routes.post("/yookassa-webhook")
async def yookassa_webhook(request: web.Request):
    try:
        data = await request.json()
    except Exception:
        log.exception("Webhook: не удалось распарсить JSON")
        return web.json_response({"status": "error"}, status=400)

    event = data.get("event")
    if event != "payment.succeeded":
        # Игнорируем любые другие события (ожидание, отмена и т.д.)
        return web.json_response({"status": "ignored"})

    payment_object = data.get("object", {})
    amount_value = payment_object.get("amount", {}).get("value")
    metadata = payment_object.get("metadata", {}) or {}
    # Email и имя можно получить либо из customer-блока ЮKassa, либо из metadata
    # (которые передаются формой Tilda при создании платежа).
    email = (
        payment_object.get("receipt", {}).get("customer", {}).get("email")
        or metadata.get("email", "")
    )
    name = metadata.get("name", "")
    username = metadata.get("username", "")

    if not email:
        log.error("Webhook: в платеже нет email, не могу создать участника. payload=%s", data)
        return web.json_response({"status": "no_email"}, status=200)

    try:
        price = int(float(amount_value))
    except (TypeError, ValueError):
        log.error("Webhook: не удалось распарсить сумму платежа: %s", amount_value)
        return web.json_response({"status": "bad_amount"}, status=200)

    row, participant_id = sheets.add_paid_via_webhook(
        name=name, email=email, username=username, price=price,
    )
    log.info("Webhook: оплата зачислена, row=%s participant_id=%s email=%s", row, participant_id, email)

    return web.json_response({"status": "ok"})


def create_app() -> web.Application:
    app = web.Application()
    app.add_routes(routes)
    return app
