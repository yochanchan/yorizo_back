import time
import urllib.request

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.core.config import settings

router = APIRouter(prefix="/api/speech", tags=["speech"])

EXPIRES_IN_SECONDS = 600
REFRESH_PADDING_SECONDS = 60
_token_cache: dict[str, tuple[str, float]] = {}


def _fetch_token_from_azure(region: str, key: str) -> str:
    url = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    request = urllib.request.Request(url, method="POST", data=b"")
    request.add_header("Ocp-Apim-Subscription-Key", key)
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = response.read()
    token = payload.decode("utf-8").strip()
    if not token:
        raise ValueError("empty token")
    return token


async def _issue_token(region: str, key: str) -> str:
    now = time.monotonic()
    cached = _token_cache.get(region)
    if cached and cached[1] > now:
        return cached[0]

    token = await run_in_threadpool(_fetch_token_from_azure, region, key)
    _token_cache[region] = (token, now + EXPIRES_IN_SECONDS - REFRESH_PADDING_SECONDS)
    return token


@router.post("/token")
async def create_speech_token() -> dict[str, object]:
    region = (settings.azure_speech_region or "").strip()
    key = settings.azure_speech_key
    if not region or not key:
        raise HTTPException(status_code=503, detail="Azure Speech not configured")

    try:
        token = await _issue_token(region, key)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - wrapped for clarity
        raise HTTPException(status_code=503, detail="Failed to issue speech token") from exc

    return {"token": token, "region": region, "expires_in": EXPIRES_IN_SECONDS}
