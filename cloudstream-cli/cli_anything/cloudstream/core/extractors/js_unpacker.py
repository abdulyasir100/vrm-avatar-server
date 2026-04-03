"""JavaScript Unpacker — ported from CloudStream's JsUnpacker.kt.

Handles obfuscated JS packed with Dean Edwards' packer format:
  function(p,a,c,k,e,d){...}

Used by FileMoon, StreamWish, Mp4Upload, and many other extractors.
"""

import re
import string


def _base_n(num: int, radix: int) -> str:
    """Convert number to string in given radix (up to 62)."""
    if radix <= 36:
        digits = string.digits + string.ascii_lowercase
    else:
        digits = string.digits + string.ascii_lowercase + string.ascii_uppercase

    if num == 0:
        return "0"

    result = []
    while num > 0:
        result.append(digits[num % radix])
        num //= radix
    return "".join(reversed(result))


def is_packed(text: str) -> bool:
    """Check if text contains packed JavaScript."""
    return bool(re.search(r"function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,", text))


def find_packed(text: str) -> str | None:
    """Extract the packed JS block from text."""
    match = re.search(
        r"function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*[rd]\s*\)\s*\{.*?\}\s*\("
        r"'(.*?)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'(.*?)'\s*\.\s*split\s*\(\s*'\|'\s*\)",
        text,
        re.DOTALL,
    )
    if match:
        return match.group(0)
    return None


def unpack(packed: str) -> str | None:
    """Unpack Dean Edwards packed JavaScript.

    Args:
        packed: The packed function(p,a,c,k,e,d) string.

    Returns:
        Unpacked JavaScript source, or None if unpacking fails.
    """
    match = re.search(
        r"\}\s*\(\s*'(.*?)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'(.*?)'\s*\.\s*split\s*\(\s*'\|'\s*\)",
        packed,
        re.DOTALL,
    )
    if not match:
        return None

    payload = match.group(1)
    radix = int(match.group(2))
    count = int(match.group(3))
    keywords = match.group(4).split("|")

    if radix <= 1:
        radix = 62

    def replacer(m: re.Match) -> str:
        word = m.group(0)
        try:
            index = int(word, radix) if radix <= 36 else _unbaser(word, radix)
        except (ValueError, IndexError):
            return word
        if 0 <= index < len(keywords) and keywords[index]:
            return keywords[index]
        return word

    result = re.sub(r"\b[a-zA-Z0-9]+\b", replacer, payload)
    return result


def _unbaser(word: str, radix: int) -> int:
    """Convert a word from a custom base to integer."""
    digits = string.digits + string.ascii_lowercase + string.ascii_uppercase
    if radix > len(digits):
        digits += "".join(chr(i) for i in range(161, 161 + radix - len(digits)))

    result = 0
    for char in word:
        idx = digits.index(char) if char in digits else -1
        if idx < 0:
            raise ValueError(f"Invalid character '{char}' for base {radix}")
        result = result * radix + idx
    return result


def get_and_unpack(html: str) -> str:
    """Find packed JS in HTML and unpack it, or return original."""
    packed = find_packed(html)
    if packed:
        unpacked = unpack(packed)
        if unpacked:
            return unpacked
    return html
