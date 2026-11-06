"""SSE parser, including the worker delta shape and split chunks."""

from relay.sse import extract_delta_text, parse_sse_events


def _chunks(blob: bytes, size: int):
    for start in range(0, len(blob), size):
        yield blob[start : start + size]


def test_worker_delta_format_and_openai_chunks():
    raw = (
        b": keep-alive\n"
        b"event: delta\n"
        b'data: {"id":"c1","choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}\n\n'
        b'data: {"delta":"\xe4\xbd\xa0"}\r\n'
        b"\n"
        b'data: {"type":"delta","delta":{"content":"\xe5\xa5\xbd"}}\n'
        b'data: {"choices":[{"index":0,"delta":{"content":"\xe5\x91\x80"}}]}\n'
        b'data: {"choices":[{"delta":{"content":[{"type":"text","text":"!"}]}}]}\n'
        b"data: {not-json}\n"
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n'
        b"data: [DONE]\n"
        b'data: {"delta":"ignored"}\n'
    )
    assert list(parse_sse_events(_chunks(raw, 1))) == ["你", "好", "呀", "!"]
    assert list(parse_sse_events([raw])) == ["你", "好", "呀", "!"]


def test_openai_stream_split_mid_line():
    raw = (
        'data: {"choices":[{"delta":{"content":"Hello"}}]}\n'
        'data: {"choices":[{"delta":{"content":" there"}}]}\n'
        "data: [DONE]\n"
    ).encode("utf-8")
    # Split inside the JSON and inside the UTF-8-free ASCII stream.
    assert list(parse_sse_events(_chunks(raw, 7))) == ["Hello", " there"]


def test_extract_delta_helpers():
    assert extract_delta_text({"delta": "yo"}) == "yo"
    assert extract_delta_text({"choices": [{"text": "legacy"}]}) == "legacy"
    assert extract_delta_text({"response": "plain"}) == "plain"
    assert extract_delta_text({"choices": [{"delta": {"content": ""}}]}) is None
    assert extract_delta_text(["nope"]) is None


def test_done_without_trailing_newline():
    raw = b'data: {"content":"x"}\ndata: [DONE]'
    assert list(parse_sse_events([raw])) == ["x"]
