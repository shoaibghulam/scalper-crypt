"""What counts as a USDT perpetual symbol.

One rule, in one place. It used to be `[A-Z0-9]{2,25}USDT`, copied into ten
modules, and it refused 18 of the 528 symbols Binance actually lists: the
single-character bases (A, B, C, F, T, W, 4 and friends) and the meme listings
whose base is written in Chinese. Because one rejected entry refuses an entire
list, "select all coins" could never start a run.

The property that matters is not the alphabet. A symbol becomes a directory
name in the candle store, so it must never contain a path separator, a dot or
whitespace. `[^\W_]` gives exactly that: unicode letters and digits, no
underscore, no separators, nothing that can climb out of a directory.
"""

import re

#: Usable as a Pydantic `pattern=`, which cannot call a function.
SYMBOL_PATTERN = r"^[^\W_]{1,25}USDT$"

_SYMBOL = re.compile(SYMBOL_PATTERN)


def is_symbol(value: str) -> bool:
    """Whether this is a USDT perpetual symbol safe to use as a path segment."""
    return bool(_SYMBOL.fullmatch(value))


def clean_symbols(values: list[str]) -> list[str]:
    """Trim and upper-case a selection, dropping duplicates but keeping order.

    Upper-casing leaves a non-ASCII base untouched, which is what we want: the
    exchange lists it exactly as written.
    """
    return list(dict.fromkeys(value.strip().upper() for value in values))
