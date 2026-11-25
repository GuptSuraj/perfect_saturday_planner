import os
from datetime import timedelta

import httpx
from langchain_openai import ChatOpenAI
from livekit import api
from livekit.api.access_token import InferenceGrants


def planning_model(client: httpx.AsyncClient) -> ChatOpenAI:
    model = os.getenv("OPENAI_MODEL") or "gpt-5-mini"
    common = {"timeout": 23, "max_retries": 1, "max_completion_tokens": 2600, "http_async_client": client}
    if model.removeprefix("openai/").startswith("gpt-5"):
        common["reasoning_effort"] = "low"
    if key := os.getenv("OPENAI_API_KEY"):
        if model.removeprefix("openai/").startswith("gpt-5-mini"):
            common["reasoning_effort"] = "minimal"
        return ChatOpenAI(model=model.removeprefix("openai/"), api_key=key, **common)

    # LiveKit Inference uses an authenticated OpenAI Chat Completions gateway.
    # Sign a fresh, inference-only token for each request using the public token API.
    async def inference_token() -> str:
        return (
            api.AccessToken()
            .with_identity("agent")
            .with_inference_grants(InferenceGrants(perform=True))
            .with_ttl(timedelta(minutes=5))
            .to_jwt()
        )

    return ChatOpenAI(
        model=model if "/" in model else f"openai/{model}",
        api_key=inference_token,
        base_url="https://agent-gateway.livekit.cloud/v1",
        **common,
    )
