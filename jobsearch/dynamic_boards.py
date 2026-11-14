"""Dodatkowe publiczne job boardy z dozwolonymi, jawnymi formatami danych."""
from __future__ import annotations

import html as html_lib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import sources

SOLID_DIVISIONS = {"IT", "Engineering", "Marketing", "Sales", "HR", "Logistics", "Finances", "Other"}
_DEFAULT_CAMPAIGN = "job-search-template"
_USER_AGENT = "JobSearchStarter/0.1"
_MAX_PAGES = 10


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Nie śledzi przekierowań poza ścieżką zweryfikowaną przez robots.txt."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


def _json_request(url: str, timeout: int) -> Any:
    return sources._request_json(url, timeout)


def _text_request(url: str, timeout: int) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise sources.SourceError("nieprawidłowy adres źródła")
    request = urllib.request.Request(url, headers={"Accept": "text/html,application/xhtml+xml,text/plain", "User-Agent": _USER_AGENT})
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read(5_000_000).decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        suffix = " (przekierowanie zablokowane)" if 300 <= exc.code < 400 else ""
        raise sources.SourceError(f"odpowiedź HTTP {exc.code}{suffix}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise sources.SourceError("brak połączenia lub przekroczony limit czasu") from None


def _query_url(base: str, params: dict[str, Any]) -> str:
    return base + ("&" if "?" in base else "?") + urllib.parse.urlencode(params)


def _rows(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        candidates = next((payload[field] for field in (key, "jobs", "offers", "items", "results")
                           if field in payload), None)
        if not isinstance(candidates, list):
            raise sources.SourceError("nieprawidłowa odpowiedź API")
    else:
        raise sources.SourceError("nieprawidłowa odpowiedź API")
    if not all(isinstance(row, dict) for row in candidates):
        raise sources.SourceError("nieprawidłowa lista ofert")
    return candidates


def _searchable_text(value: Any) -> str:
    """Spłaszcza treść wyłącznie do tymczasowego pola używanego przy dopasowaniu."""
    if isinstance(value, str):
        text = html_lib.unescape(re.sub(r"<[^>]*>", " ", value))
        return " ".join(text.split())
    if isinstance(value, list):
        return " ".join(part for item in value if (part := _searchable_text(item)))
    if isinstance(value, dict):
        return _searchable_text(value.get("name") or value.get("text") or value.get("value") or "")
    return ""


def _solidjobs(*, keywords: list[str], location: str, pages: int, timeout: int,
               options: dict[str, Any]) -> sources.SourceBatch:
    raw_divisions = options.get("divisions", ["IT"])
    if not isinstance(raw_divisions, list) or not raw_divisions:
        raise sources.SourceError("podaj co najmniej jeden dział SOLID.Jobs")
    divisions: list[str] = []
    for item in raw_divisions:
        division = str(item).strip()
        if division not in SOLID_DIVISIONS:
            raise sources.SourceError("nieznany dział SOLID.Jobs")
        if division not in divisions:
            divisions.append(division)
    campaign = str(options.get("campaign", _DEFAULT_CAMPAIGN)).strip()
    if not re.fullmatch(r"[a-z0-9-]{1,64}", campaign):
        raise sources.SourceError("campaign SOLID.Jobs musi składać się z małych liter, cyfr i łączników")

    by_id: dict[str, dict[str, str]] = {}
    failures: list[str] = []
    succeeded = 0
    queries = keywords or [""]
    for division in divisions:
        for word_index, keyword in enumerate(queries, start=1):
            reported_total: int | None = None
            retrieved_pages = 0
            for page in range(min(pages, _MAX_PAGES)):
                params: dict[str, Any] = {
                    "campaign": campaign,
                    "pageIndex": page,
                    "pageSize": 50,
                }
                if location:
                    params["search.cities"] = location
                if keyword:
                    params["search.searchTerm"] = keyword
                url = _query_url(
                    f"https://solid.jobs/public-api/offers/{urllib.parse.quote(division)}", params
                )
                try:
                    payload = _json_request(url, timeout)
                    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
                        raise sources.SourceError("nieprawidłowa odpowiedź API")
                    response_page = payload.get("pageIndex")
                    total_pages = payload.get("totalPages")
                    if (not isinstance(response_page, int) or isinstance(response_page, bool)
                            or response_page != page):
                        raise sources.SourceError("nieprawidłowy numer strony w odpowiedzi API")
                    if (not isinstance(total_pages, int) or isinstance(total_pages, bool)
                            or total_pages < 0):
                        raise sources.SourceError("nieprawidłowe metadane paginacji")
                    jobs = payload["jobs"]
                    if not all(isinstance(item, dict) for item in jobs):
                        raise sources.SourceError("nieprawidłowa lista ofert")
                    if reported_total is not None and total_pages != reported_total:
                        raise sources.SourceError("zmieniły się metadane paginacji w trakcie pobierania")
                    reported_total = total_pages
                    if total_pages == 0 and jobs:
                        raise sources.SourceError("sprzeczne metadane paginacji")
                    if page >= total_pages and total_pages > 0:
                        raise sources.SourceError("odpowiedź wykracza poza deklarowaną paginację")
                    succeeded += 1
                    retrieved_pages += 1
                    for item in jobs:
                        row = sources._normalized(
                            "solidjobs", division, raw_id=item.get("jobOfferKey"),
                            title=item.get("title"), company=item.get("company"),
                            location=sources._location_name(item.get("locations")), url=item.get("url"),
                            match_text=" ".join(filter(None, (
                                _searchable_text(item.get("description")),
                                _searchable_text(item.get("skills")),
                            ))),
                        )
                        if row:
                            by_id[row["id"]] = row
                    if not jobs and page + 1 < total_pages:
                        failures.append(
                            f"dział {division}, hasło {word_index}, strona {page + 1}: pusta strona przed końcem paginacji"
                        )
                        break
                    if page + 1 >= total_pages:
                        break
                except sources.SourceError as exc:
                    failures.append(f"dział {division}, hasło {word_index}, strona {page + 1}: {exc}")
                    break
            if reported_total is not None and retrieved_pages < reported_total and retrieved_pages >= min(pages, _MAX_PAGES):
                failures.append(
                    f"dział {division}, hasło {word_index}: limit stron, pobrano {retrieved_pages} z {reported_total}"
                )
    return sources._finish_batch("solidjobs", by_id, failures, succeeded)


def _jsonld_postings(page_html: str) -> list[dict[str, Any]]:
    scripts = re.findall(r"<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script\s*>",
                         page_html, flags=re.IGNORECASE | re.DOTALL)
    found: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            kind = value.get("@type", [])
            types = kind if isinstance(kind, list) else [kind]
            if any(str(name).casefold() == "jobposting" for name in types):
                found.append(value)
            for key in ("@graph", "itemListElement", "mainEntity"):
                if key in value:
                    visit(value[key])

    for raw in scripts:
        try:
            visit(json.loads(html_lib.unescape(raw)))
        except json.JSONDecodeError:
            continue
    return found


def _jsonld_location(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(filter(None, (_jsonld_location(item) for item in value)))
    if isinstance(value, dict):
        address = value.get("address")
        if isinstance(address, list):
            return ", ".join(filter(None, (_jsonld_location(item) for item in address)))
        if isinstance(address, dict):
            return ", ".join(str(address[key]).strip() for key in ("addressLocality", "addressRegion", "addressCountry")
                              if address.get(key))
        return str(value.get("name") or "")
    return str(value or "")


def _jsonld_rows(source: str, board: str, page_html: str, page_url: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in _jsonld_postings(page_html):
        organization = item.get("hiringOrganization") or {}
        if isinstance(organization, list):
            organization = organization[0] if organization else {}
        company = organization.get("name", "") if isinstance(organization, dict) else ""
        location = _jsonld_location(item.get("jobLocation") or item.get("jobLocationType"))
        url = item.get("url") or item.get("sameAs") or ""
        if isinstance(url, dict):
            url = url.get("url", "")
        if isinstance(url, str) and url.startswith("/"):
            url = urllib.parse.urljoin(page_url, url)
        identifier = item.get("identifier")
        raw_id = identifier.get("value") if isinstance(identifier, dict) else identifier
        row = sources._normalized(source, board, raw_id=raw_id, title=item.get("title") or item.get("name"),
            company=company, location=location, url=url,
            match_text=" ".join(filter(None, (_searchable_text(item.get("description")),
                                               _searchable_text(item.get("skills"))))))
        if row:
            out.append(row)
    return out


def _decode_json_string(value: str) -> str:
    try:
        return json.loads('"' + value + '"')
    except (json.JSONDecodeError, TypeError):
        return value.replace(r"\"", '"').replace(r"\/", "/")


def _embedded_value(segment: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}":"((?:\\.|[^"\\])*)"', segment, re.DOTALL)
    return _decode_json_string(match.group(1)) if match else ""


def _embedded_rows(source: str, page_html: str) -> list[dict[str, str]]:
    decoded = page_html.replace(r'\"', '"').replace(r'\/', '/')
    pattern = re.compile(
        r'"body":"(?P<title>(?:\\.|[^"\\])*)".{0,12000}?'
        r'"companyName":"(?P<company>(?:\\.|[^"\\])*)".{0,12000}?'
        r'"slug":"(?P<slug>(?:\\.|[^"\\])+?)"', re.DOTALL
    )
    base = "https://rocketjobs.pl" if source == "rocketjobs" else "https://justjoin.it"
    path = "/oferta-pracy/" if source == "rocketjobs" else "/job-offer/"
    rows: list[dict[str, str]] = []
    for match in pattern.finditer(decoded):
        title = _decode_json_string(match.group("title"))
        company = _decode_json_string(match.group("company"))
        slug = _decode_json_string(match.group("slug"))
        if not slug:
            continue
        segment_end = min(len(decoded), match.end() + 12000)
        next_offer = re.search(r'"body":"', decoded[match.end():segment_end])
        if next_offer:
            segment_end = match.end() + next_offer.start()
        segment = decoded[match.start():segment_end]
        city = _embedded_value(segment, "city")
        skill_names = [_decode_json_string(found) for found in re.findall(
            r'"name":"((?:\\.|[^"\\])*)"', segment
        )]
        match_text = " ".join(filter(None, (_embedded_value(segment, "description"), *skill_names)))
        url = base + path + urllib.parse.quote(slug, safe="/-_")
        row = sources._normalized(source, "public", raw_id=slug, title=title, company=company,
                                  location=city, url=url, match_text=match_text)
        if row:
            rows.append(row)
    return rows


def _listing_urls(name: str, options: dict[str, Any]) -> list[str]:
    defaults = {
        "justjoinit": "https://justjoin.it/job-offers/all-locations",
        "rocketjobs": "https://rocketjobs.pl/oferty-pracy/wszystkie-lokalizacje",
        "jobhunt": "https://jobhunt.pl/",
    }
    configured = options.get("listing_urls", options.get("listing_url"))
    if configured is None:
        configured = [defaults[name]]
    elif isinstance(configured, str):
        configured = [configured]
    if not isinstance(configured, list) or not configured:
        raise sources.SourceError("podaj listing_urls z publicznymi stronami list ofert")
    allowed = {
        "justjoinit": {"justjoin.it", "www.justjoin.it"},
        "rocketjobs": {"rocketjobs.pl", "www.rocketjobs.pl"},
        "jobhunt": {"jobhunt.pl", "www.jobhunt.pl"},
    }
    known_paths = {name: urllib.parse.urlsplit(url).path for name, url in defaults.items()}
    urls: list[str] = []
    for value in configured:
        parsed = urllib.parse.urlsplit(str(value))
        if (parsed.scheme != "https" or parsed.hostname not in allowed[name] or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.path != known_paths[name]
                or parsed.query or parsed.fragment):
            raise sources.SourceError("listing_urls zawiera nieobsługiwany adres")
        urls.append(urllib.parse.urlunsplit(parsed))
    return urls


def _parse_robots(body: str) -> list[tuple[list[str], list[tuple[str, str]]]]:
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []

    def finish_group() -> None:
        nonlocal agents, rules
        if agents:
            groups.append((agents, rules))
        agents, rules = [], []

    for raw_line in body.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            continue
        directive, value = line.split(":", 1)
        directive, value = directive.strip().casefold(), value.strip()
        if directive == "user-agent":
            if rules:
                finish_group()
            if value:
                agents.append(value.casefold())
        elif directive in {"allow", "disallow"} and agents:
            rules.append((directive, value))
    finish_group()
    if not groups:
        raise sources.SourceError("robots.txt ma nierozpoznany format; pobieranie strony wyłączone")
    return groups


def _robots_can_fetch(groups: list[tuple[list[str], list[tuple[str, str]]]], url: str,
                      user_agent: str = _USER_AGENT) -> bool:
    agent = user_agent.casefold()
    best_specificity = 0
    selected: list[tuple[str, str]] = []
    wildcard: list[tuple[str, str]] = []
    for agents, rules in groups:
        matched = [item for item in agents if item != "*" and item in agent]
        if matched:
            specificity = max(map(len, matched))
            if specificity > best_specificity:
                best_specificity, selected = specificity, list(rules)
            elif specificity == best_specificity:
                selected.extend(rules)
        elif "*" in agents:
            wildcard.extend(rules)
    if best_specificity == 0:
        selected = wildcard

    parsed = urllib.parse.urlsplit(url)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    matches: list[tuple[int, str]] = []
    for directive, pattern in selected:
        if directive == "disallow" and not pattern:
            continue
        terminal = pattern.endswith("$")
        body = pattern[:-1] if terminal else pattern
        regex = "^" + "".join(".*" if char == "*" else re.escape(char) for char in body)
        regex += "$" if terminal else ".*"
        if re.match(regex, target):
            specificity = len(body.replace("*", ""))
            matches.append((specificity, directive))
    if not matches:
        return True
    strongest = max(specificity for specificity, _ in matches)
    return any(directive == "allow" for specificity, directive in matches if specificity == strongest)


def _robots_for(listing_url: str, timeout: int) -> list[tuple[list[str], list[tuple[str, str]]]]:
    parsed = urllib.parse.urlsplit(listing_url)
    robots_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    try:
        body = _text_request(robots_url, timeout)
    except sources.SourceError:
        raise sources.SourceError("nie można zweryfikować robots.txt; pobieranie strony wyłączone") from None
    return _parse_robots(body)


def _html_board(name: str, *, keywords: list[str], location: str, pages: int, timeout: int,
                options: dict[str, Any]) -> sources.SourceBatch:
    listings = _listing_urls(name, options)
    by_id: dict[str, dict[str, str]] = {}
    failures: list[str] = []
    succeeded = 0
    robots_by_host: dict[str, list[tuple[list[str], list[tuple[str, str]]]]] = {}
    page_limit = min(pages, _MAX_PAGES)
    for listing_index, listing in enumerate(listings, start=1):
        parsed = urllib.parse.urlsplit(listing)
        terms = keywords or [""]
        for word_index, keyword in enumerate(terms, start=1):
            query_page_limit = 1 if name == "rocketjobs" else page_limit
            previous_page_ids: set[str] = set()
            for page in range(query_page_limit):
                query = [(key, value) for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                         if key not in {"page", "search", "keyword", "city"}]
                if page:
                    query.append(("page", str(page + 1)))
                if keyword:
                    query.append(("keyword", keyword))
                page_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                                                   urllib.parse.urlencode(query), ""))
                try:
                    robot = robots_by_host.get(parsed.hostname or "")
                    if robot is None:
                        robot = _robots_for(listing, timeout)
                        robots_by_host[parsed.hostname or ""] = robot
                    if not _robots_can_fetch(robot, page_url):
                        raise sources.SourceError("robots.txt zabrania pobierania tej ścieżki")
                    page_html = _text_request(page_url, timeout)
                    postings = _jsonld_rows(name, "public", page_html, page_url)
                    if name in {"justjoinit", "rocketjobs"}:
                        postings.extend(_embedded_rows(name, page_html))
                    if not postings:
                        raise sources.SourceError("strona nie zawiera rozpoznanych danych ofert")
                    page_ids = {row["id"] for row in postings}
                    if page > 0 and page_ids and page_ids.issubset(previous_page_ids):
                        failures.append(
                            f"lista {listing_index}, hasło {word_index}: kolejna strona powtarza oferty; paginacja zatrzymana"
                        )
                        break
                    previous_page_ids.update(page_ids)
                    succeeded += 1
                    for row in postings:
                        by_id[row["id"]] = row
                    if name == "rocketjobs":
                        failures.append(
                            f"lista {listing_index}, hasło {word_index}: paginacja nie jest potwierdzona; pobrano jedną stronę"
                        )
                        break
                    if len(postings) < 10:
                        break
                    if page + 1 == query_page_limit:
                        failures.append(
                            f"lista {listing_index}, hasło {word_index}: osiągnięto limit {query_page_limit} stron; mogą pozostać dalsze oferty"
                        )
                except sources.SourceError as exc:
                    failures.append(f"lista {listing_index}, hasło {word_index}, strona {page + 1}: {exc}")
                    break
    if location and succeeded:
        failures.insert(0, "lokalizacja jest filtrowana lokalnie; HTML źródła nie potwierdza filtra miasta")
    return sources._finish_batch(name, by_id, failures, succeeded)


def _capgemini(*, keywords: list[str], location: str, pages: int, timeout: int,
               options: dict[str, Any]) -> sources.SourceBatch:
    if not keywords:
        raise sources.SourceError("wpisz hasła wyszukiwania dla Capgemini")
    endpoint = "https://cg-jobstream-api.azurewebsites.net/api/job-search"
    by_id: dict[str, dict[str, str]] = {}
    failures: list[str] = []
    succeeded = 0
    for index, keyword in enumerate(keywords, start=1):
        total_pages: int | None = None
        retrieved_pages = 0
        previous_page_ids: set[str] = set()
        for page in range(min(pages, _MAX_PAGES)):
            params = {"search": keyword, "country_code": "pl-pl", "size": 50, "page": page + 1}
            try:
                payload = _json_request(_query_url(endpoint, params), timeout)
                if not isinstance(payload, dict):
                    raise sources.SourceError("nieprawidłowa odpowiedź API")
                total = payload.get("total")
                if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                    raise sources.SourceError("nieprawidłowe metadane paginacji")
                items = _rows(payload, "data")
                declared_pages = (total + 49) // 50
                if total_pages is not None and declared_pages != total_pages:
                    raise sources.SourceError("zmieniły się metadane paginacji w trakcie pobierania")
                total_pages = declared_pages
                if page >= total_pages and total_pages > 0:
                    raise sources.SourceError("odpowiedź wykracza poza deklarowaną paginację")
                if total == 0 and items:
                    raise sources.SourceError("sprzeczne metadane paginacji")
                if not items and page < total_pages:
                    raise sources.SourceError("pusta strona przed końcem paginacji")
                succeeded += 1
                retrieved_pages += 1
                page_ids: set[str] = set()
                for item in items:
                    raw_id = item.get("id") or item.get("ref")
                    title = item.get("title")
                    ref = item.get("ref")
                    origin = item.get("source")
                    suffix = f"{ref}+{str(origin).lower()}" if ref and origin else raw_id
                    url = item.get("url")
                    if not url and suffix:
                        url = f"https://www.capgemini.com/jobs/{urllib.parse.quote(str(suffix), safe='+-')}"
                    row = sources._normalized("capgemini", "pl-pl", raw_id=raw_id, title=title,
                        company="Capgemini", location=item.get("location", ""), url=url,
                        match_text=" ".join(filter(None, (_searchable_text(item.get("description")),
                                                           _searchable_text(item.get("skills"))))))
                    if row:
                        by_id[row["id"]] = row
                        page_ids.add(row["id"])
                if page and page_ids and page_ids.issubset(previous_page_ids):
                    failures.append(f"hasło {index}: kolejna strona powtarza oferty; paginacja zatrzymana")
                    break
                previous_page_ids.update(page_ids)
                if page + 1 >= total_pages:
                    break
            except sources.SourceError as exc:
                failures.append(f"hasło {index}, strona {page + 1}: {exc}")
                break
        if total_pages is not None and retrieved_pages < total_pages and retrieved_pages >= min(pages, _MAX_PAGES):
            failures.append(
                f"hasło {index}: limit stron, pobrano {retrieved_pages} z {total_pages}"
            )
    if location and succeeded:
        failures.insert(0, "lokalizacja jest filtrowana lokalnie; API Capgemini nie otrzymuje filtra miasta")
    return sources._finish_batch("capgemini", by_id, failures, succeeded)


def fetch(name: str, *, keywords: list[str], location: str, pages: int,
          timeout: int, options: dict[str, Any]) -> sources.SourceBatch:
    if not isinstance(options, dict):
        raise sources.SourceError("ustawienia źródła muszą być obiektem")
    try:
        pages = int(pages)
        timeout = int(timeout)
    except (TypeError, ValueError):
        raise sources.SourceError("nieprawidłowy limit stron lub timeout") from None
    if not (1 <= pages <= _MAX_PAGES) or not (2 <= timeout <= 120):
        raise sources.SourceError("nieprawidłowy limit stron lub timeout")
    if name == "solidjobs":
        return _solidjobs(keywords=keywords, location=location, pages=pages, timeout=timeout, options=options)
    if name in {"justjoinit", "rocketjobs", "jobhunt"}:
        return _html_board(name, keywords=keywords, location=location, pages=pages, timeout=timeout, options=options)
    if name == "capgemini":
        return _capgemini(keywords=keywords, location=location, pages=pages, timeout=timeout, options=options)
    raise sources.SourceError("nieobsługiwane źródło job board")
