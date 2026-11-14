"""Ograniczone adaptery HTML dla publicznych tablic pracy.

Moduł pobiera wyłącznie pola potrzebne do listy ofert. Nie utrwala opisów ani
wynagrodzeń. Ten moduł obsługuje Pracuj.pl, the:protocol i JobHunt.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from html.parser import HTMLParser
from typing import Any

from . import dynamic_boards, sources


_USER_AGENT = "JobSearchStarter/0.1"
_MAX_BODY = 4_000_000
_MAX_PAGES = 10

_SITES = {
    "pracuj": {
        "origin": "https://www.pracuj.pl",
        "hosts": {"pracuj.pl", "www.pracuj.pl"},
    },
    "theprotocol": {
        "origin": "https://theprotocol.it",
        "hosts": {"theprotocol.it", "www.theprotocol.it"},
    },
    "jobhunt": {
        "origin": "https://jobhunt.pl",
        "hosts": {"jobhunt.pl", "www.jobhunt.pl"},
    },
}

_SUPPORTED = frozenset(_SITES)
_NO_RESULTS = (
    "nie znaleźliśmy ofert",
    "brak ofert pracy",
    "nie znaleziono ofert",
    "no jobs found",
    "no results found",
)
_BLOCK_MARKERS = (
    "access denied",
    "request blocked",
    "verify you are human",
    "captcha",
    "cloudflare ray id",
    "unusual traffic",
)


@dataclass
class _Page:
    jsonld: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    pracuj_cards: list[dict[str, str]] = field(default_factory=list)
    heading_links: list[dict[str, str]] = field(default_factory=list)
    _script_type: str = ""
    _in_title: bool = False
    _link_attrs: dict[str, str] | None = None
    _link_text: list[str] = field(default_factory=list)
    links: list[tuple[dict[str, str], str]] = field(default_factory=list)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = _Page()
        self._tags: list[tuple[str, dict[str, str]]] = []
        self._anchors: list[dict[str, Any]] = []
        self._heading: tuple[str, list[str]] | None = None
        self._heading_attrs: dict[str, str] = {}
        self._span_parts: list[str] | None = None
        self._pracuj_card: dict[str, str] | None = None
        self._pracuj_card_tile_id: int | None = None
        self._pracuj_tile_id: int | None = None
        self._pracuj_tile_depth: int | None = None
        self._pracuj_tile_tag: str | None = None
        self._pracuj_tile_link = ""
        self._pracuj_tile_counter = 0
        self._pending_pracuj_link = ""
        self._in_script = False

    def _finish_pracuj_card(self) -> None:
        if self._pracuj_card is not None:
            self.page.pracuj_cards.append(self._pracuj_card)
            self._pracuj_card = None
            self._pracuj_card_tile_id = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.casefold(): value or "" for key, value in attrs}
        classes = attrs_map.get("class", "").split()
        is_offer_tile_root = tag.casefold() == "div" and any(
            token == "offer-tile_root" or token.startswith("offer-tile_root_") for token in classes
        )
        starts_card_container = tag.casefold() == "article" or is_offer_tile_root
        if starts_card_container and self._pracuj_tile_id is None:
            if self._pracuj_card is not None:
                self._finish_pracuj_card()
            self._pracuj_tile_counter += 1
            self._pracuj_tile_id = self._pracuj_tile_counter
            self._pracuj_tile_depth = len(self._tags)
            self._pracuj_tile_tag = tag.casefold()
            self._pracuj_tile_link = ""
        if tag.casefold() == "script":
            self._in_script = True
            self.page._script_type = attrs_map.get("type", "").split(";", 1)[0].strip().casefold()
        elif tag.casefold() == "title":
            self.page._in_title = True
        elif tag.casefold() == "a":
            self.page._link_attrs = attrs_map
            self.page._link_text = []
            self._anchors.append({
                "href": attrs_map.get("href", ""),
                "data-test": attrs_map.get("data-test", ""),
                "headings": {},
                "spans": [],
            })
            if self._pracuj_card is not None and attrs_map.get("data-test", "").casefold() in {
                "link-offer-title", "link-offer"
            }:
                if self._pracuj_tile_id is not None:
                    self._pracuj_tile_link = attrs_map.get("href", "")
                    if self._pracuj_card_tile_id == self._pracuj_tile_id:
                        self._pracuj_card["url"] = self._pracuj_tile_link
                else:
                    self._pending_pracuj_link = attrs_map.get("href", "")
            elif attrs_map.get("data-test", "").casefold() in {"link-offer-title", "link-offer"}:
                if self._pracuj_tile_id is not None:
                    self._pracuj_tile_link = attrs_map.get("href", "")
                else:
                    self._pending_pracuj_link = attrs_map.get("href", "")
        elif tag.casefold() in {"h2", "h3", "h4"}:
            if tag.casefold() == "h2" and attrs_map.get("data-test", "").casefold() == "offer-title":
                self._finish_pracuj_card()
                self._pracuj_card = {"title": "", "company": "", "location": "", "url": ""}
                self._pracuj_card_tile_id = self._pracuj_tile_id
                self._pracuj_card["url"] = self._pracuj_tile_link or self._pending_pracuj_link
                self._pending_pracuj_link = ""
                for anchor in reversed(self._anchors):
                    if anchor.get("data-test", "").casefold() in {"link-offer-title", "link-offer"}:
                        self._pracuj_card["url"] = str(anchor.get("href", ""))
                        break
            self._heading = (tag.casefold(), [])
            self._heading_attrs = attrs_map
        elif tag.casefold() == "span":
            self._span_parts = []

        if tag.casefold() not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self._tags.append((tag.casefold(), attrs_map))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "script":
            self._in_script = False
            self.page._script_type = ""
        elif tag == "title":
            self.page._in_title = False
        elif tag == "a" and self.page._link_attrs is not None:
            link_text = " ".join(self.page._link_text)
            self.page.links.append((self.page._link_attrs, link_text))
            self.page._link_attrs = None
            self.page._link_text = []
            if self._anchors:
                anchor = self._anchors.pop()
                title = anchor["headings"].get("h2") or anchor["headings"].get("h3")
                path = urllib.parse.urlsplit(anchor["href"]).path.casefold()
                if not title and path.startswith("/oferta/"):
                    title = link_text
                if title or path.startswith("/oferta/"):
                    self.page.heading_links.append({"title": title, "url": anchor["href"]})
        elif tag in {"h2", "h3", "h4"} and self._heading is not None:
            heading_tag, parts = self._heading
            value = " ".join(" ".join(parts).split())
            if value:
                if self._anchors:
                    self._anchors[-1]["headings"][heading_tag] = value
                if self._pracuj_card is not None:
                    data_test = self._heading_attrs.get("data-test", "").casefold()
                    if data_test == "offer-title":
                        self._pracuj_card["title"] = value
                        if self._anchors and self._anchors[-1].get("data-test", "").casefold() in {
                            "link-offer-title", "link-offer"
                        }:
                            self._pracuj_card["url"] = str(self._anchors[-1].get("href", ""))
                    elif data_test == "text-company-name":
                        self._pracuj_card["company"] = value
                    elif data_test == "text-region":
                        self._pracuj_card["location"] = value
            self._heading = None
            self._heading_attrs = {}
        elif tag == "span" and self._span_parts is not None:
            value = " ".join(" ".join(self._span_parts).split())
            if value and self._anchors:
                self._anchors[-1]["spans"].append(value)
            self._span_parts = None

        if tag == self._pracuj_tile_tag and self._pracuj_tile_depth == len(self._tags) - 1:
            if self._pracuj_card_tile_id == self._pracuj_tile_id:
                self._finish_pracuj_card()
            self._pracuj_tile_id = None
            self._pracuj_tile_depth = None
            self._pracuj_tile_tag = None
            self._pracuj_tile_link = ""

        for index in range(len(self._tags) - 1, -1, -1):
            if self._tags[index][0] == tag:
                del self._tags[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._in_script:
            if self.page._script_type == "application/ld+json":
                self.page.jsonld.append(data)
            return
        cleaned = " ".join(data.split())
        if cleaned:
            self.page.text.append(cleaned)
            if self.page._link_attrs is not None:
                self.page._link_text.append(cleaned)
            if self._heading is not None:
                self._heading[1].append(cleaned)
            elif self._span_parts is not None:
                self._span_parts.append(cleaned)

    def finish(self) -> None:
        if self._pending_pracuj_link and self._pracuj_card is not None and not self._pracuj_card.get("url"):
            self._pracuj_card["url"] = self._pending_pracuj_link
        self._pending_pracuj_link = ""
        self._finish_pracuj_card()


def _valid_source(name: str) -> str:
    name = str(name or "").strip().casefold()
    if name not in _SUPPORTED:
        raise sources.SourceError("nieobsługiwana tablica ofert")
    return name


def _search_url(name: str, keyword: str, location: str) -> str:
    site = _SITES[name]
    encoded = urllib.parse.quote(keyword.strip(), safe="")
    if name == "pracuj":
        # Publiczny wzorzec wyszukiwania używany przez serwis; miasto jest
        # filtrowane później z wyświetlonego pola lokalizacji oferty.
        return f"{site['origin']}/praca/{encoded};kw/polska;ct,1"
    if name == "theprotocol":
        return f"{site['origin']}/filtry/{encoded}"
    query = {"q": keyword.strip()}
    if location.strip():
        query["city"] = location.strip()
    return f"{site['origin']}/?{urllib.parse.urlencode(query)}"


def _safe_url(name: str, value: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(value)
        return (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.hostname.casefold() in _SITES[name]["hosts"]
            and parsed.port in {None, 443}
            and not parsed.username
            and not parsed.password
        )
    except ValueError:
        return False


@lru_cache(maxsize=12)
def _robots_text(origin: str, timeout: int) -> str:
    request = urllib.request.Request(
        origin.rstrip("/") + "/robots.txt",
        headers={"Accept": "text/plain", "User-Agent": _USER_AGENT},
    )
    try:
        opener = urllib.request.build_opener(dynamic_boards._NoRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            if urllib.parse.urlsplit(response.geturl()).netloc.casefold() != urllib.parse.urlsplit(origin).netloc.casefold():
                raise sources.SourceError("przekierowanie robots.txt poza host zostało zablokowane")
            return response.read(256_000).decode("utf-8-sig", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 410}:
            return ""
        raise sources.SourceError(f"robots.txt niedostępny (HTTP {exc.code}); pomijam źródło") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise sources.SourceError("robots.txt niedostępny; pomijam źródło") from None


def _robots_allows(url: str, timeout: int) -> bool:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    origin = f"{parsed.scheme}://{parsed.netloc}"
    body = _robots_text(origin, timeout)
    if not body.strip():
        return True
    groups = dynamic_boards._parse_robots(body)
    return dynamic_boards._robots_can_fetch(groups, url, _USER_AGENT)


def _fetch_html(url: str, timeout: int, *, source: str) -> str:
    if source not in _SITES or not _safe_url(source, url):
        raise sources.SourceError("nieprawidłowy adres tablicy")
    if not _robots_allows(url, timeout):
        raise sources.SourceError("robots.txt zabrania pobrania tej ścieżki")
    request = urllib.request.Request(
        url,
        headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": _USER_AGENT},
    )
    opener = urllib.request.build_opener(dynamic_boards._NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            final_url = response.geturl()
            if not _safe_url(source, final_url):
                raise sources.SourceError("przekierowanie poza domenę tablicy zostało zablokowane")
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read(_MAX_BODY).decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        raise sources.SourceError(f"odpowiedź HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise sources.SourceError("brak połączenia lub przekroczony limit czasu") from None


def _is_jobposting(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    kind = value.get("@type", [])
    kinds = kind if isinstance(kind, list) else [kind]
    return any(str(item).casefold() == "jobposting" for item in kinds)


def _jobpostings(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            if _is_jobposting(node):
                found.append(node)
            for key in ("@graph", "itemListElement", "mainEntity"):
                if key in node:
                    visit(node[key])

    visit(value)
    return found


def _first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), {})
    return value if isinstance(value, dict) else {}


def _location(posting: dict[str, Any]) -> str:
    raw_places = posting.get("jobLocation") or posting.get("jobLocationType")
    places = raw_places if isinstance(raw_places, list) else [raw_places]
    locations: list[str] = []
    seen: set[str] = set()
    for place in places:
        place = place if isinstance(place, dict) else {}
        raw_addresses = place.get("address", place.get("name", ""))
        addresses = raw_addresses if isinstance(raw_addresses, list) else [raw_addresses]
        for address in addresses:
            if isinstance(address, dict):
                locality = address.get("addressLocality") or address.get("name") or ""
                region = address.get("addressRegion") or ""
                country = address.get("addressCountry") or ""
                if isinstance(country, dict):
                    country = country.get("name") or country.get("addressCountry") or ""
                location = ", ".join(str(value).strip() for value in (locality, region, country) if value)
            else:
                location = str(address or "").strip()
            key = location.casefold()
            if location and key not in seen:
                seen.add(key)
                locations.append(location)
    return ", ".join(locations)


def _is_offer_link(name: str, value: str, page_url: str) -> str | None:
    if not isinstance(value, str):
        return None
    absolute = urllib.parse.urljoin(page_url, value)
    if not _safe_url(name, absolute):
        return None
    path = urllib.parse.unquote(urllib.parse.urlsplit(absolute).path).casefold()
    if name == "pracuj":
        valid_path = path.startswith("/praca/") and re.search(r",oferta,[^/,]+$", path) is not None
    elif name == "theprotocol":
        valid_path = (
            path.startswith(("/praca/", "/szczegoly/praca/"))
            and re.search(r",oferta,[^/,]+$", path) is not None
        )
    else:
        valid_path = path.startswith("/oferta/") and len(path.strip("/").split("/")) >= 2
    return absolute if valid_path else None


def _portal_offer_id(name: str, value: str) -> str | None:
    if name not in {"pracuj", "theprotocol"}:
        return None
    try:
        path = urllib.parse.unquote(urllib.parse.urlsplit(value).path)
    except (TypeError, ValueError):
        return None
    match = re.search(r",oferta,([^/,]+)$", path, re.IGNORECASE)
    return match.group(1).casefold() if match else None


def _offer_identity(source: str, url: str) -> str:
    portal_id = _portal_offer_id(source, url)
    if portal_id:
        return f"{source}:portal:{portal_id}"
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((
        parsed.scheme.casefold(),
        parsed.netloc.casefold(),
        parsed.path.rstrip("/") or "/",
        parsed.query,
        "",
    ))


def _merge_rows_by_url(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for row in rows:
        key = _offer_identity(row["source"], row["url"])
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(row)
            continue
        for field_name in ("title", "company", "location"):
            if not existing.get(field_name) and row.get(field_name):
                existing[field_name] = row[field_name]
        match_parts = dict.fromkeys(
            part for value in (existing.get("_match_text", ""), row.get("_match_text", ""))
            for part in value.split() if part
        )
        existing["_match_text"] = " ".join(match_parts).casefold()
    return merged


def _generic_jobhunt_title(value: Any) -> bool:
    title = re.sub(r"\s+", " ", str(value or "").casefold()).strip(" \t\r\n.,:;!?…")
    return title in {
        "", "zobacz", "zobacz ofertę", "zobacz ofertę pracy", "zobacz szczegóły",
        "szczegóły", "szczegóły oferty", "czytaj więcej", "więcej", "więcej informacji",
        "przejdź", "przejdź do oferty", "sprawdź ofertę", "oferta", "praca",
        "details", "view offer", "see offer", "view job", "read more", "more", "apply",
    }


def _rows(name: str, page: _Page, page_url: str) -> tuple[list[dict[str, str]], int, str]:
    joined = " ".join(page.text).casefold()

    postings: list[dict[str, Any]] = []
    malformed = False
    for raw in page.jsonld:
        try:
            postings.extend(_jobpostings(json.loads(raw)))
        except json.JSONDecodeError:
            malformed = True
    if malformed:
        raise sources.SourceError("strona zawiera nieprawidłowe dane JSON-LD")

    rows: list[dict[str, str]] = []
    for item in postings:
        organization = _first_dict(item.get("hiringOrganization"))
        company = organization.get("name", "")
        identifier = item.get("identifier")
        if isinstance(identifier, dict):
            identifier = identifier.get("value") or identifier.get("name")
        offer_url = item.get("url") or item.get("sameAs") or ""
        if isinstance(offer_url, dict):
            offer_url = offer_url.get("url", "")
        offer_url = _is_offer_link(name, offer_url, page_url)
        portal_id = _portal_offer_id(name, offer_url) if offer_url else None
        row = sources._normalized(
            name,
            "public",
            raw_id=portal_id or identifier,
            title=item.get("title"),
            company=company,
            location=_location(item),
            url=offer_url,
            # Opisy i wynagrodzenia nie są przekazywane ani zapisywane.
            match_text="",
        )
        if row:
            rows.append(row)

    markup_rows: list[dict[str, str]] = []
    generic_jobhunt_links = 0
    if name == "pracuj":
        for card in page.pracuj_cards:
            offer_url = _is_offer_link(name, card.get("url", ""), page_url)
            row = sources._normalized(
                name,
                "public",
                raw_id=_portal_offer_id(name, offer_url) if offer_url else None,
                title=card.get("title"),
                company=card.get("company"),
                location=card.get("location"),
                url=offer_url,
            )
            if row:
                markup_rows.append(row)
    elif name in {"theprotocol", "jobhunt"}:
        for card in page.heading_links:
            offer_url = _is_offer_link(name, card.get("url", ""), page_url)
            if name == "jobhunt" and offer_url and _generic_jobhunt_title(card.get("title")):
                generic_jobhunt_links += 1
                continue
            row = sources._normalized(
                name,
                "public",
                raw_id=_portal_offer_id(name, offer_url) if offer_url else None,
                title=card.get("title"),
                company="",
                location="",
                url=offer_url,
            )
            if row:
                markup_rows.append(row)
    rows.extend(markup_rows)
    rows = list(_merge_rows_by_url(rows).values())

    if generic_jobhunt_links and not rows:
        raise sources.SourceError("link do oferty jest widoczny, ale jego tytuł jest pusty lub niejednoznaczny")
    if postings and not rows:
        raise sources.SourceError("JSON-LD zawiera oferty, ale nie znaleziono prawidłowych URL-i ofert")
    if rows:
        warning = "pominięto link do oferty z niejednoznaczną etykietą" if generic_jobhunt_links else ""
        return rows, len(rows), warning
    if any(marker in joined for marker in _BLOCK_MARKERS):
        raise sources.SourceError("strona zgłosiła blokadę lub weryfikację dostępu")
    if any(marker in joined for marker in _NO_RESULTS):
        return [], 0, ""
    raise sources.SourceError("nieobsługiwany HTML: nie wykryto ofert JSON-LD ani potwierdzonego pustego wyniku")


def _next_page(name: str, page: _Page, page_url: str, page_index: int) -> str | None:
    for attrs, label in page.links:
        rel = {value.casefold() for value in attrs.get("rel", "").split()}
        if "next" not in rel:
            continue
        href = urllib.parse.urljoin(page_url, attrs.get("href", ""))
        if _safe_url(name, href):
            return href
    if name == "pracuj":
        # Pracuj.pl public search results use the `pn` query parameter for
        # numbered pages. The name is taken from visible listing URLs.
        parsed = urllib.parse.urlsplit(page_url)
        query = [(key, value) for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                 if key != "pn"]
        query.append(("pn", str(page_index + 1)))
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                                       urllib.parse.urlencode(query), ""))
    return None


def fetch(name: str, *, keywords: list[str], location: str, pages: int,
          timeout: int, options: dict[str, Any]) -> sources.SourceBatch:
    name = _valid_source(name)
    if not isinstance(keywords, list) or not any(str(word).strip() for word in keywords):
        raise sources.SourceError("wpisz co najmniej jedno hasło wyszukiwania")
    try:
        page_limit = int(pages)
        timeout = int(timeout)
    except (TypeError, ValueError):
        raise sources.SourceError("nieprawidłowy limit stron lub czasu") from None
    if not (1 <= page_limit <= _MAX_PAGES) or timeout < 1:
        raise sources.SourceError("nieprawidłowy limit stron lub czasu")

    offers: dict[str, dict[str, str]] = {}
    failures: list[str] = []
    warnings: list[str] = []
    succeeded = 0
    for keyword_index, raw_keyword in enumerate(keywords, start=1):
        keyword = str(raw_keyword).strip()
        if not keyword:
            continue
        page_url = _search_url(name, keyword, str(location or ""))
        seen_page_id_sets: set[frozenset[str]] = set()
        for page_index in range(1, page_limit + 1):
            try:
                page_html = _fetch_html(page_url, timeout, source=name)
                parsed = _PageParser()
                parsed.feed(page_html)
                parsed.finish()
                rows, result_count, row_warning = _rows(name, parsed.page, page_url)
                if row_warning:
                    warnings.append(f"hasło {keyword_index}, strona {page_index}: {row_warning}")
                succeeded += 1
                page_ids = frozenset(row["id"] for row in rows)
                offers.update(_merge_rows_by_url([*offers.values(), *rows]))
                if result_count == 0:
                    break
                if page_ids in seen_page_id_sets:
                    warnings.append(
                        f"hasło {keyword_index}: powtórzony zbiór ofert na stronie {page_index}; paginacja zatrzymana"
                    )
                    break
                seen_page_id_sets.add(page_ids)
                next_url = _next_page(name, parsed.page, page_url, page_index)
                if not next_url or next_url == page_url:
                    break
                if page_index == page_limit:
                    warnings.append(
                        f"hasło {keyword_index}: osiągnięto limit {page_limit} stron; wyniki mogą być ucięte"
                    )
                    break
                page_url = next_url
            except sources.SourceError as exc:
                failures.append(f"hasło {keyword_index}, strona {page_index}: {exc}")
                break
            except (ValueError, UnicodeError):
                failures.append(f"hasło {keyword_index}, strona {page_index}: nieprawidłowy format HTML")
                break
    location_warning = "lokalizacja nie jest dostępna w rozpoznanych polach; główny filtr lokalizacji może odrzucić te oferty"
    if str(location or "").strip() and name in {"pracuj", "theprotocol", "jobhunt"} and offers:
        if any(not offer.get("location") for offer in offers.values()):
            warnings.append(location_warning)
    batch = sources._finish_batch(name, offers, failures, succeeded)
    if warnings:
        visible_warnings = list(dict.fromkeys(warnings))
        if location_warning in visible_warnings and location_warning not in visible_warnings[:4]:
            visible_warnings = visible_warnings[:3] + [location_warning]
        warning_text = "ostrzeżenie: " + "; ".join(visible_warnings[:4])
        batch.error = f"{batch.error}; {warning_text}" if batch.error else warning_text
    return batch
