"""Public webhook endpoints for IM platforms (no cookie auth)."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from app.database import SessionLocal
from app.models import ImChannel
from app.services import channels as channel_pkg  # noqa: F401
from app.services.channels.runtime import dispatch_webhook, process_inbound_bg, send_outbound_hint_bg

router = APIRouter(tags=["channel-hooks"])


@router.api_route(
    "/hooks/channels/{provider}/{channel_id}/{webhook_secret}",
    methods=["GET", "POST"],
)
async def channel_webhook(
    provider: str,
    channel_id: str,
    webhook_secret: str,
    request: Request,
    background_tasks: BackgroundTasks,
):
    db = SessionLocal()
    try:
        channel = (
            db.query(ImChannel)
            .filter(
                ImChannel.id == channel_id,
                ImChannel.provider == provider,
                ImChannel.webhook_secret == webhook_secret,
            )
            .first()
        )
        if not channel:
            return JSONResponse({"error": "not found"}, status_code=404)
        if not channel.enabled:
            return JSONResponse({"error": "disabled"}, status_code=403)

        body = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        # also keep original-case helpers used by adapters
        for k, v in request.headers.items():
            headers[k] = v
        query = {k: v for k, v in request.query_params.items()}

        result, inbound = await dispatch_webhook(
            db,
            channel,
            method=request.method,
            headers=headers,
            query=query,
            body=body,
        )
        if inbound:
            background_tasks.add_task(process_inbound_bg, channel.id, inbound)
        elif result.outbound_hint:
            background_tasks.add_task(send_outbound_hint_bg, channel.id, result.outbound_hint)

        if result.content_type == "text/plain":
            return PlainTextResponse(
                content=str(result.body if result.body is not None else ""),
                status_code=result.status_code,
            )
        return JSONResponse(
            content=result.body if result.body is not None else {"ok": True},
            status_code=result.status_code,
        )
    finally:
        db.close()
