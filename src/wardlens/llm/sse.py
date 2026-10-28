"""UTF-8 SSE framing, independent of Requests' text/* charset heuristics."""

from __future__ import annotations

import codecs
from collections.abc import Iterable, Iterator


class SSEProtocolError(ValueError):
    pass


def _lines(chunks: Iterable[bytes], maximum: int) -> Iterator[str]:
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    line: list[str] = []
    skip_lf = False
    for chunk in chunks:
        if not isinstance(chunk, bytes):
            raise SSEProtocolError("SSE reader requires bytes")
        for character in decoder.decode(chunk):
            if skip_lf:
                skip_lf = False
                if character == "\n":
                    continue
            if character in {"\r", "\n"}:
                yield "".join(line)
                line.clear()
                skip_lf = character == "\r"
            else:
                line.append(character)
                if len(line) > maximum:
                    raise SSEProtocolError("SSE line exceeds limit")
    decoder.decode(b"", final=True)  # Reject a truncated multibyte sequence.
    # SSE does not dispatch an unterminated line/event on EOF.


def iter_sse_data(chunks: Iterable[bytes], *, maximum: int = 2_000_000) -> Iterator[str]:
    """Yield complete data events; ignore comments/fields, not corrupt JSON.

    Only CR/LF delimit lines. str.splitlines() additionally splits U+0085,
    U+2028, etc., which are valid characters inside a JSON string.
    """
    data: list[str] = []
    size = 0
    for line in _lines(chunks, maximum):
        if not line:
            if data:
                yield "\n".join(data)
            data.clear()
            size = 0
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if field != "data":
            continue
        if separator and value.startswith(" "):
            value = value[1:]
        size += len(value) + 1
        if size > maximum:
            raise SSEProtocolError("SSE event exceeds limit")
        data.append(value)
