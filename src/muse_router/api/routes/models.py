from fastapi import APIRouter, Depends

from ..deps import require_api_key

router = APIRouter()

MODELS = [
    {"id": "muse-spark", "object": "model", "created": 1735689600, "owned_by": "meta-muse"},
    {"id": "muse-chat", "object": "model", "created": 1735689600, "owned_by": "meta-muse"},
    {"id": "gpt-4o", "object": "model", "created": 1735689600, "owned_by": "system-alias"},
    {"id": "gpt-4o-mini", "object": "model", "created": 1735689600, "owned_by": "system-alias"},
    {"id": "claude-3-5-sonnet-20241022", "object": "model", "created": 1735689600, "owned_by": "system-alias"},
]


@router.get("/v1/models", dependencies=[Depends(require_api_key)])
async def list_models():
    return {"object": "list", "data": MODELS}
