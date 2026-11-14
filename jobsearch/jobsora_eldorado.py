"""Adaptery Jobsora i oficjalnego MCP Czy Jest Eldorado."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from . import dynamic_boards, sources


_USER_AGENT = "JobSearchStarter/0.1"
_JOBSORA_ORIGIN = "https://pl.jobsora.com"
_ELDORADO_ORIGIN = "https://czyjesteldorado.pl"
_ELDORADO_MCP = _ELDORADO_ORIGIN + "/_mcp"
_PROTOCOL_VERSION = "2024-11-05"
_MAX_BODY = 4_000_000
_MAX_MCP_BODY = 5_000_000
_MAX_PAGES = 10
_VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
_NO_RESULTS = ("nie znaleźliśmy ofert", "brak ofert pracy", "nie znaleziono ofert", "no jobs found", "no results found")
_CHALLENGE_TEXT_MARKERS = ("verify you are human", "access denied", "request blocked", "cloudflare ray id", "unusual traffic", "complete the captcha", "solve the captcha", "confirm you are not a robot")


def _validate_inputs(keywords: list[str], pages: int, timeout: int) -> tuple[list[str], int, int]:
    if not isinstance(keywords, list):
        raise sources.SourceError("hasła wyszukiwania muszą być listą")
    clean_keywords = [str(item).strip() for item in keywords if str(item).strip()]
    if not clean_keywords:
        raise sources.SourceError("wpisz co najmniej jedno hasło wyszukiwania")
    try:
        page_limit = int(pages)
        timeout_seconds = int(timeout)
    except (TypeError, ValueError):
        raise sources.SourceError("nieprawidłowy limit stron lub czasu") from None
    if isinstance(pages, bool) or isinstance(timeout, bool) or not (1 <= page_limit <= _MAX_PAGES) or timeout_seconds < 1:
        raise sources.SourceError("nieprawidłowy limit stron lub czasu")
    return clean_keywords, page_limit, timeout_seconds


def _append_warnings(batch: sources.SourceBatch, warnings: list[str]) -> sources.SourceBatch:
    if warnings:
        text = "ostrzeżenie: " + "; ".join(list(dict.fromkeys(warnings))[:4])
        batch.error = f"{batch.error}; {text}" if batch.error else text
    return batch


def _safe_display_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("value") or value.get("text") or value.get("display_name") or ""
    if isinstance(value, list):
        value = " ".join(filter(None, (_safe_display_text(item) for item in value)))
    return sources._redact_salary(" ".join(str(value or "").split()))


def _is_terminal_source_error(error: Exception) -> bool:
    message = str(error).casefold()
    return any(marker in message for marker in (
        "http 403", "http 429", "blokad", "weryfikac", "robots.txt",
        "przekierowanie zablokowane", "nieobsługiwany html",
    ))


def _is_jobsora_404(error: Exception) -> bool:
    return re.search(r"\bhttp\s+404\b", str(error), re.IGNORECASE) is not None

def _path_slug(value: str) -> str:
    text = re.sub(r"\s+", "-", str(value or "").strip().casefold())
    text = re.sub(r"[^\w-]", "-", text, flags=re.UNICODE)
    text = re.sub(r"-+", "-", text).strip("-_")
    if not text or len(text) > 160:
        raise sources.SourceError("hasło lub lokalizacja ma nieprawidłowy format")
    return urllib.parse.quote(text, safe="-")


def _jobsora_search_url(keyword: str, location: str) -> str:
    keyword_slug = _path_slug(keyword)
    location_slug = _path_slug(location) if str(location or "").strip() else ""
    prefix = f"/praca/{location_slug}/" if location_slug else "/praca/"
    return _JOBSORA_ORIGIN + prefix + "q-" + keyword_slug


def _safe_jobsora_listing_url(value: str, page_url: str = _JOBSORA_ORIGIN) -> str | None:
    try:
        absolute = urllib.parse.urljoin(page_url, str(value or ""))
        parsed = urllib.parse.urlsplit(absolute)
        if (parsed.scheme.casefold() != "https" or parsed.hostname != "pl.jobsora.com"
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.fragment):
            return None
        path = urllib.parse.unquote(parsed.path)
        if path != "/praca" and not path.startswith("/praca/"):
            return None
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if query and (len(query) != 1 or query[0][0] != "start" or not query[0][1].isdigit()):
            return None
        return urllib.parse.urlunsplit(("https", "pl.jobsora.com", parsed.path,
                                        urllib.parse.urlencode(query), ""))
    except (TypeError, ValueError, UnicodeError):
        return None


def _jobsora_offer_url(value: str, page_url: str) -> str | None:
    try:
        absolute = urllib.parse.urljoin(page_url, str(value or ""))
        parsed = urllib.parse.urlsplit(absolute)
        if (parsed.scheme.casefold() != "https" or parsed.hostname != "pl.jobsora.com"
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            return None
        path = urllib.parse.unquote(parsed.path).rstrip("/")
        match = re.fullmatch(r"/wakat-(\d+)", path)
        if not match:
            return None
        canonical_path = "/wakat-" + match.group(1)
        return urllib.parse.urlunsplit(("https", "pl.jobsora.com", canonical_path, "", ""))
    except (TypeError, ValueError, UnicodeError):
        return None


def _jobsora_offer_id(value: str) -> str | None:
    try:
        path = urllib.parse.unquote(urllib.parse.urlsplit(value).path).rstrip("/")
    except (TypeError, ValueError, UnicodeError):
        return None
    match = re.fullmatch(r"/wakat-(\d+)", path)
    return match.group(1) if match else None


def _jobsora_next_start_url(value: str, page_url: str) -> str | None:
    candidate = _safe_jobsora_listing_url(value, page_url)
    if not candidate:
        return None
    candidate_query = urllib.parse.parse_qsl(urllib.parse.urlsplit(candidate).query, keep_blank_values=True)
    current_query = urllib.parse.parse_qsl(urllib.parse.urlsplit(page_url).query, keep_blank_values=True)
    candidate_start = next((int(value) for key, value in candidate_query if key == "start" and value.isdigit()), None)
    current_start = next((int(value) for key, value in current_query if key == "start" and value.isdigit()), 0)
    return candidate if candidate_start is not None and candidate_start > current_start else None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _jobsora_robots_allows(url: str, timeout: int) -> bool:
    rules = dynamic_boards._robots_for(url, timeout)
    return dynamic_boards._robots_can_fetch(rules, url, _USER_AGENT)


def _fetch_jobsora_html(url: str, timeout: int) -> str:
    safe_url = _safe_jobsora_listing_url(url)
    if not safe_url or safe_url != url:
        raise sources.SourceError("nieprawidłowy adres wyszukiwania Jobsora")
    if not _jobsora_robots_allows(safe_url, timeout):
        raise sources.SourceError("robots.txt zabrania pobrania tej ścieżki")
    request = urllib.request.Request(
        safe_url,
        headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": _USER_AGENT},
    )
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise sources.SourceError(f"odpowiedź HTTP {response.status}")
            final_url = _safe_jobsora_listing_url(response.geturl())
            if final_url != safe_url:
                raise sources.SourceError("przekierowanie Jobsora zostało zablokowane")
            charset = response.headers.get_content_charset() or "utf-8"
            page = response.read(_MAX_BODY).decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        suffix = " (przekierowanie zablokowane)" if 300 <= exc.code < 400 else ""
        raise sources.SourceError(f"odpowiedź HTTP {exc.code}{suffix}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise sources.SourceError("brak połączenia lub przekroczony limit czasu") from None
    return page


class _JobsoraParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.offers: list[dict[str, str]] = []
        self.next_url: str | None = None
        self._stack: list[str] = []
        self._card: dict[str, str] | None = None
        self._card_tag = ""
        self._card_depth = -1
        self._captures: list[dict[str, Any]] = []
        self.visible_text: list[str] = []
        self._ignored_tags: list[str] = []

    def _start_capture(self, field: str, tag: str) -> None:
        if self._card is not None:
            self._captures.append({"field": field, "tag": tag, "depth": len(self._stack), "parts": []})

    def _finish_capture(self, capture: dict[str, Any]) -> None:
        value = _safe_display_text(" ".join(capture["parts"]))
        field = capture["field"]
        if self._card is not None and value:
            if field == "anchor_title":
                if not self._card.get("title"):
                    self._card["title"] = value
            elif not self._card.get(field):
                self._card[field] = value

    def _finish_card(self) -> None:
        card = self._card
        self._card = None
        self._card_tag = ""
        self._card_depth = -1
        if not card:
            return
        row = sources._normalized(
            "jobsora", "public", raw_id=_jobsora_offer_id(card.get("url", "")),
            title=card.get("title"), company=card.get("company"), location=card.get("location"),
            url=card.get("url"), match_text="",
        )
        if row:
            self.offers.append(row)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() in {"script", "style"}:
            self._ignored_tags.append(tag.casefold())
        classes = values.get("class", "").split()
        lowered = {item.casefold() for item in classes}
        if tag.casefold() == "a" and values.get("href") and self.next_url is None:
            rel = {item.casefold() for item in values.get("rel", "").split()}
            label = (values.get("aria-label", "") + " " + values.get("class", "")).casefold()
            if "next" in rel or "pagination__next" in label or "next-page" in label:
                self.next_url = _safe_jobsora_listing_url(values["href"], self.page_url)
            else:
                self.next_url = _jobsora_next_start_url(values["href"], self.page_url)
        card_root = bool(lowered.intersection({"c-job-card", "js-vacancy-snippet"}))
        if card_root:
            if self._card is not None:
                self._finish_card()
            self._card = {"title": "", "company": "", "location": "", "url": ""}
            card_url = _jobsora_offer_url(values.get("data-href", ""), self.page_url)
            if card_url:
                self._card["url"] = card_url
            self._card_tag = tag.casefold()
            self._card_depth = len(self._stack)

        if self._card is not None:
            if tag.casefold() == "a" and values.get("href"):
                offer_url = _jobsora_offer_url(values["href"], self.page_url)
                if offer_url:
                    if not self._card.get("url") or "c-job-title" in lowered:
                        self._card["url"] = offer_url
                    self._start_capture("anchor_title", tag.casefold())
            if lowered.intersection({"c-job-title", "c-job-title__text", "job-title"}):
                self._start_capture("title", tag.casefold())
            elif tag.casefold() in {"h1", "h2", "h3"} and not self._card.get("title"):
                self._start_capture("title", tag.casefold())
            if lowered.intersection({"c-job-company__name", "company-name"}):
                self._start_capture("company", tag.casefold())
            if lowered.intersection({"c-job-location__city", "c-job-location__value", "job-location"}):
                self._start_capture("location", tag.casefold())

        if tag.casefold() not in _VOID_TAGS:
            self._stack.append(tag.casefold())

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"script", "style"}:
            for index in range(len(self._ignored_tags) - 1, -1, -1):
                if self._ignored_tags[index] == tag:
                    del self._ignored_tags[index]
                    break
        closing_depth = next((index for index in range(len(self._stack) - 1, -1, -1)
                              if self._stack[index] == tag), None)
        if closing_depth is None:
            return
        remaining: list[dict[str, Any]] = []
        for capture in self._captures:
            if capture["depth"] == closing_depth and capture["tag"] == tag:
                self._finish_capture(capture)
            elif capture["depth"] >= closing_depth:
                continue
            else:
                remaining.append(capture)
        self._captures = remaining
        if self._card is not None and closing_depth == self._card_depth and tag == self._card_tag:
            self._finish_card()
        del self._stack[closing_depth:]

    def handle_data(self, data: str) -> None:
        if self._ignored_tags:
            return
        self.visible_text.append(data)
        if data.strip():
            for capture in self._captures:
                capture["parts"].append(data)
    def close(self) -> None:
        super().close()
        if self._card is not None:
            self._finish_card()


def _parse_jobsora_page(page_html: str, page_url: str) -> tuple[list[dict[str, str]], str | None]:
    parser = _JobsoraParser(page_url)
    parser.feed(page_html)
    parser.close()
    offers: dict[str, dict[str, str]] = {row["id"]: row for row in parser.offers}
    visible_text = " ".join(parser.visible_text).casefold()
    if not offers and any(marker in visible_text for marker in _CHALLENGE_TEXT_MARKERS):
        raise sources.SourceError("strona zgłosiła blokadę lub weryfikację dostępu")
    if not offers and not any(marker in visible_text for marker in _NO_RESULTS):
        raise sources.SourceError("nieobsługiwany HTML: nie wykryto kart ofert")
    return list(offers.values()), parser.next_url


def _fetch_jobsora(*, keywords: list[str], location: str, pages: int,
                   timeout: int) -> sources.SourceBatch:
    search_terms, page_limit, timeout_seconds = _validate_inputs(keywords, pages, timeout)
    offers: dict[str, dict[str, str]] = {}
    failures: list[str] = []
    warnings: list[str] = []
    succeeded = 0
    for keyword_index, keyword in enumerate(search_terms, start=1):
        try:
            page_url = _jobsora_search_url(keyword, str(location or ""))
        except sources.SourceError as exc:
            failures.append(f"hasło {keyword_index}: {exc}")
            continue
        used_location_fallback = False
        seen_page_sets: set[frozenset[str]] = set()
        stop_source = False
        for page_index in range(1, page_limit + 1):
            try:
                try:
                    page_html = _fetch_jobsora_html(page_url, timeout_seconds)
                except sources.SourceError as initial_error:
                    if page_index == 1 and str(location or "").strip() and _is_jobsora_404(initial_error):
                        page_url = _jobsora_search_url(keyword, "")
                        page_html = _fetch_jobsora_html(page_url, timeout_seconds)
                        used_location_fallback = True
                        warnings.append(
                            "lokalizacja nie jest obsługiwana w ścieżce Jobsora; zastosowano filtr lokalny, wyniki mogą być ucięte"
                        )
                    else:
                        raise
                rows, next_url = _parse_jobsora_page(page_html, page_url)
                succeeded += 1
                page_ids = frozenset(row["id"] for row in rows)
                if not rows:
                    break
                if page_ids in seen_page_sets:
                    warnings.append(f"hasło {keyword_index}: powtórzony zestaw ofert, dalsza paginacja zatrzymana")
                    break
                seen_page_sets.add(page_ids)
                if used_location_fallback:
                    target_location = str(location or "").strip().casefold()
                    rows = [row for row in rows if target_location in row.get("location", "").casefold()]
                offers.update({row["id"]: row for row in rows})
                if not next_url:
                    if page_index == 1 and page_limit > 1:
                        warnings.append(f"hasło {keyword_index}: portal nie udostępnił linku do kolejnej strony")
                    break
                if page_index >= page_limit:
                    warnings.append(f"hasło {keyword_index}: osiągnięto limit {page_limit} stron; wyniki mogą być ucięte")
                    break
                page_url = next_url
            except sources.SourceError as exc:
                message = f"hasło {keyword_index}, strona {page_index}: {exc}"
                if _is_terminal_source_error(exc):
                    failures.append(message + "; dalsze hasła pominięte po ograniczeniu źródła")
                    stop_source = True
                else:
                    failures.append(message)
                break
            except (ValueError, UnicodeError):
                failures.append(f"hasło {keyword_index}, strona {page_index}: nieprawidłowy format strony")
                break
        if stop_source:
            break
    batch = sources._finish_batch("jobsora", offers, failures, succeeded)
    return _append_warnings(batch, warnings)


def _safe_eldorado_url(value: Any) -> str | None:
    try:
        absolute = urllib.parse.urljoin(_ELDORADO_ORIGIN + "/", str(value or ""))
        parsed = urllib.parse.urlsplit(absolute)
        if (parsed.scheme.casefold() != "https" or parsed.hostname not in {"czyjesteldorado.pl", "www.czyjesteldorado.pl"}
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            return None
        path = urllib.parse.unquote(parsed.path).rstrip("/")
        if not re.fullmatch(r"/praca/\d+(?:-[^/]+)?", path):
            return None
        return urllib.parse.urlunsplit(("https", "czyjesteldorado.pl", parsed.path.rstrip("/"), "", ""))
    except (TypeError, ValueError, UnicodeError):
        return None


def _eldorado_offer_id(value: str) -> str | None:
    try:
        path = urllib.parse.unquote(urllib.parse.urlsplit(value).path).rstrip("/")
    except (TypeError, ValueError, UnicodeError):
        return None
    match = re.fullmatch(r"/praca/(\d+)(?:-[^/]+)?", path)
    return match.group(1) if match else None


def _mcp_payload(raw: bytes, content_type: str, expected_id: int | None = None) -> dict[str, Any] | None:
    def decode(candidate: str) -> dict[str, Any] | None:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict):
            return None
        if expected_id is not None and value.get("id") != expected_id:
            return None
        return value

    if "text/event-stream" not in content_type.casefold():
        return decode(raw.decode("utf-8", errors="replace")) if raw else None
    event_data: list[str] = []
    for line in raw.decode("utf-8", errors="replace").splitlines() + [""]:
        if not line:
            if event_data:
                message = decode("\n".join(event_data))
                if message is not None:
                    return message
                event_data = []
            continue
        if line.startswith(":") or not line.startswith("data:"):
            continue
        value = line[5:]
        event_data.append(value[1:] if value.startswith(" ") else value)
    return None


def _mcp_post(payload: dict[str, Any], timeout: int, *, session_id: str = "",
              protocol_version: str = "", expected_id: int | None = None) -> tuple[str, dict[str, Any] | None]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if protocol_version:
        headers["MCP-Protocol-Version"] = protocol_version
    request = urllib.request.Request(_ELDORADO_MCP, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            if not 200 <= response.status < 300:
                raise sources.SourceError(f"MCP zwrócił HTTP {response.status}")
            raw = response.read(_MAX_MCP_BODY)
            content_type = response.headers.get("Content-Type", "")
            return response.headers.get("Mcp-Session-Id", session_id), _mcp_payload(raw, content_type, expected_id)
    except urllib.error.HTTPError as exc:
        suffix = " (przekierowanie zablokowane)" if 300 <= exc.code < 400 else ""
        raise sources.SourceError(f"MCP zwrócił HTTP {exc.code}{suffix}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise sources.SourceError("brak połączenia z MCP lub przekroczony limit czasu") from None


class _EldoradoClient:
    def __init__(self, timeout: int) -> None:
        self.timeout = timeout
        self.session_id = ""
        self.protocol_version = _PROTOCOL_VERSION
        self.request_id = 0

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.request_id += 1
        request_id = self.request_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        session_id, response = _mcp_post(payload, self.timeout, session_id=self.session_id,
                                         protocol_version=self.protocol_version if self.session_id else "", expected_id=request_id)
        if session_id:
            self.session_id = session_id
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise sources.SourceError("MCP zwrócił nieprawidłową odpowiedź")
        if response.get("error"):
            raise sources.SourceError("serwer MCP zgłosił błąd żądania")
        result = response.get("result")
        if not isinstance(result, dict):
            raise sources.SourceError("MCP zwrócił nieprawidłowe dane")
        return result

    def initialize(self) -> None:
        result = self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "JobSearchStarter", "version": "0.1"},
        })
        server_version = result.get("protocolVersion")
        if isinstance(server_version, str) and server_version:
            self.protocol_version = server_version
        _, response = _mcp_post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                                self.timeout, session_id=self.session_id,
                                protocol_version=self.protocol_version)
        if response and response.get("error"):
            raise sources.SourceError("serwer MCP nie przyjął inicjalizacji")

    def search_jobs(self, phrase: str) -> dict[str, Any]:
        result = self._request("tools/call", {"name": "search_jobs", "arguments": {"phrase": phrase}})
        if result.get("isError") is True:
            raise sources.SourceError("serwer MCP zgłosił błąd wyszukiwania")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict) or not isinstance(structured.get("jobs"), list):
            raise sources.SourceError("MCP zwrócił nieprawidłową listę ofert")
        if not all(isinstance(item, dict) for item in structured["jobs"]):
            raise sources.SourceError("MCP zwrócił nieprawidłowe elementy ofert")
        return structured


def _eldorado_rows(jobs: list[dict[str, Any]]) -> tuple[dict[str, dict[str, str]], int]:
    offers: dict[str, dict[str, str]] = {}
    rejected = 0
    for job in jobs:
        title = _safe_display_text(job.get("title"))
        company = _safe_display_text(job.get("company"))
        location = _safe_display_text(sources._location_name(job.get("cities")))
        url = _safe_eldorado_url(job.get("url"))
        tag_text = _safe_display_text(job.get("keywords", []))
        row = sources._normalized("czyjesteldorado", "public", raw_id=_eldorado_offer_id(url or ""), title=title,
                                  company=company, location=location, url=url, match_text=tag_text)
        if row:
            offers[row["id"]] = row
        else:
            rejected += 1
    return offers, rejected


def _fetch_eldorado(*, keywords: list[str], location: str, pages: int,
                    timeout: int) -> sources.SourceBatch:
    search_terms, page_limit, timeout_seconds = _validate_inputs(keywords, pages, timeout)
    client = _EldoradoClient(timeout_seconds)
    client.initialize()
    offers: dict[str, dict[str, str]] = {}
    failures: list[str] = []
    warnings: list[str] = []
    succeeded = 0
    for keyword_index, keyword in enumerate(search_terms, start=1):
        phrase = " ".join(part for part in (keyword, str(location or "").strip()) if part)
        try:
            response = client.search_jobs(phrase)
            rows, rejected = _eldorado_rows(response["jobs"])
            succeeded += 1
            offers.update(rows)
            total = response.get("total_results")
            if isinstance(total, int) and not isinstance(total, bool) and total > len(response["jobs"]):
                warnings.append(
                    f"hasło {keyword_index}: MCP zwrócił {len(response['jobs'])} z {total} wyników; brak paginacji"
                )
            if rejected:
                warnings.append(f"hasło {keyword_index}: pominięto {rejected} wyników bez bezpiecznego URL lub tytułu")
        except sources.SourceError as exc:
            message = f"hasło {keyword_index}: {exc}"
            if _is_terminal_source_error(exc):
                failures.append(message + "; dalsze hasła pominięte po ograniczeniu źródła")
                break
            failures.append(message)
    if page_limit > 1:
        warnings.append("MCP nie udostępnia numeru strony; pobierana jest jedna porcja wyników na hasło")
    batch = sources._finish_batch("czyjesteldorado", offers, failures, succeeded)
    return _append_warnings(batch, warnings)


def fetch(name: str, *, keywords: list[str], location: str, pages: int,
          timeout: int, options: dict[str, Any]) -> sources.SourceBatch:
    """Pobierz oferty ze wskazanego źródła bez zapisywania treści ogłoszeń."""
    source_name = str(name or "").strip().casefold()
    if source_name == "jobsora":
        return _fetch_jobsora(keywords=keywords, location=location, pages=pages, timeout=timeout)
    if source_name == "czyjesteldorado":
        return _fetch_eldorado(keywords=keywords, location=location, pages=pages, timeout=timeout)
    raise sources.SourceError("nieobsługiwana tablica ofert")
