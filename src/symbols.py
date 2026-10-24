"""NSE cash symbol parsing. This simulator is India / NSE only."""

from __future__ import annotations

import re

# NSE cash tickers: RELIANCE, SBIN, M&M, BAJAJ-AUTO, CUPID.
_NSE_BODY = re.compile(r"^[A-Z][A-Z0-9&-]{0,20}$")


def parse_symbols(text: str) -> list[str]:
    """Split a user string into NSE cash symbols.

    ``SBIN.NS`` is accepted and stored as ``SBIN`` (Yahoo's ``.NS`` suffix is
    added by the data layer). Other exchange suffixes are rejected.
    """
    raw = (text or "").replace(";", ",").replace("\n", ",").replace(" ", ",")
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        symbol = part.strip().upper()
        if not symbol:
            continue
        if symbol.endswith(".NS"):
            symbol = symbol[: -len(".NS")]
        if "." in symbol:
            raise ValueError(
                f"{part.strip()} is not an NSE cash symbol. "
                "Use names such as CUPID or SBIN,VEDL (Yahoo .NS only)."
            )
        if not _NSE_BODY.fullmatch(symbol):
            raise ValueError(f"Invalid NSE symbol: {part.strip()}")
        if symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


def to_yahoo_nse(text: str) -> str:
    """One NSE symbol with the Yahoo ``.NS`` suffix, for TradingAgents."""
    symbols = parse_symbols(text)
    if len(symbols) != 1:
        raise ValueError("Enter one NSE symbol, for example SBIN or CUPID.")
    return f"{symbols[0]}.NS"
