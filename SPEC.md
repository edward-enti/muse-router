# Technical Specification: `muse-router` (Muse.ai to OpenAI API Gateway)

> **Hướng dẫn dành cho AI Coding Agent:**  
> Tài liệu này là bản đặc tả kỹ thuật toàn diện (Single Source of Truth) để xây dựng dự án `muse-router`. Hãy tuân thủ nghiêm ngặt kiến trúc phân tầng, kiểu dữ liệu Pydantic v2, cơ chế xử lý bất đồng bộ (`asyncio`), quy tắc xoay vòng tài khoản (Account Pool) và luồng First-Chunk Failover được mô tả dưới đây. Không sử dụng các thư viện đồng bộ gây nghẽn (blocking I/O) trong luồng xử lý chính.

---

## 1. Tổng quan Hệ thống (System Overview)

### 1.1. Mục tiêu
Xây dựng một dịch vụ Reverse Proxy / API Gateway hiệu năng cao bằng Python (FastAPI) giúp chuyển đổi nền tảng web **muse.ai** (Meta Personal AI Agent - codename nội bộ **Hatch**, chạy mô hình *Muse Spark*) thành chuẩn **OpenAI Compatible API** (`/v1/chat/completions`, `/v1/completions`, `/v1/models`).

### 1.2. Tính năng cốt lõi
1. **OpenAI API Drop-in Replacement:** Tương thích 100% với OpenAI SDK, Cursor, Windsurf, LobeChat, OpenWebUI, NextChat (hỗ trợ cả `stream: true` qua Server-Sent Events và `stream: false`).
2. **Multi-Account Pool & Smart Rotation:** Quản lý danh sách nhiều tài khoản `muse.ai` thông qua bộ cookies phiên (`hatch_*`). Hỗ trợ xoay vòng theo chiến lược `round_robin`, `least_loaded`, và `session_affinity`.
3. **Zero-Downtime First-Chunk Failover:** Chặn (buffer) chunk dữ liệu đầu tiên từ `muse.ai` trước khi gửi HTTP `200 OK` về client. Nếu tài khoản gặp lỗi (hết hạn cookie, dính rate-limit 429, lỗi mạng) trước khi sinh ra token đầu tiên, hệ thống tự động thu hồi phiên, đánh dấu cooldown và chuyển sang tài khoản tiếp theo trong Pool hoàn toàn trong suốt với client.
4. **Emulated Tool/Function Calling:** Hỗ trợ OpenAI `tools` / `tool_choice` thông qua kỹ thuật Prompt Injection & XML/JSON Stream Parsing.
5. **Pluggable Upstream Driver:** Tách biệt hoàn toàn logic điều phối với giao thức kết nối upstream thông qua abstract interface `MuseDriver` (`BrowserCDPDriver`, `DirectHTTPDriver`, `MockDriver`).
6. **Admin REST API & Hot-Reload Persistence:** Cung cấp API quản trị để thêm/xóa/kiểm tra tài khoản trực tiếp lúc runtime, tự động đồng bộ trạng thái xuống đĩa (`data/accounts.json`) một cách an toàn (atomic write).

---

## 2. Kiến trúc Phân tầng & Cấu trúc Thư mục

### 2.1. Sơ đồ Luồng xử lý (Data Flow)
```text
Client Request (/v1/chat/completions)
  │
  ├─► [1. Auth Middleware] ──► Kiểm tra Bearer Token (MUSE_API_KEY)
  │
  ├─► [2. Context Builder] ──► Chuẩn hóa mảng `messages` + `tools` thành Prompt kịch bản
  │
  ├─► [3. Gateway Orchestrator] (Vòng lặp Retry tối đa MUSE_MAX_RETRIES lần)
  │     │
  │     ├─► (a) Xin cấp phát Lease từ [AccountPool] (Thread/Async-safe)
  │     ├─► (b) Gọi `driver.generate_stream(account, prompt, ...)`
  │     ├─► (c) Chờ `first_chunk = await anext(stream)`
  │     │     ├── Thành công: Trả về HTTP 200 (StreamingResponse hoặc gom JSON)
  │     │     └── Thất bại: Phân loại Exception -> Cập nhật trạng thái Account -> Thử lại (a)
  │     └─► (d) Khối `finally`: Luôn giải phóng `active_leases` cho Account
  │
  └─► [4. Background Keep-Alive Worker] ──► Định kỳ kiểm tra & làm mới phiên `hatch_vml`
```

### 2.2. Cấu trúc Thư mục Chuẩn (Project Structure)
```text
muse-router/
├── .env.example
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── README.md
├── SPEC.md
├── data/
│   └── .gitkeep                 # Nơi lưu trữ accounts.json lúc runtime
├── src/
│   └── muse_router/
│       ├── __init__.py
│       ├── main.py              # Khởi tạo FastAPI, Lifespan, CORS, Exception Handlers
│       ├── config.py            # Pydantic BaseSettings đọc biến môi trường
│       ├── errors.py            # Custom Exceptions & OpenAI Error Formatter
│       ├── utils/
│       │   ├── __init__.py
│       │   ├── tokenizer.py     # Đếm token bằng tiktoken (cl100k_base)
│       │   └── id_gen.py        # Sinh ID chuẩn: chatcmpl-xxx, cmpl-xxx, call_xxx
│       ├── api/
│       │   ├── __init__.py
│       │   ├── deps.py          # Dependency Injection (Auth, Pool, Gateway)
│       │   ├── schemas.py       # Toàn bộ Pydantic v2 Models (OpenAI & Admin)
│       │   └── routes/
│       │       ├── __init__.py
│       │       ├── chat.py      # POST /v1/chat/completions, POST /v1/completions
│       │       ├── models.py    # GET /v1/models
│       │       ├── admin.py     # CRUD /admin/accounts, /admin/stats
│       │       └── health.py    # GET /healthz, GET /readyz
│       ├── core/
│       │   ├── __init__.py
│       │   ├── pool.py          # AccountPool, AccountLease context manager, Persistence
│       │   ├── prompt.py        # Multi-turn Messages -> Prompt & Tool Calling Parser
│       │   ├── gateway.py       # Orchestration, First-chunk failover, SSE Generator
│       │   └── keepalive.py     # Background worker duy trì phiên tài khoản
│       └── drivers/
│           ├── __init__.py
│           ├── base.py          # Abstract Base Class: MuseDriver
│           ├── mock.py          # MockDriver phục vụ Unit Test & Benchmark
│           ├── browser_cdp.py   # Playwright Chromium Headless + Network/DOM Streamer
│           └── http_direct.py   # Direct HTTP/WebSocket với curl_cffi
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_pool.py
    ├── test_prompt.py
    ├── test_gateway_failover.py
    └── test_api_routes.py
```

---

## 3. Đặc tả Chi tiết Từng Module

### 3.1. Cấu hình Hệ thống (`src/muse_router/config.py`)
Sử dụng `pydantic_settings.BaseSettings` với tiền tố `MUSE_`:

| Biến môi trường | Kiểu dữ liệu | Mặc định | Mô tả |
| :--- | :--- | :--- | :--- |
| `MUSE_HOST` | `str` | `"0.0.0.0"` | Địa chỉ bind server |
| `MUSE_PORT` | `int` | `18610` | Cổng lắng nghe |
| `MUSE_API_KEY` | `str` | `"sk-muse-default-key"` | API Key cho Client gọi `/v1/*` (hỗ trợ nhiều key cách nhau dấu phẩy) |
| `MUSE_ADMIN_KEY` | `str` | `"sk-muse-admin-key"` | API Key cho Admin gọi `/admin/*` |
| `MUSE_DRIVER` | `Literal["mock", "browser", "http"]` | `"browser"` | Loại driver kết nối với `muse.ai` |
| `MUSE_ACCOUNTS_FILE` | `Path` | `Path("data/accounts.json")` | Đường dẫn file lưu trạng thái Account Pool |
| `MUSE_POOL_STRATEGY` | `Literal["round_robin", "least_loaded", "affinity"]` | `"round_robin"` | Thuật toán chọn tài khoản |
| `MUSE_MAX_RETRIES` | `int` | `3` | Số lần tự động đổi tài khoản tối đa trên mỗi request lỗi |
| `MUSE_DEFAULT_MAX_CONCURRENT` | `int` | `2` | Số luồng chạy song song tối đa mặc định trên 1 tài khoản |
| `MUSE_QUOTA_COOLDOWN_SEC` | `int` | `1800` | Thời gian nghỉ (giây) khi tài khoản bị 429 / hết quota |
| `MUSE_TRANSIENT_COOLDOWN_SEC` | `int` | `45` | Thời gian nghỉ (giây) khi gặp lỗi mạng / timeout |
| `MUSE_REQUEST_TIMEOUT_SEC` | `float` | `120.0` | Timeout tối đa cho 1 lần sinh phản hồi |
| `MUSE_FIRST_CHUNK_TIMEOUT_SEC`| `float` | `25.0` | Timeout chờ token đầu tiên từ `muse.ai` trước khi ép failover |
| `MUSE_HEADLESS` | `bool` | `True` | Chạy trình duyệt ở chế độ ẩn (cho `browser` driver) |
| `MUSE_KEEPALIVE_INTERVAL_SEC` | `int` | `900` | Chu kỳ chạy ngầm kiểm tra/giữ phiên (0 = tắt) |

---

### 3.2. Hệ thống Phân loại Lỗi (`src/muse_router/errors.py`)

Định nghĩa cây kế thừa Exception rõ ràng để `Gateway` và `AccountPool` xử lý tự động:

```python
class MuseRouterError(Exception):
    """Base exception cho toàn hệ thống"""
    def __init__(self, message: str, status_code: int = 500, error_type: str = "server_error", code: str | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        self.code = code

# --- Các lỗi Upstream (Từ muse.ai) ---
class UpstreamError(MuseRouterError):
    """Lỗi phát sinh khi giao tiếp với muse.ai"""

class AccountAuthExpiredError(UpstreamError):
    """Cookie hatch_sess hết hạn hoặc bị redirect về trang login -> Đánh dấu EXPIRED"""

class AccountQuotaExceededError(UpstreamError):
    """Bị giới hạn tốc độ (429) hoặc hết lượt dùng -> Đánh dấu COOLING_DOWN (dài hạn)"""

class UpstreamTransientError(UpstreamError):
    """Lỗi mạng, timeout, 502/503/504 hoặc VM chưa kịp khởi tạo -> Đánh dấu COOLING_DOWN (ngắn hạn)"""

# --- Các lỗi Pool ---
class NoAvailableAccountError(MuseRouterError):
    """Không còn tài khoản nào ở trạng thái READY hoặc đều đã kịch trần max_concurrent -> HTTP 503"""
```

**Định dạng trả về khi lỗi (Chuẩn OpenAI Error JSON):**
Mọi HTTP Exception trả về cho client ở nhánh `/v1/*` bắt buộc phải tuân thủ cấu trúc:
```json
{
  "error": {
    "message": "No available muse.ai accounts in pool (all cooling down or busy).",
    "type": "service_unavailable",
    "param": null,
    "code": "no_available_account"
  }
}
```

---

### 3.3. Quản lý Tài khoản & Xoay vòng (`src/muse_router/core/pool.py`)

#### A. Cấu trúc Cookie bắt buộc của `muse.ai` (Hatch Protocol)
Mỗi tài khoản `muse.ai` hợp lệ cần bộ cookies:
*   **Bắt buộc:** `hatch_sess` (Session Token chính).
*   **Khuyến nghị (tự động cập nhật nếu trình duyệt nhận Set-Cookie mới):**
    *   `hatch_gw`: Gateway routing cookie.
    *   `hatch_vml`: Workspace VM Lease token.
    *   `hatch_native_auth_device`: Device UUID.
    *   `datr`: Meta browser/anti-bot fingerprint cookie.

#### B. Data Model (`MuseAccount`)
```python
class AccountStatus(str, Enum):
    READY = "ready"
    COOLING_DOWN = "cooling_down"
    EXPIRED = "expired"
    DISABLED = "disabled"

class MuseAccount(BaseModel):
    id: str = Field(default_factory=lambda: f"acc_{uuid.uuid4().hex[:8]}")
    label: str
    cookies: dict[str, str]
    user_agent: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    proxy: Optional[str] = None
    status: AccountStatus = AccountStatus.READY
    max_concurrent: int = 2
    active_leases: int = 0
    cooldown_until: Optional[float] = None
    last_used_at: float = 0.0
    total_requests: int = 0
    success_requests: int = 0
    failed_requests: int = 0
    last_error: Optional[str] = None

    @validator("cookies")
    def validate_required_cookies(cls, v: dict[str, str]) -> dict[str, str]:
        if "hatch_sess" not in v or not v["hatch_sess"].strip():
            raise ValueError("Missing required cookie: 'hatch_sess'")
        return v
```

#### C. Logic Hoạt động của `AccountPool`
1. **Đồng bộ hóa (`asyncio.Lock`):** Tất cả thao tác đọc/ghi trạng thái tài khoản (`lease`, `release`, `mark_cooldown`, `mark_expired`) phải nằm trong `async with self._lock:`.
2. **Tự động Phục hồi Cooldown (Auto-Recovery):**
   Trước mỗi lần tìm tài khoản khả dụng, kiểm tra toàn bộ các tài khoản đang có `status == AccountStatus.COOLING_DOWN`. Nếu `time.time() >= account.cooldown_until`, tự động chuyển `status = AccountStatus.READY` và `cooldown_until = None`.
3. **Điều kiện Khả dụng (`is_available`):**
   Một tài khoản được phép chọn khi và chỉ khi:
   * `account.status == AccountStatus.READY`
   * `account.active_leases < account.max_concurrent`
   * `account.id not in excluded_account_ids` (Danh sách các tài khoản vừa bị lỗi trong cùng một request đang retry).
4. **Thuật toán Chọn Tài khoản:**
   * `round_robin`: Sắp xếp các tài khoản khả dụng theo `(account.last_used_at ASC, account.active_leases ASC)`, chọn phần tử đầu tiên.
   * `least_loaded`: Sắp xếp theo tỷ lệ tải `(account.active_leases / account.max_concurrent ASC, account.last_used_at ASC)`.
   * `affinity`: Nhận thêm tham số `session_key: Optional[str]`. Dùng hàm băm `hashlib.sha256(session_key.encode()).hexdigest()` để ánh xạ cố định vào một tài khoản. Nếu tài khoản đó đang bận hoặc không khả dụng, tự động fallback về `least_loaded`.
5. **Context Manager `lease()`:**
   Cung cấp async context manager để đảm bảo `active_leases` không bao giờ bị rò rỉ (leak) kể cả khi client ngắt kết nối giữa chừng (GeneratorExit / CancelledError):
   ```python
   @asynccontextmanager
   async def acquire_lease(
       self,
       excluded_ids: set[str] | None = None,
       session_key: str | None = None
   ) -> AsyncIterator[MuseAccount]:
       account = await self._select_and_increment(excluded_ids, session_key)
       try:
           yield account
       finally:
           await self._decrement_lease(account.id)
   ```
6. **Lưu trữ Bền vững (Atomic File Persistence):**
   Khi thêm/sửa/xóa tài khoản hoặc khi trạng thái tài khoản chuyển sang `EXPIRED` / `COOLING_DOWN`, ghi danh sách tài khoản ra file tạm `data/accounts.json.tmp` rồi dùng `os.replace()` đè lên `data/accounts.json` để tránh hỏng file khi crash. Không lưu trường runtime `active_leases` (luôn reset về `0` khi khởi động server).

---

### 3.4. Bộ Chuyển đổi Ngữ cảnh & Tool Calling (`src/muse_router/core/prompt.py`)

Vì mỗi request trong hệ thống xoay vòng có thể rơi vào một tài khoản `muse.ai` khác nhau, `muse-router` hoạt động theo mô hình **Stateless Context Serialization** (Đóng gói toàn bộ lịch sử hội thoại vào 1 lượt gửi).

#### A. Quy tắc Chuyển đổi `messages` -> Chuỗi `prompt`
*   Nếu chỉ có 1 message duy nhất từ `user` và không có `system` hay `tools`: Gửi nguyên văn nội dung `content` của user.
*   Nếu là hội thoại nhiều lượt (multi-turn) hoặc có `system` prompt:
    Chuẩn hóa thành định dạng rõ ràng để mô hình *Muse Spark* không bị nhầm vai:
    ```text
    <|system_instructions|>
    {Nội dung các message có role="system" hoặc role="developer"}
    </|system_instructions|>

    <|conversation_history|>
    User: {Tin nhắn 1}
    Assistant: {Phản hồi 1}
    Tool ({tool_call_id}): {Kết quả thực thi tool}
    </|conversation_history|>

    <|current_user_message|>
    {Tin nhắn cuối cùng của User}
    </|current_user_message|>
    Please respond directly to the current_user_message as the Assistant, without repeating the XML tags.
    ```
*   **Xử lý Multimodal `content`:** Trong chuẩn OpenAI, `message.content` có thể là `str` hoặc `list[dict]` (ví dụ `[{"type": "text", "text": "..."}, {"type": "image_url", ...}]`). Bộ parser phải trích xuất và nối toàn bộ các phần tử `"type": "text"` lại với nhau.

#### B. Giả lập Function/Tool Calling (`tools` & `tool_choice`)
Nếu request truyền danh sách `tools`:
1. Chèn hướng dẫn vào khối `<|system_instructions|>` yêu cầu model nếu cần gọi hàm thì **chỉ** xuất ra khối XML theo định dạng:
   ```xml
   <tool_call>
   {"name": "function_name", "arguments": {"arg1": "val1"}}
   </tool_call>
   ```
2. Ở chiều trả về (Response Parser): Nếu phát hiện thẻ `<tool_call>...</tool_call>` trong nội dung phản hồi của `muse.ai`, tự động bóc tách JSON bên trong và chuyển đổi sang cấu trúc `choices[0].message.tool_calls` (hoặc `delta.tool_calls` khi stream) với `finish_reason = "tool_calls"`.

---

### 3.5. Giao diện Kết nối Upstream (`src/muse_router/drivers/`)

#### A. Abstract Interface (`base.py`)
```python
class DriverStreamChunk(BaseModel):
    text: str
    updated_cookies: Optional[dict[str, str]] = None

class MuseDriver(ABC):
    @abstractmethod
    async def initialize(self) -> None:
        """Khởi tạo tài nguyên (VD: bật Playwright Browser instance)"""

    @abstractmethod
    async def shutdown(self) -> None:
        """Giải phóng tài nguyên khi tắt server"""

    @abstractmethod
    async def generate_stream(
        self,
        account: MuseAccount,
        prompt: str,
        model: str,
        timeout: float = 120.0,
    ) -> AsyncIterator[DriverStreamChunk]:
        """Gửi prompt tới muse.ai và yield từng phần văn bản (delta text)"""

    @abstractmethod
    async def verify_account(self, account: MuseAccount) -> bool:
        """Kiểm tra nhanh tài khoản còn hợp lệ hay không"""
```

#### B. Chi tiết các Driver cần triển khai
1. **`MockDriver` (`drivers/mock.py`):**
   * Dùng cho môi trường phát triển và chạy `pytest`.
   * Hỗ trợ giả lập lỗi thông qua nội dung prompt hoặc label của account:
     * Nếu `account.cookies.get("hatch_sess") == "expired_token"` -> Ném `AccountAuthExpiredError`.
     * Nếu `account.cookies.get("hatch_sess") == "ratelimit_token"` -> Ném `AccountQuotaExceededError`.
     * Mặc định: Chia câu trả lời mẫu thành từng token cách nhau `0.02s` và yield về `Gateway`.

2. **`BrowserCDPDriver` (`drivers/browser_cdp.py`):**
   * Sử dụng `playwright.async_api`. Khởi tạo 1 tiến trình Chromium dùng chung (hoặc mỗi proxy một browser context riêng).
   * Với mỗi `account.id`, duy trì một `BrowserContext` được cache trong bộ nhớ (kèm cấu hình `proxy` và `user_agent` của account đó).
   * Nạp toàn bộ `account.cookies` cho miền `.muse.ai` và `muse.ai`.
   * Điều hướng tới `https://muse.ai/`.
     * **Kiểm tra Auth:** Nếu URL bị chuyển hướng sang trang đăng nhập (`/login`, `/auth`, `facebook.com`, `meta.com/auth`) hoặc xuất hiện nút Sign In -> Ném `AccountAuthExpiredError` ngay lập tức.
   * **Cơ chế Gửi & Đọc Stream:**
     * Bắt sự kiện mạng (`page.on("response")` / `page.on("websocket")`) để chặn bắt các gói SSE/WebSocket hoặc JSON stream từ backend của `muse.ai` (ưu tiên số 1), **kết hợp** với `MutationObserver` đọc sự thay đổi text của phần tử tin nhắn trả lời cuối cùng trên DOM (fallback an toàn nếu `muse.ai` đổi tên trường JSON).
     * Điền `prompt` vào ô nhập liệu (`textarea` hoặc `[contenteditable="true"]`) và kích hoạt gửi.
     * Liên tục đẩy (`yield`) phần văn bản mới tăng thêm (`new_text[len(old_text):]`) vào `asyncio.Queue` để `generate_stream` trả về theo thời gian thực.
     * Sau khi hoàn tất câu trả lời, đọc lại `await context.cookies()` để cập nhật các cookie mới (`hatch_vml`, `hatch_gw`) vào `account.cookies`.

3. **`DirectHTTPDriver` (`drivers/http_direct.py`):**
   * Sử dụng `httpx.AsyncClient` (hoặc `curl_cffi.requests.AsyncSession` nếu có cài đặt) để gửi trực tiếp request tới endpoint hội thoại của `muse.ai` kèm header `Cookie: hatch_sess=...; hatch_gw=...; hatch_vml=...`.
   * Chuyển đổi mã lỗi HTTP:
     * `401`, `403` -> `AccountAuthExpiredError`
     * `429` -> `AccountQuotaExceededError`
     * `500`, `502`, `503`, `504`, `TimeoutException` -> `UpstreamTransientError`

---

### 3.6. Điều phối Gateway & First-Chunk Failover (`src/muse_router/core/gateway.py`)

Đây là trái tim của hệ thống. Phải cài đặt đúng thuật toán **First-Chunk Pre-fetching** để đảm bảo có thể đổi tài khoản ngay cả khi client yêu cầu `stream: true`.

```python
async def execute_with_failover(
    self,
    prompt: str,
    model: str,
    session_key: str | None = None,
) -> tuple[MuseAccount, AsyncIterator[str]]:
    """
    Thực hiện lấy tài khoản, gọi upstream và đợi bằng được chunk đầu tiên.
    Nếu lỗi trước khi có chunk đầu tiên -> Đánh dấu tài khoản & thử tài khoản khác.
    Trả về: (account, combined_async_generator)
    """
    excluded_ids: set[str] = set()
    last_error: Exception | None = None

    for attempt in range(self.settings.max_retries):
        cm = self.pool.acquire_lease(excluded_ids=excluded_ids, session_key=session_key)
        account = await cm.__aenter__()
        excluded_ids.add(account.id)

        try:
            raw_stream = self.driver.generate_stream(
                account=account,
                prompt=prompt,
                model=model,
                timeout=self.settings.request_timeout_sec,
            )
            # Đợi chunk đầu tiên với timeout ngắn hơn (first_chunk_timeout_sec)
            first_chunk = await asyncio.wait_for(
                anext(raw_stream),
                timeout=self.settings.first_chunk_timeout_sec
            )

            # Nếu tới được đây -> Upstream hoạt động tốt!
            # Tạo generator bọc phần còn lại và đảm bảo gọi cm.__aexit__ ở khối finally
            async def _wrapped_stream() -> AsyncIterator[str]:
                try:
                    if first_chunk.updated_cookies:
                        await self.pool.update_cookies(account.id, first_chunk.updated_cookies)
                    yield first_chunk.text

                    async for chunk in raw_stream:
                        if chunk.updated_cookies:
                            await self.pool.update_cookies(account.id, chunk.updated_cookies)
                        if chunk.text:
                            yield chunk.text

                    await self.pool.record_success(account.id)
                except Exception as mid_stream_err:
                    await self._handle_account_exception(account.id, mid_stream_err)
                    raise
                finally:
                    await cm.__aexit__(None, None, None)

            return account, _wrapped_stream()

        except StopAsyncIteration:
            # Stream rỗng (không có dữ liệu trả về)
            await cm.__aexit__(None, None, None)
            last_error = UpstreamTransientError("Upstream returned an empty response stream.")
            await self.pool.mark_cooldown(account.id, self.settings.transient_cooldown_sec, str(last_error))

        except Exception as err:
            await cm.__aexit__(type(err), err, err.__traceback__)
            last_error = err
            await self._handle_account_exception(account.id, err)

    # Nếu hết số lần retry mà vẫn thất bại
    if isinstance(last_error, MuseRouterError):
        raise last_error
    raise UpstreamTransientError(f"All retry attempts failed. Last error: {last_error}")
```

---

## 4. Đặc tả Chi tiết REST API Endpoints

### 4.1. OpenAI-Compatible API (`/v1/*`)
**Xác thực:** Header `Authorization: Bearer <MUSE_API_KEY>`. Nếu sai hoặc thiếu -> Trả về HTTP `401` chuẩn OpenAI Error.

#### 1. `GET /v1/models`
Trả về danh sách các model được hỗ trợ (bao gồm model gốc và các alias phổ biến để tương thích với các IDE/UI cố định tên model):
```json
{
  "object": "list",
  "data": [
    {"id": "muse-spark", "object": "model", "created": 1735689600, "owned_by": "meta-muse"},
    {"id": "muse-chat", "object": "model", "created": 1735689600, "owned_by": "meta-muse"},
    {"id": "gpt-4o", "object": "model", "created": 1735689600, "owned_by": "system-alias"},
    {"id": "gpt-4o-mini", "object": "model", "created": 1735689600, "owned_by": "system-alias"},
    {"id": "claude-3-5-sonnet-20241022", "object": "model", "created": 1735689600, "owned_by": "system-alias"}
  ]
}
```

#### 2. `POST /v1/chat/completions`
*   **Request Payload (`ChatCompletionRequest`):**
    ```python
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
    ```
*   **Response khi `stream: false`:**
    ```json
    {
      "id": "chatcmpl-9f8a7b6c5d4e3f2a1b",
      "object": "chat.completion",
      "created": 1740000000,
      "model": "muse-spark",
      "choices": [
        {
          "index": 0,
          "message": {
            "role": "assistant",
            "content": "Xin chào! Tôi có thể giúp gì cho bạn?"
          },
          "finish_reason": "stop"
        }
      ],
      "usage": {
        "prompt_tokens": 24,
        "completion_tokens": 12,
        "total_tokens": 36
      },
      "system_fingerprint": "fp_muse_acc_1a2b3c"
    }
    ```
    *(Lưu ý: Trường `system_fingerprint` gắn mã `fp_muse_{account.id}` để dễ dàng debug xem tài khoản nào đã phục vụ request).*

*   **Response khi `stream: true`:**
    *   Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`.
    *   Chunk đầu tiên gửi role:
        `data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1740000000,"model":"muse-spark","system_fingerprint":"fp_muse_acc_01","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}\n\n`
    *   Các chunk nội dung tiếp theo:
        `data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1740000000,"model":"muse-spark","system_fingerprint":"fp_muse_acc_01","choices":[{"index":0,"delta":{"content":"Xin chào"},"finish_reason":null}]}\n\n`
    *   Chunk kết thúc:
        `data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1740000000,"model":"muse-spark","system_fingerprint":"fp_muse_acc_01","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n`
    *   Nếu client truyền `stream_options: {"include_usage": true}`, gửi thêm 1 chunk chứa `usage` với `choices: []` trước khi kết thúc.
    *   Dòng đóng luồng bắt buộc:
        `data: [DONE]\n\n`

#### 3. `POST /v1/completions` (Legacy Completions API)
*   **Request Payload (`CompletionRequest`):**
    *   `model: str`
    *   `prompt: Union[str, list[str]]` (Nếu là `list[str]`, nối lại hoặc lấy phần tử đầu).
    *   `stream: bool = False`
*   **Response (`stream: false`):** `object: "text_completion"`, `choices: [{"text": "...", "index": 0, "logprobs": null, "finish_reason": "stop"}]`.
*   **Response (`stream: true`):** SSE chunks với `object: "text_completion"` và `choices: [{"text": "delta...", "index": 0, "finish_reason": null}]`, kết thúc bằng `data: [DONE]\n\n`.

---

### 4.2. Admin & Pool Management API (`/admin/*`)
**Xác thực:** Header `Authorization: Bearer <MUSE_ADMIN_KEY>`.

1. **`GET /admin/accounts`**
   * Trả về danh sách tất cả tài khoản (Đã che bớt giá trị nhạy cảm của `hatch_sess`, chỉ hiển thị 6 ký tự đầu và 4 ký tự cuối, ví dụ `"eyJhbG...9xYz"`).
   * Thống kê tổng quan: `total`, `ready`, `cooling_down`, `expired`, `disabled`, `active_leases_total`.
2. **`POST /admin/accounts`**
   * Cho phép thêm 1 tài khoản hoặc một mảng tài khoản (`list[AccountCreateRequest]`).
   * Hỗ trợ truyền `cookies` dưới dạng `dict[str, str]` **HOẶC** truyền chuỗi `cookie_header: str` (raw `Cookie` header copy trực tiếp từ Chrome DevTools, hệ thống tự động parse thành dict).
3. **`PATCH /admin/accounts/{account_id}`**
   * Cập nhật `cookies`, `proxy`, `max_concurrent`, `label`, hoặc `status` (`READY` / `DISABLED`).
4. **`POST /admin/accounts/{account_id}/reset`**
   * Đưa tài khoản từ trạng thái `COOLING_DOWN` hoặc `EXPIRED` về lại `READY`, xóa `cooldown_until` và `last_error`.
5. **`DELETE /admin/accounts/{account_id}`**
   * Xóa tài khoản khỏi Pool và cập nhật `data/accounts.json`.

---

## 5. Quản lý Dependencies & Đóng gói (`pyproject.toml` & `Dockerfile`)

### 5.1. `pyproject.toml`
```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "muse-router"
version = "0.1.0"
description = "High-performance OpenAI-compatible API Router for muse.ai with Multi-Account Rotation"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "pydantic>=2.9.0",
    "pydantic-settings>=2.5.0",
    "httpx>=0.27.2",
    "playwright>=1.48.0",
    "tiktoken>=0.8.0",
    "aiofiles>=24.1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "asgi-lifespan>=2.1.0",
]

[project.scripts]
muse-router = "muse_router.main:run"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

### 5.2. `Dockerfile`
Sử dụng base image chính thức của Playwright hoặc Python slim kèm cài đặt Chromium dependencies để chạy `BrowserCDPDriver` ổn định trong container:
```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.48.0-noble

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e .

RUN mkdir -p /app/data

EXPOSE 18610

ENV MUSE_HOST=0.0.0.0
ENV MUSE_PORT=18610
ENV MUSE_DRIVER=browser
ENV MUSE_HEADLESS=true

CMD ["uvicorn", "muse_router.main:app", "--host", "0.0.0.0", "--port", "18610"]
```

---

## 6. Kịch bản Kiểm thử Bắt buộc (Acceptance Tests cho AI Agent)

Sau khi viết xong code, AI Agent cần tạo và chạy bộ test `pytest` tại thư mục `tests/` đảm bảo vượt qua **100%** các kịch bản sau:

1. **`test_pool_rotation_and_concurrency` (`tests/test_pool.py`):**
   * Tạo Pool với 3 tài khoản (`acc_1`, `acc_2`, `acc_3`), `max_concurrent=1`.
   * Kiểm tra chiến lược `round_robin` cấp phát tuần tự `acc_1 -> acc_2 -> acc_3 -> acc_1`.
   * Khi cả 3 tài khoản đều đang giữ lease chưa giải phóng, yêu cầu lease thứ 4 phải ném `NoAvailableAccountError`.
   * Kiểm tra parser tự động tách chuỗi raw `Cookie: hatch_sess=abc; hatch_gw=xyz` thành dict hợp lệ.

2. **`test_first_chunk_failover_streaming` (`tests/test_gateway_failover.py`):**
   * Nạp 2 tài khoản vào Pool: `acc_bad` (cookie `"expired_token"`) và `acc_good` (cookie `"valid_token"`).
   * Gửi request `POST /v1/chat/completions` với `stream: true`.
   * Kỳ vọng:
     * Hệ thống thử `acc_bad` trước -> `MockDriver` ném `AccountAuthExpiredError` trước khi có chunk đầu tiên.
     * `acc_bad` tự động chuyển sang trạng thái `EXPIRED`.
     * Hệ thống tự động failover sang `acc_good` và stream thành công về cho client với HTTP `200 OK` và kết thúc bằng `data: [DONE]`.
     * Tổng số request thành công của `acc_good` tăng lên 1, `active_leases` của cả 2 tài khoản sau khi kết thúc request đều bằng `0`.

3. **`test_openai_endpoints_compatibility` (`tests/test_api_routes.py`):**
   * Kiểm tra `GET /v1/models` trả về đúng schema OpenAI.
   * Kiểm tra `POST /v1/chat/completions` (`stream: false` và `stream: true`).
   * Kiểm tra `POST /v1/completions` (`stream: false` và `stream: true`).
   * Kiểm tra giả lập Tool Calling: Khi gửi `tools` và model trả về `<tool_call>{"name": "get_weather", "arguments": {"city": "Hanoi"}}</tool_call>`, response trả về đúng mảng `tool_calls` và `finish_reason="tool_calls"`.
   * Kiểm tra Admin API: Thêm tài khoản qua `POST /admin/accounts`, xem danh sách qua `GET /admin/accounts`, reset trạng thái qua `POST /admin/accounts/{id}/reset`.

---

## 7. Hướng dẫn Thực thi dành cho AI Coding Agent (Step-by-Step)

Khi nhận được file `SPEC.md` này, hãy thực hiện tuần tự theo 5 bước:

1. **Bước 1:** Khởi tạo cấu trúc thư mục, `pyproject.toml`, `.env.example`, `src/muse_router/config.py`, `src/muse_router/errors.py`, và các hàm tiện ích trong `src/muse_router/utils/`.
2. **Bước 2:** Triển khai `src/muse_router/api/schemas.py` và `src/muse_router/core/pool.py` (kèm tính năng đọc/ghi file atomic và parse raw cookie string).
3. **Bước 3:** Triển khai `src/muse_router/core/prompt.py` (Context Builder + Tool Call XML Parser) và các Driver trong `src/muse_router/drivers/` (`base.py`, `mock.py`, `http_direct.py`, `browser_cdp.py`).
4. **Bước 4:** Triển khai `src/muse_router/core/gateway.py`, `src/muse_router/core/keepalive.py`, toàn bộ các routes trong `src/muse_router/api/routes/` và `src/muse_router/main.py`.
5. **Bước 5:** Viết bộ unit tests trong `tests/` và chạy `pytest -v` để xác nhận tất cả các bài kiểm thử đều đạt (PASS).