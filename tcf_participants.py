"""Parse TCF chat participant text into CWSU and organization codes.

This is a Python port of the standalone ``tcf_participants.html`` utility used
by operations.  It deliberately has no Streamlit dependency so the parser can
be tested independently and reused by other TCF tooling.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


CWSU = {
    "ZAB": "Albuquerque",
    "ZAU": "Chicago",
    "ZBW": "Boston",
    "ZDC": "Washington",
    "ZDV": "Denver",
    "ZFW": "Fort Worth",
    "ZHU": "Houston",
    "ZID": "Indianapolis",
    "ZJX": "Jacksonville",
    "ZKC": "Kansas City",
    "ZLA": "Los Angeles",
    "ZLC": "Salt Lake City",
    "ZMA": "Miami",
    "ZME": "Memphis",
    "ZMP": "Minneapolis",
    "ZNY": "New York",
    "ZOA": "Oakland",
    "ZOB": "Cleveland",
    "ZSE": "Seattle",
    "ZTL": "Atlanta",
}

ORGANIZATIONS = {
    "AAL": "American Airlines",
    "AWC": "Aviation Weather Center",
    "CMAC": "CMAC",
    "Delta": "Delta Air Lines",
    "FedEx": "FedEx",
    "JetBlue": "JetBlue",
    "MSC": "Meteorological Service of Canada",
    "NAM": "NWS - AWC NAM Forecaster",
    "SWA": "Southwest",
    "UAL": "United Airlines",
}

BASE_ALIASES = {
    "abq": "ZAB",
    "ord": "ZAU",
    "bos": "ZBW",
    "dc": "ZDC",
    "den": "ZDV",
    "dfw": "ZFW",
    "iah": "ZHU",
    "indy": "ZID",
    "jax": "ZJX",
    "kc": "ZKC",
    "slc": "ZLC",
    "mia": "ZMA",
    "mpls": "ZMP",
    "nyc": "ZNY",
    "los angeles": "ZLA",
    "la cwsu": "ZLA",
    "lax": "ZLA",
    "nws - awc": "AWC",
    "awc": "AWC",
    "nws - awc - wes adkins": "NAM",
    "nws - awc - joe carr": "NAM",
    "nws - awc - ken widelski": "NAM",
    "nws - awc - kyle struckmann": "NAM",
    "awc - wes adkins": "NAM",
    "awc - joe carr": "NAM",
    "awc - ken widelski": "NAM",
    "awc - kyle struckmann": "NAM",
    "wes adkins": "NAM",
    "joe carr": "NAM",
    "ken widelski": "NAM",
    "kyle struckmann": "NAM",
    "av - meteorological service of canada": "MSC",
    "msc": "MSC",
    "av - fedex": "FedEx",
    "fedex": "FedEx",
    "av - jetblue": "JetBlue",
    "jetblue": "JetBlue",
    "av - atl delta air lines": "Delta",
    "delta": "Delta",
    "southwest": "SWA",
    "swa": "SWA",
    "av - cmac": "CMAC",
    "cmac": "CMAC",
    "united airlines": "UAL",
    "ual": "UAL",
    "american airlines": "AAL",
    "aal": "AAL",
}

CODE_RE = re.compile(r"\bZ[A-Z]{2}\b")
SPLIT_RE = re.compile(r"[\n,;|/\\()\[\]{}<>•\-–—]+")


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def build_aliases(include_orgs: bool = True, strict_short: bool = True) -> dict[str, str]:
    aliases = dict(BASE_ALIASES)
    if not include_orgs:
        for key in (
            "nws - awc", "awc",
            "av - meteorological service of canada", "msc",
            "av - fedex", "fedex",
            "av - jetblue", "jetblue",
            "av - atl delta air lines", "delta",
            "southwest", "swa",
        ):
            aliases.pop(key, None)
    if strict_short:
        for key in ("kc", "dc", "mia"):
            aliases.pop(key, None)
    return aliases


def _city_match(token: str) -> str | None:
    normalized = _norm(token)
    if "cwsu" not in normalized:
        return None

    city_to_code = {name.lower(): code for code, name in CWSU.items()}
    cities = sorted(city_to_code, key=len, reverse=True)
    for city in cities:
        if _norm(city) in normalized:
            return city_to_code[city]

    match = re.search(r"cwsu\s+([a-z0-9 ]{2,})", normalized)
    if match:
        guess = _norm(match.group(1))
        for city in cities:
            if _norm(city) in guess:
                return city_to_code[city]
    return None


def _normalize_to_code(token: str, aliases: dict[str, str]) -> str | None:
    direct = next((code for code in CODE_RE.findall(token.upper()) if code in CWSU), None)
    if direct:
        return direct

    normalized = _norm(token)
    for key in sorted(aliases, key=len, reverse=True):
        if _norm(key) in normalized:
            return aliases[key]

    return _city_match(token)


@dataclass(frozen=True)
class ParticipantParseResult:
    codes: tuple[str, ...]
    cwsus: tuple[str, ...]
    organizations: tuple[str, ...]
    unknown: tuple[str, ...]


def parse_participants(raw: str, *, include_orgs: bool = True,
                       strict_short: bool = True) -> ParticipantParseResult:
    """Parse pasted participant text using the standalone converter's rules."""
    aliases = build_aliases(include_orgs=include_orgs, strict_short=strict_short)
    found: set[str] = set()
    unknown: set[str] = set()

    for code in CODE_RE.findall((raw or "").upper()):
        if code in CWSU:
            found.add(code)

    for chunk in SPLIT_RE.split(raw or ""):
        token = chunk.strip()
        if not token:
            continue
        code = _normalize_to_code(token, aliases)
        if code:
            found.add(code)
        elif len(token) > 2:
            unknown.add(token)

    codes = tuple(sorted(found))
    cwsus = tuple(code for code in codes if code in CWSU)
    organizations = tuple(code for code in codes if code in ORGANIZATIONS)
    return ParticipantParseResult(
        codes=codes,
        cwsus=cwsus,
        organizations=organizations,
        unknown=tuple(sorted(unknown)),
    )
