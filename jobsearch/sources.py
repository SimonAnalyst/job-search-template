"""Adaptery publicznych tablic pracy; opisy są używane tylko podczas dopasowania."""
from __future__ import annotations
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

TRACKING_KEYS = {"ref", "source", "campaign", "gh_src"}
CURRENCY = r"(?:PLN|zł(?:oty|ote|otych)?|EUR|€|USD|\$|GBP|£|CHF|CZK|SEK|NOK|DKK|HUF|RON)"
AMOUNT = r"(?:\d{1,3}(?:[ \u00a0.,]\d{3})+|\d+)(?:[,.]\d{1,2})?(?:\s*(?:tys\.?|[kKmM]))?"
RANGE = r"(?:\s*(?:-|–|—|to)\s*(?:" + CURRENCY + r"\s*)?" + AMOUNT + r")?"
QUALIFIER = r"(?:\s*(?:netto|brutto))?"
RATE = r"(?:\s*(?:/|per\s+)(?:h|hr|hour|hours|m|mo|month|months|y|yr|year|years|godz(?:\.|in(?:ę|a|y)?)?|mies(?:\.|iąc(?:e|y)?))(?![A-Za-z]))?"
SALARY_PATTERNS = (
    re.compile(r"(?:" + CURRENCY + r"\s*)" + AMOUNT + RANGE + QUALIFIER + RATE, re.IGNORECASE),
    re.compile(r"(?:od\s+|from\s+)?" + AMOUNT + RANGE + r"\s*" + CURRENCY + QUALIFIER + RATE, re.IGNORECASE),
    re.compile(r"(?:od\s+|from\s+)?" + AMOUNT + RANGE + r"\s+(?:netto|brutto)(?:\s*(?:/|per\s+)(?:h|hr|hour|hours|godz(?:\.|in(?:ę|a|y)?))(?![A-Za-z]))?", re.IGNORECASE),
)

def _redact_salary(title: str) -> str:
    for pattern in SALARY_PATTERNS:
        title = pattern.sub("[wynagrodzenie pominięte]", title)
    return " ".join(title.split())

SENSITIVE_QUERY = re.compile(r"(salary|wage|pay|rate|compensation)", re.IGNORECASE)

class SourceError(Exception):
    """Błąd źródła bez danych żądania mogących zawierać sekrety."""

@dataclass
class SourceBatch:
    name: str
    offers: list[dict[str, str]]
    error: str | None = None

def clean_url(value: Any, *, allow_http: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        allowed = {"https", "http"} if allow_http else {"https"}
        if parsed.scheme.lower() not in allowed or not parsed.hostname or parsed.username or parsed.password:
            return None
        host = parsed.hostname.lower()
        netloc = f"{host}:{parsed.port}" if parsed.port and parsed.port != (443 if parsed.scheme.lower() == "https" else 80) else host
        query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                 if k.casefold() not in TRACKING_KEYS and not k.casefold().startswith("utm_")
                 and not SENSITIVE_QUERY.search(k)]
        return urllib.parse.urlunsplit((parsed.scheme.lower(), netloc, parsed.path, urllib.parse.urlencode(query), ""))
    except (ValueError, UnicodeError):
        return None

def _stable_id(source: str, board: str, raw_id: Any, url: str) -> str:
    if raw_id not in (None, ""):
        return f"{source}:{board}:{str(raw_id)[:160]}"
    return f"{source}:{board}:url:{hashlib.sha256(url.encode('utf-8')).hexdigest()}"

def _normalized(source: str, board: str, *, raw_id: Any, title: Any, company: Any,
                location: Any, url: Any, match_text: Any = "", allow_http: bool = False) -> dict[str, str] | None:
    safe_url = clean_url(url, allow_http=allow_http)
    title = _redact_salary(" ".join(str(title or "").split()))
    if not safe_url or not title:
        return None
    company = " ".join(str(company or "").split())
    location = " ".join(str(location or "").split())
    return {"id": _stable_id(source, board, raw_id, safe_url), "title": title[:240],
            "company": company[:200], "location": location[:200], "url": safe_url,
            "source": source, "_match_text": " ".join((title, company, location, str(match_text or ""))).casefold()}

def _request_json(url: str, timeout: int) -> Any:
    request = urllib.request.Request(url, headers={"Accept":"application/json","User-Agent":"JobSearchStarter/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(4_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SourceError(f"odpowiedź HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SourceError("brak połączenia lub przekroczony limit czasu") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SourceError("źródło zwróciło nieprawidłowe dane") from None

def _slug(value: Any) -> str:
    slug = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", slug):
        raise SourceError("nieprawidłowy identyfikator tablicy")
    return slug

def _location_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("display_name") or value.get("name") or "")
    if isinstance(value, list):
        return ", ".join(filter(None, (_location_name(item) for item in value)))
    return str(value or "")

def _finish_batch(name: str, offers: dict[str, dict[str, str]],
                  failures: list[str], succeeded: int) -> SourceBatch:
    if succeeded == 0:
        detail = "; ".join(failures[:4]) or "brak poprawnych wyników"
        raise SourceError(detail[:500])
    error = "częściowa awaria: " + "; ".join(failures[:4]) if failures else None
    return SourceBatch(name, list(offers.values()), error)

def fetch_adzuna(*, country: str, keywords: list[str], location: str, pages: int,
                 timeout: int, app_id: str, app_key: str) -> SourceBatch:
    if not app_id or not app_key:
        raise SourceError("brak wymaganych kluczy API")
    by_id: dict[str, dict[str, str]] = {}
    failures: list[str] = []
    succeeded = 0
    for word_index, keyword in enumerate(keywords, start=1):
        for page in range(1, pages + 1):
            query = {"app_id":app_id,"app_key":app_key,"results_per_page":"50",
                     "content-type":"application/json","what":keyword}
            if location:
                query["where"] = location
            url = f"https://api.adzuna.com/v1/api/jobs/{urllib.parse.quote(country)}/search/{page}?{urllib.parse.urlencode(query)}"
            try:
                payload = _request_json(url, timeout)
                if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                    raise SourceError("nieprawidłowa odpowiedź API")
                items = payload["results"]
                if not isinstance(items, list):
                    raise SourceError("nieprawidłowa lista ofert")
                succeeded += 1
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    company = item.get("company") or {}
                    row = _normalized("adzuna", country, raw_id=item.get("id"), title=item.get("title"),
                        company=company.get("display_name", "") if isinstance(company, dict) else "",
                        location=_location_name(item.get("location")), url=item.get("redirect_url"),
                        match_text=item.get("description"), allow_http=True)
                    if row:
                        by_id[row["id"]] = row
            except SourceError as exc:
                failures.append(f"hasło {word_index}, strona {page}: {exc}")
    return _finish_batch("adzuna", by_id, failures, succeeded)

def fetch_lever(*, boards: list[str], timeout: int, region: str = "global") -> SourceBatch:
    region = region.strip().casefold()
    if region not in {"global", "eu"}:
        raise SourceError("region Lever musi mieć wartość global albo eu")
    api_host = "api.eu.lever.co" if region == "eu" else "api.lever.co"
    by_id: dict[str, dict[str, str]] = {}
    failures: list[str] = []
    succeeded = 0
    for index, raw_board in enumerate(boards, start=1):
        try:
            board = _slug(raw_board)
            payload = _request_json(f"https://{api_host}/v0/postings/{urllib.parse.quote(board)}?mode=json", timeout)
            if not isinstance(payload, list):
                raise SourceError("nieprawidłowa lista ofert")
            succeeded += 1
            for item in payload:
                if not isinstance(item, dict):
                    continue
                cats = item.get("categories") or {}
                row = _normalized("lever", board, raw_id=item.get("id"), title=item.get("text"),
                    company=board, location=cats.get("location", "") if isinstance(cats, dict) else "",
                    url=item.get("hostedUrl"), match_text=item.get("descriptionPlain") or item.get("description"))
                if row:
                    by_id[row["id"]] = row
        except SourceError as exc:
            failures.append(f"tablica {index}: {exc}")
    return _finish_batch("lever", by_id, failures, succeeded)

def fetch_greenhouse(*, boards: list[str], timeout: int) -> SourceBatch:
    by_id: dict[str, dict[str, str]] = {}
    failures: list[str] = []
    succeeded = 0
    for index, raw_board in enumerate(boards, start=1):
        try:
            board = _slug(raw_board)
            url = f"https://boards-api.greenhouse.io/v1/boards/{urllib.parse.quote(board)}/jobs?content=true"
            payload = _request_json(url, timeout)
            if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
                raise SourceError("nieprawidłowa odpowiedź API")
            items = payload["jobs"]
            if not isinstance(items, list):
                raise SourceError("nieprawidłowa lista ofert")
            succeeded += 1
            for item in items:
                if not isinstance(item, dict):
                    continue
                loc = item.get("location") or {}
                row = _normalized("greenhouse", board, raw_id=item.get("id"), title=item.get("title"),
                    company=board, location=loc.get("name", "") if isinstance(loc, dict) else "",
                    url=item.get("absolute_url"), match_text=item.get("content"))
                if row:
                    by_id[row["id"]] = row
        except SourceError as exc:
            failures.append(f"tablica {index}: {exc}")
    return _finish_batch("greenhouse", by_id, failures, succeeded)
