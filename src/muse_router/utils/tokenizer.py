"""Token counting: tiktoken cl100k_base when available, else a simple fallback.

The fallback counts whitespace-separated words. It is approximate and only
exists so the API/tests keep working when tiktoken cannot be installed.
"""

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover - exercised when tiktoken is absent
    _ENC = None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    if _ENC is not None:
        return len(_ENC.encode(text))
    return len(text.split())
