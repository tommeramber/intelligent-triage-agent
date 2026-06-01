"""Readiness probe tests — offline, no OpenAI network calls."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.main import readiness


@pytest.mark.asyncio
async def test_readiness_ok_on_raw_models_200():
    """Readiness validates OpenAI via raw HTTP status, not paginated model parsing."""
    raw = MagicMock()
    raw.status_code = 200
    client = MagicMock()
    client.models.with_raw_response.list = AsyncMock(return_value=raw)

    request = MagicMock()
    request.app.state.openai_client = client

    result = await readiness(request)

    assert result.status == "ready"
    assert result.openai_reachable is True
    client.models.with_raw_response.list.assert_awaited_once()


@pytest.mark.asyncio
async def test_readiness_not_ready_on_non_200():
    from fastapi import HTTPException

    raw = MagicMock()
    raw.status_code = 503
    client = MagicMock()
    client.models.with_raw_response.list = AsyncMock(return_value=raw)

    request = MagicMock()
    request.app.state.openai_client = client

    with pytest.raises(HTTPException) as exc_info:
        await readiness(request)

    assert exc_info.value.status_code == 503
