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
        return web.json_response({"status": "ignored"})

    payment_object = data.get("object", {})
    amount_value = payment_object.get("amount", {}).get("value")
    metadata = payment_object.get("metadata", {}) or {}
    email = (
        payment_object.get("receipt", {}).get("customer", {}).get("email")
        or metadata.get("email", "")
    )
    name = metadata.get("name", "")
    username = metadata.get("username", "")

    if not email:
        log.error("Webhook: в платеже нет email. payload=%s", data)
        return web.json_response({"status": "no_email"}, status=200)

    try:
        price = int(float(amount_value))
    except (TypeError, ValueError):
        log.error("Webhook: не удалось распарсить сумму: %s", amount_value)
        return web.json_response({"status": "bad_amount"}, status=200)

    row, participant_id = sheets.add_paid_via_webhook(
        name=name, email=email, username=username, price=price,
    )
    log.info("Webhook: оплата зачислена row=%s id=%s email=%s", row, participant_id, email)

    return web.json_response({"status": "ok"})


# --- Tilda webhook (после оплаты формы) ---

async def _extract_tilda_data(request: web.Request) -> dict:
    """
    Tilda шлёт данные формы как form-urlencoded (по умолчанию)
    или как JSON (если настроено). Универсально пробуем оба.
    """
    content_type = (request.headers.get("Content-Type") or "").lower()
    if "application/json" in content_type:
        try:
            return await request.json()
        except Exception:
            log.exception("Tilda: не удалось распарсить JSON")
            return {}

    try:
        post = await request.post()
        # MultiDict → обычный dict
        data = {k: v for k, v in post.items()}
        return data
    except Exception:
        log.exception("Tilda: не удалось распарсить form-data")
        return {}


def _pick_first(data: dict, keys) -> str:
    """Возвращает первое непустое значение по списку возможных ключей."""
    for k in keys:
        v = data.get(k)
        if v:
            return str(v).strip()
    return ""


@routes.post("/tilda-webhook")
async def tilda_webhook(request: web.Request):
    try:
        data = await _extract_tilda_data(request)
    except Exception:
        log.exception("Tilda: ошибка разбора запроса")
        data = {}

    # Логируем ВСЁ, что прислала Tilda — потом подкорректируем парсинг по факту
    log.info("Tilda webhook payload: %s", data)

    # Пробуем вытащить поля по разным возможным именам
    email = _pick_first(data, ["Email", "email", "E-mail", "mail"])
    name = _pick_first(data, ["Name", "name", "Имя", "FIO", "fio"])
    username = _pick_first(data, ["Username", "username", "Telegram", "telegram", "tg"])
    amount_raw = _pick_first(
        data,
        ["Payment[amount]", "payment[amount]", "amount", "Amount",
         "Payment[sum]", "sum", "Sum", "стоимость", "Сумма"],
    )

    # Если email есть — пытаемся создать участника
    if email:
        try:
            price = int(float(str(amount_raw).replace(",", "."))) if amount_raw else 0
        except (TypeError, ValueError):
            price = 0

        # Если сумма неизвестна — тариф определим как "неизвестно"
        if price <= 0:
            log.warning("Tilda: не удалось определить сумму (%r), пишу как 0", amount_raw)

        try:
            row, participant_id = sheets.add_paid_via_webhook(
                name=name, email=email, username=username, price=price,
            )
            log.info("Tilda: оплата зачислена row=%s id=%s email=%s",
                     row, participant_id, email)
        except Exception:
            log.exception("Tilda: ошибка записи в Sheets")
    else:
        log.warning("Tilda: нет email в payload, ничего не записываю. data=%s", data)

    # Tilda ждёт 200 и "ok" — иначе будет считать webhook битым
    return web.json_response({"status": "ok"})


# GET-обработчик — на случай, если Tilda проверяет доступность через GET
@routes.get("/tilda-webhook")
async def tilda_webhook_get(request: web.Request):
    return web.json_response({"status": "ok"})


def create_app() -> web.Application:
    app = web.Application()
    app.add_routes(routes)
    return app
