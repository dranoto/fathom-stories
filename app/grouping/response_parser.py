import json
import re
from typing import Any, Dict


_THINK_BLOCK_RE = re.compile(
    r"<think(?:ing)?>.*?</think(?:ing)?>",
    re.DOTALL | re.IGNORECASE,
)
_THINK_OPEN_RE = re.compile(r"<think(?:ing)?>", re.IGNORECASE)


def parse_json_object(content: str) -> Dict[str, Any]:
    cleaned = _THINK_BLOCK_RE.sub("", content).strip()
    if _THINK_OPEN_RE.search(cleaned):
        raise ValueError("Unclosed thinking block in model response")

    decoder = json.JSONDecoder()
    index = 0
    while index < len(cleaned):
        if cleaned[index] not in "{[":
            index += 1
            continue
        try:
            value, consumed = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            index += 1
            continue
        if isinstance(value, dict):
            return value
        index += consumed

    raise ValueError("No JSON object found in model response")
