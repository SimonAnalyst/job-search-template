"""Adapters for public No Fluff Jobs and Bulldogjob result listings.

Only listing pages are requested. Job detail URLs are returned for the user to
open in a browser; this module never requests them.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import urllib.parse
from html.parser import HTMLParser
from typing import Any

from . import dynamic_boards, sources


_USER_AGENT = "JobSearchStarter/0.1"
_MAX_PAGES = 10
_MAX_KEYWORD_LENGTH = 120
_PAGE_SIZE_HINT = {"nofluffjobs": 20, "bulldogjob": 50}
_SITES = {
    "nofluffjobs": {
        "origin": "https://nofluffjobs.com",
        "listing_path": "/pl",
        "hosts": {"nofluffjobs.com", "www.nofluffjobs.com"},
        "detail_prefixes": ("/pl/job/", "/job/", "/pl/posting/", "/posting/"),
    },
    "bulldogjob": {
        "origin": "https://bulldogjob.com",
        "listing_path": "/companies/jobs",
        "hosts": {"bulldogjob.com", "www.bulldogjob.com", "bulldogjob.pl", "www.bulldogjob.pl"},
    },
}
_NO_RESULTS = (
    "no jobs found", "no results found", "nie znaleziono ofert", "brak ofert pracy",
    "nie znaleźliśmy ofert", "nie znaleźliśmy żadnych ofert",
)
_BLOCK_MARKERS = (
    "verify you are human", "checking your browser", "complete the security check",
    "access denied", "request blocked", "unusual traffic", "cloudflare ray id",
    "cf-chl-", "captcha challenge",
)


class _ListingParser(HTMLParser):
    """Collects script payloads and offer links with their visible heading."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[tuple[dict[str, str], str]] = []
        self.links: list[dict[str, str]] = []
        self.visible_text: list[str] = []
        self._script_attrs: dict[str, str] | None = None
        self._script_text: list[str] = []
        self._link: dict[str, Any] | None = None
        self._heading: list[str] | None = None
        self._in_title = False
        self.title: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.casefold(): value or "" for key, value in attrs}
        tag = tag.casefold()
        if tag == "script":
            self._script_attrs = attrs_map
            self._script_text = []
        elif tag == "a":
            self._link = {"attrs": attrs_map, "text": [], "heading": []}
        elif tag in {"h1", "h2", "h3"} and self._link is not None:
            self._heading = []
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "script" and self._script_attrs is not None:
            self.scripts.append((self._script_attrs, "".join(self._script_text)))
            self._script_attrs = None
            self._script_text = []
        elif tag == "a" and self._link is not None:
            attrs = self._link["attrs"]
            heading = " ".join(" ".join(self._link["heading"]).split())
            text = " ".join(" ".join(self._link["text"]).split())
            self.links.append({
                "url": attrs.get("href", ""),
                "title": heading or attrs.get("aria-label", "") or text,
            })
            self._link = None
            self._heading = None
        elif tag in {"h1", "h2", "h3"}:
            self._heading = None
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._script_attrs is not None:
            self._script_text.append(data)
            return
        text = " ".join(data.split())
        if not text:
            return
        self.visible_text.append(text)
        if self._link is not None:
            self._link["text"].append(text)
        if self._heading is not None:
            self._link["heading"].append(text)
        if self._in_title:
            self.title.append(text)


def _text_request(url: str, timeout: int) -> str:
    """Use the shared no-redirect public HTML requester."""
    return dynamic_boards._text_request(url, timeout)


def _listing_url(name: str, keyword: str, page: int) -> str:
    site = _SITES[name]
    if name == "nofluffjobs":
        query: list[tuple[str, str]] = []
        if keyword:
            query.append(("criteria", keyword))
        if page > 1:
            query.append(("page", str(page)))
        suffix = urllib.parse.urlencode(query)
        return site["origin"] + site["listing_path"] + ("?" + suffix if suffix else "")

    path = site["listing_path"]
    if keyword:
        path += "/s/skills," + urllib.parse.quote(keyword, safe="-_.")
    elif page > 1:
        # The no-term pagination route is not confirmed; keep the page-one
        # default and let fetch() report that it cannot safely page it.
        return site["origin"] + path
    if page > 1:
        path += f"/page,{page}"
    return site["origin"] + path


def _get_robots(listing_url: str, timeout: int) -> list[tuple[list[str], list[tuple[str, str]]]]:
    parsed = urllib.parse.urlsplit(listing_url)
    robots_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    try:
        body = _text_request(robots_url, timeout)
    except sources.SourceError as exc:
        raise sources.SourceError(f"nie można zweryfikować robots.txt; pomijam źródło ({exc})") from None
    return dynamic_boards._parse_robots(body)


def _safe_offer_url(name: str, value: Any, page_url: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    absolute = urllib.parse.urljoin(page_url, value.strip())
    try:
        parsed = urllib.parse.urlsplit(absolute)
        if (parsed.scheme.casefold() != "https" or parsed.hostname is None
                or parsed.hostname.casefold() not in _SITES[name]["hosts"]
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            return None
        path = urllib.parse.unquote(parsed.path).casefold()
    except (ValueError, UnicodeError):
        return None

    if name == "nofluffjobs":
        if not path.startswith(_SITES[name]["detail_prefixes"]):
            return None
        # robots disallows crawling /posting/; this is only a link emitted for
        # the user, and no detail page is fetched by this module.
        parts = [part for part in path.split("/") if part]
        required_parts = 3 if path.startswith(("/pl/job/", "/pl/posting/")) else 2
        if len(parts) < required_parts:
            return None
    else:
        parts = [part for part in path.strip("/").split("/") if part]
        if len(parts) != 3 or parts[:2] != ["companies", "jobs"] or parts[2] == "s":
            return None

    clean = sources.clean_url(absolute)
    if not clean:
        return None
    clean_parts = urllib.parse.urlsplit(clean)
    # Query parameters on job-detail links are not needed by the dashboard and
    # can carry tracking or other unrelated information.
    return urllib.parse.urlunsplit((clean_parts.scheme, clean_parts.netloc, clean_parts.path, "", ""))


def _first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), {})
    return value if isinstance(value, dict) else {}


def _searchable_text(value: Any) -> str:
    if isinstance(value, str):
        value = html_lib.unescape(re.sub(r"<[^>]*>", " ", value))
        return " ".join(value.split())
    if isinstance(value, list):
        return " ".join(part for item in value if (part := _searchable_text(item)))
    if isinstance(value, dict):
        return _searchable_text(value.get("name") or value.get("text") or value.get("value") or "")
    return ""


def _redact_text(value: Any) -> str:
    text = sources._redact_salary(_searchable_text(value))
    return re.sub(
        r"(\[wynagrodzenie pominięte\])\s*(?:/|per)\s*(?:h|hr|hrs|hour|hours|godz(?:\.|iny|inę|ina)?)\b",
        r"\1", text, flags=re.IGNORECASE,
    )


def _make_row(name: str, *, raw_id: Any, title: Any, company: Any,
              location: Any, url: Any, page_url: str, match_text: Any = "") -> dict[str, str] | None:
    offer_url = _safe_offer_url(name, url, page_url)
    if not offer_url:
        return None
    safe_match = _redact_text(match_text)
    return sources._normalized(
        name, "public", raw_id=raw_id, title=_redact_text(title), company=company,
        location=location, url=offer_url, match_text=safe_match,
    )


def _jobpostings(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            kind = node.get("@type", [])
            kinds = kind if isinstance(kind, list) else [kind]
            if any(str(item).casefold() == "jobposting" for item in kinds):
                found.append(node)
            for key in ("@graph", "itemListElement", "mainEntity"):
                if key in node:
                    visit(node[key])

    visit(value)
    return found

def _jsonld_rows(name: str, page: _ListingParser, page_url: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for attrs, raw in page.scripts:
        if attrs.get("type", "").split(";", 1)[0].strip().casefold() != "application/ld+json":
            continue
        try:
            decoded = json.loads(html_lib.unescape(raw))
        except json.JSONDecodeError:
            continue
        for item in _jobpostings(decoded):
            identifier = item.get("identifier")
            if isinstance(identifier, dict):
                identifier = identifier.get("value") or identifier.get("name")
            organization = _first_dict(item.get("hiringOrganization"))
            url = item.get("url") or item.get("sameAs") or ""
            if isinstance(url, dict):
                url = url.get("url", "")
            location = dynamic_boards._jsonld_location(item.get("jobLocation") or item.get("jobLocationType"))
            match = " ".join(filter(None, (
                _searchable_text(item.get("description")),
                _searchable_text(item.get("skills")),
                _searchable_text(item.get("qualifications")),
            )))
            row = _make_row(
                name, raw_id=identifier, title=item.get("title") or item.get("name"),
                company=organization.get("name", ""), location=location,
                url=url, page_url=page_url, match_text=match,
            )
            if row:
                out.append(row)
    return out


def _find_list(node: Any, key: str) -> tuple[bool, list[Any] | None]:
    if isinstance(node, dict):
        if key in node:
            value = node[key]
            return True, value if isinstance(value, list) else None
        for value in node.values():
            found, rows = _find_list(value, key)
            if found:
                return found, rows
    elif isinstance(node, list):
        for value in node:
            found, rows = _find_list(value, key)
            if found:
                return found, rows
    return False, None


def _nofluff_location(value: Any) -> str:
    if not isinstance(value, dict):
        return sources._location_name(value)
    places = value.get("places") or []
    places = places if isinstance(places, list) else [places]
    locations: list[str] = []
    for place in places:
        if isinstance(place, dict):
            label = place.get("city") or place.get("name") or place.get("displayName") or ""
        else:
            label = place
        normalized = " ".join(str(label or "").split())
        if normalized and normalized.casefold() not in {item.casefold() for item in locations}:
            locations.append(normalized)
    remote = value.get("fullyRemote") in (True, 1, "true", "True")
    if remote:
        for label in ("Zdalnie", "Remote"):
            if label.casefold() not in {item.casefold() for item in locations}:
                locations.append(label)
    if not locations:
        fallback = sources._location_name(value.get("name") or value.get("city") or "")
        if fallback:
            locations.append(fallback)
    return ", ".join(locations)


def _nofluff_state_rows(page: _ListingParser, page_url: str) -> tuple[bool, list[dict[str, str]]]:
    for attrs, raw in page.scripts:
        if attrs.get("id", "").casefold() != "serverapp-state":
            continue
        try:
            payload = json.loads(html_lib.unescape(raw))
        except json.JSONDecodeError:
            raise sources.SourceError("strona No Fluff Jobs zwróciła nieprawidłowe dane") from None
        found, postings = _find_list(payload, "postings")
        if not found or postings is None:
            raise sources.SourceError("stan No Fluff Jobs nie zawiera rozpoznanej listy ofert")
        rows: list[dict[str, str]] = []
        for posting in postings:
            if not isinstance(posting, dict):
                continue
            slug = posting.get("url")
            if isinstance(slug, str) and slug.strip() and not slug.startswith(("/", "https://", "http://")):
                offer_url = "/pl/job/" + urllib.parse.quote(slug.strip(), safe="-_.")
            else:
                offer_url = slug
            match = " ".join(filter(None, (
                _searchable_text(posting.get("technology")),
                _searchable_text(posting.get("category")),
            )))
            row = _make_row(
                "nofluffjobs", raw_id=posting.get("id") or posting.get("reference"),
                title=posting.get("title"), company=posting.get("name"),
                location=_nofluff_location(posting.get("location")),
                url=offer_url, page_url=page_url, match_text=match,
            )
            if row:
                rows.append(row)
        if postings and not rows:
            raise sources.SourceError("stan No Fluff Jobs zawiera oferty bez rozpoznanego linku")
        return True, rows
    return False, []

def _find_jobs(node: Any) -> tuple[bool, list[Any]]:
    if isinstance(node, dict):
        for key in ("jobs", "offers", "jobOffers"):
            if key in node:
                value = node[key]
                return True, value if isinstance(value, list) else []
        for value in node.values():
            found, jobs = _find_jobs(value)
            if found:
                return found, jobs
    elif isinstance(node, list):
        for value in node:
            found, jobs = _find_jobs(value)
            if found:
                return found, jobs
    return False, []


def _bulldog_skill_filter_matches(page_props: Any, keyword: str) -> bool:
    target = keyword.strip().casefold()
    if not target or not isinstance(page_props, dict):
        return False
    slug_state = page_props.get("slugState")
    filters = slug_state.get("filters") if isinstance(slug_state, dict) else None
    skills = filters.get("skills") if isinstance(filters, dict) else None
    entries = skills if isinstance(skills, list) else [skills]
    for entry in entries:
        value = entry.get("value") if isinstance(entry, dict) else entry
        if isinstance(value, str) and value.strip().casefold() == target:
            return True
    return False

def _bulldog_next_rows(page: _ListingParser, page_url: str, keyword: str = "") -> tuple[bool, list[dict[str, str]]]:
    for attrs, raw in page.scripts:
        if attrs.get("id", "").casefold() != "__next_data__":
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise sources.SourceError("strona Bulldogjob zwróciła nieprawidłowe dane") from None
        props = payload.get("props") if isinstance(payload, dict) else None
        raw_page_props = props.get("pageProps") if isinstance(props, dict) else None
        has_page_props = isinstance(raw_page_props, dict)
        page_props = raw_page_props if has_page_props else payload
        direct_jobs = raw_page_props.get("jobs") if has_page_props else None
        if isinstance(direct_jobs, list):
            found, jobs = True, direct_jobs
            selected_skill = _bulldog_skill_filter_matches(raw_page_props, keyword)
        else:
            found, jobs = _find_jobs(page_props)
            selected_skill = False
        if not found:
            continue
        rows: list[dict[str, str]] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            identifier = job.get("id") or job.get("jobId") or job.get("jobOfferId") or job.get("offerId")
            title = job.get("position") or job.get("title") or job.get("jobTitle") or job.get("name")
            company = _first_dict(job.get("company") or job.get("employer"))
            company_name = company.get("name", "") or job.get("companyName") or job.get("employerName") or ""
            location = sources._location_name(job.get("city") or job.get("location") or job.get("locationName") or "")
            if job.get("remote") in (True, 1, "true", "True", "remote", "Remote"):
                if "remote" not in location.casefold():
                    location = ", ".join(filter(None, (location, "Remote")))
            offer_url = ("/companies/jobs/" + urllib.parse.quote(str(identifier), safe="-_.")
                         if identifier not in (None, "") else "")
            match = " ".join(filter(None, (
                _searchable_text(job.get("technologyTags")),
                _searchable_text(job.get("skills") or job.get("requiredSkills") or job.get("technologies")),
                _searchable_text(job.get("category") or job.get("mainCategory")),
                keyword if selected_skill else "",
            )))
            row = _make_row(
                "bulldogjob", raw_id=identifier, title=title, company=company_name,
                location=location, url=offer_url, page_url=page_url, match_text=match,
            )
            if row:
                rows.append(row)
        if jobs and not rows:
            raise sources.SourceError("stan Bulldogjob zawiera oferty bez rozpoznanych pól lub ID")
        return True, rows
    return False, []


def _anchor_rows(name: str, page: _ListingParser, page_url: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for link in page.links:
        offer_url = _safe_offer_url(name, link.get("url"), page_url)
        if not offer_url:
            continue
        parsed = urllib.parse.urlsplit(offer_url)
        path_parts = [part for part in parsed.path.strip("/").split("/") if part]
        raw_id = path_parts[-1] if path_parts else None
        row = _make_row(
            name, raw_id=raw_id, title=link.get("title"), company="", location="",
            url=offer_url, page_url=page_url,
        )
        if row:
            out.append(row)
    return out


def _merge_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    by_url: dict[str, dict[str, str]] = {}
    for row in rows:
        current = by_url.get(row["url"])
        if current is None:
            by_url[row["url"]] = row
            continue
        for field in ("title", "company", "location"):
            if not current.get(field) and row.get(field):
                current[field] = row[field]
        current["_match_text"] = " ".join(filter(None, (current.get("_match_text", ""), row.get("_match_text", ""))))
    return list(by_url.values())


def _parse_page(name: str, page_html: str, page_url: str, keyword: str = "") -> list[dict[str, str]]:
    parser = _ListingParser()
    try:
        parser.feed(page_html)
        parser.close()
    except Exception:
        raise sources.SourceError("nie można odczytać HTML strony") from None

    if name == "nofluffjobs":
        known_state, state_rows = _nofluff_state_rows(parser, page_url)
        if known_state:
            return state_rows
    if name == "bulldogjob":
        known_state, next_rows = _bulldog_next_rows(parser, page_url, keyword)
        if known_state:
            return next_rows

    rows = _jsonld_rows(name, parser, page_url)
    rows.extend(_anchor_rows(name, parser, page_url))
    rows = _merge_rows(rows)
    if rows:
        return rows

    visible = " ".join((" ".join(parser.title), *parser.visible_text)).casefold()
    if any(marker in visible for marker in _BLOCK_MARKERS):
        raise sources.SourceError("strona wymaga weryfikacji dostępu lub zgłosiła blokadę")
    if any(marker in visible for marker in _NO_RESULTS):
        return []
    raise sources.SourceError("nieobsługiwany HTML: nie wykryto rozpoznanych ofert")


def _validated_args(name: str, keywords: list[str], location: str, pages: int,
                    timeout: int, options: dict[str, Any]) -> tuple[list[str], str, int, int]:
    if name not in _SITES:
        raise sources.SourceError("nieobsługiwane źródło job board")
    if not isinstance(options, dict):
        raise sources.SourceError("ustawienia źródła muszą być obiektem")
    if not isinstance(keywords, list) or any(not isinstance(item, str) for item in keywords):
        raise sources.SourceError("słowa kluczowe muszą być listą tekstów")
    if not isinstance(location, str):
        raise sources.SourceError("lokalizacja musi być tekstem")
    try:
        pages = int(pages)
        timeout = int(timeout)
    except (TypeError, ValueError):
        raise sources.SourceError("nieprawidłowy limit stron lub timeout") from None
    if not (1 <= pages <= _MAX_PAGES) or not (2 <= timeout <= 120):
        raise sources.SourceError("nieprawidłowy limit stron lub timeout")
    terms: list[str] = []
    for item in keywords:
        term = item.strip()
        if len(term) > _MAX_KEYWORD_LENGTH or any(ord(char) < 32 for char in term):
            raise sources.SourceError("słowo kluczowe jest zbyt długie lub zawiera niedozwolone znaki")
        if term and term.casefold() not in {current.casefold() for current in terms}:
            terms.append(term)
    return terms or [""], " ".join(location.split()), pages, timeout


def _is_terminal_source_error(exc: sources.SourceError) -> bool:
    message = str(exc).casefold()
    return (re.search(r"\bhttp\s+(?:403|429)\b", message) is not None
            or "wymaga weryfikacji dostępu" in message
            or "zgłosiła blokadę" in message)

def fetch(name: str, *, keywords: list[str], location: str, pages: int,
          timeout: int, options: dict[str, Any]) -> sources.SourceBatch:
    """Fetch public listing pages per keyword, without opening job detail pages."""
    terms, location, pages, timeout = _validated_args(name, keywords, location, pages, timeout, options)
    site = _SITES[name]
    offers: dict[str, dict[str, str]] = {}
    by_url: dict[str, dict[str, str]] = {}
    failures: list[str] = []
    succeeded = 0
    stop_batch = False
    try:
        robots = _get_robots(site["origin"] + site["listing_path"], timeout)
    except sources.SourceError as exc:
        raise sources.SourceError(f"robots.txt: {exc}") from None

    if name == "bulldogjob" and not any(terms) and pages > 1:
        failures.append("paginacja Bulldogjob bez słowa kluczowego nie jest potwierdzona; pobieram jedną stronę")
        pages = 1

    for term_index, term in enumerate(terms, start=1):
        if stop_batch:
            break
        previous_page_urls: set[str] = set()
        for page_number in range(1, pages + 1):
            page_url = _listing_url(name, term, page_number)
            try:
                if not dynamic_boards._robots_can_fetch(robots, page_url, _USER_AGENT):
                    raise sources.SourceError("robots.txt zabrania pobrania tej ścieżki")
                page_html = _text_request(page_url, timeout)
                rows = _parse_page(name, page_html, page_url, term)
                succeeded += 1
                if not rows:
                    break
                page_urls = {row["url"] for row in rows}
                if page_number > 1 and page_urls and page_urls.issubset(previous_page_urls):
                    failures.append(f"hasło {term_index}: kolejna strona powtarza oferty; paginacja zatrzymana")
                    break
                previous_page_urls.update(page_urls)
                for row in rows:
                    existing = by_url.get(row["url"])
                    if existing is not None:
                        merged = _merge_rows([existing, row])[0]
                        by_url[row["url"]] = merged
                        offers[merged["id"]] = merged
                    else:
                        by_url[row["url"]] = row
                        offers[row["id"]] = row
                if page_number == pages and len(rows) >= _PAGE_SIZE_HINT[name]:
                    failures.append(
                        f"hasło {term_index}: osiągnięto limit {pages} stron; mogą pozostać dalsze oferty"
                    )
            except sources.SourceError as exc:
                failures.append(f"hasło {term_index}, strona {page_number}: {exc}")
                if _is_terminal_source_error(exc):
                    stop_batch = True
                break

    final_rows = list(by_url.values())
    missing_location = sum(not row.get("location") for row in final_rows)
    missing_company = sum(not row.get("company") for row in final_rows)
    if location and succeeded and offers:
        failures.insert(0, "lokalizacja jest filtrowana lokalnie; listing źródła nie otrzymał filtra miasta")
    if missing_location:
        failures.append(
            f"brak rozpoznanej lokalizacji w {missing_location} z {len(final_rows)} ofert; filtr miasta może je pominąć"
        )
    if missing_company:
        failures.append(
            f"brak rozpoznanej firmy w {missing_company} z {len(final_rows)} ofert"
        )
    return sources._finish_batch(name, offers, failures, succeeded)















