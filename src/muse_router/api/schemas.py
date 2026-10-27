from typing import Any, Optional, Union

from pydantic import BaseModel, Field, model_validator

from ..core.pool import AccountStatus, parse_cookie_header


class ChatMessage(BaseModel):
    role: str
    content: Union[str, list[Any], None] = None
    tool_calls: Optional[list[Any]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str = "muse-spark"
    messages: list[ChatMessage] = Field(..., min_length=1)
    stream: bool = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    top_p: Optional[float] = 1.0
    user: Optional[str] = None
    tools: Optional[list[dict[str, Any]]] = None
    tool_choice: Optional[Union[str, dict[str, Any]]] = None
    stream_options: Optional[dict[str, Any]] = None


class CompletionRequest(BaseModel):
    model: str = "muse-spark"
    prompt: Union[str, list[str]]
    stream: bool = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    top_p: Optional[float] = 1.0
    user: Optional[str] = None
    stream_options: Optional[dict[str, Any]] = None


class AccountCreateRequest(BaseModel):
    id: Optional[str] = None
    label: str = "account"
    cookies: Optional[dict[str, str]] = None
    cookie_header: Optional[str] = None
    proxy: Optional[str] = None
    max_concurrent: Optional[int] = None
    user_agent: Optional[str] = None

    @model_validator(mode="after")
    def _resolve_cookies(self):
        cookies = dict(self.cookies or {})
        if self.cookie_header:
            cookies.update(parse_cookie_header(self.cookie_header))
        if not cookies.get("hatch_sess"):
            raise ValueError("Missing required cookie: 'hatch_sess'")
        self.cookies = cookies
        return self


class AccountUpdateRequest(BaseModel):
    label: Optional[str] = None
    cookies: Optional[dict[str, str]] = None
    cookie_header: Optional[str] = None
    proxy: Optional[str] = None
    max_concurrent: Optional[int] = None
    status: Optional[str] = None

    @model_validator(mode="after")
    def _resolve(self):
        if self.cookie_header and not self.cookies:
            self.cookies = parse_cookie_header(self.cookie_header)
        if self.status is not None:
            # Validate early so a bad status is a 422, not a 500.
            AccountStatus(self.status.lower())
        return self
