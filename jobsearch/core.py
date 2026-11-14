"""Walidacja konfiguracji, zbieranie, dopasowanie i trwały stan ofert."""
from __future__ import annotations
import json
import os
import tempfile
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import tomllib

from . import sources

OFFER_KEYS = ("id", "title", "company", "location", "url", "source", "first_seen", "last_seen")

class ConfigError(Exception):
    pass

class CollectionError(Exception):
    pass

def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)

def iso_utc(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat().replace("+00:00", "Z")

def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise

def load_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"nie znaleziono pliku konfiguracji: {path}") from None
    except (OSError, tomllib.TOMLDecodeError):
        raise ConfigError("nie można odczytać konfiguracji TOML") from None
    search = config.get("search")
    if not isinstance(search, dict):
        raise ConfigError("brak sekcji [search]")
    keywords = search.get("keywords", [])
    if not isinstance(keywords, list) or not all(isinstance(x, str) for x in keywords):
        raise ConfigError("search.keywords musi być listą tekstów")
    if not any(x.strip() for x in keywords):
        raise ConfigError("dodaj co najmniej jedno hasło w search.keywords")
    exclude = search.get("exclude_keywords", [])
    if not isinstance(exclude, list) or not all(isinstance(x, str) for x in exclude):
        raise ConfigError("search.exclude_keywords musi być listą tekstów")
    country = str(search.get("country", "pl")).strip().lower()
    if len(country) != 2 or not country.isascii() or not country.isalpha():
        raise ConfigError("search.country musi być dwuliterowym kodem kraju")
    collection = config.get("collection", {})
    try:
        timeout = int(collection.get("request_timeout", 20))
        pages = int(collection.get("max_pages", 1))
        history_days = int(collection.get("history_days", 180))
    except (ValueError, TypeError, AttributeError):
        raise ConfigError("ustawienia [collection] muszą być liczbami") from None
    if not (2 <= timeout <= 120 and 1 <= pages <= 10 and 1 <= history_days <= 3650):
        raise ConfigError("collection: timeout 2-120 s, strony 1-10, historia 1-3650 dni")
    sources_cfg = config.get("sources", {})
    if not isinstance(sources_cfg, dict):
        raise ConfigError("sekcja [sources] musi zawierać źródła")
    return {
        "search": {"country": country, "location": str(search.get("location", "")).strip(),
            "keywords": [x.strip().casefold() for x in keywords if x.strip()],
            "exclude_keywords": [x.strip().casefold() for x in exclude if x.strip()]},
        "collection": {"request_timeout": timeout, "max_pages": pages, "history_days": history_days},
        "sources": sources_cfg, "email": config.get("email", {}),
    }

def _enabled(cfg: Any) -> bool:
    return isinstance(cfg, dict) and cfg.get("enabled") is True

def _location_key(value: str) -> str:
    folded = value.casefold().replace("ł", "l")
    return "".join(char for char in unicodedata.normalize("NFKD", folded)
                   if not unicodedata.combining(char))

def collect(config: dict[str, Any], env: dict[str, str] | None = None) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    env = os.environ if env is None else env
    search, collection = config["search"], config["collection"]
    source_cfg = config["sources"]
    dynamic_names = ("justjoinit", "rocketjobs", "solidjobs", "capgemini")
    html_names = ("nofluffjobs", "bulldogjob", "theprotocol", "pracuj", "jobsora", "jobhunt", "czyjesteldorado")
    enabled = [(name, source_cfg.get(name, {})) for name in ("adzuna", "lever", "greenhouse") + dynamic_names + html_names
               if _enabled(source_cfg.get(name, {}))]
    if not enabled:
        raise CollectionError("włącz co najmniej jedno źródło w sekcji [sources.*]")
    batches: list[dict[str, Any]] = []
    raw_offers: list[dict[str, str]] = []
    for name, cfg in enabled:
        try:
            partial_error = None
            if name == "adzuna":
                result = sources.fetch_adzuna(country=search["country"], keywords=search["keywords"],
                    location=search["location"], pages=collection["max_pages"],
                    timeout=collection["request_timeout"], app_id=env.get("ADZUNA_APP_ID", ""),
                    app_key=env.get("ADZUNA_APP_KEY", ""))
            elif name == "lever":
                boards = cfg.get("boards", [])
                if not isinstance(boards, list) or not boards:
                    raise sources.SourceError("uzupełnij listę identyfikatorów boards")
                result = sources.fetch_lever(boards=boards, timeout=collection["request_timeout"],
                                             region=cfg.get("region", "global"))
            elif name == "greenhouse":
                boards = cfg.get("boards", [])
                if not isinstance(boards, list) or not boards:
                    raise sources.SourceError("uzupełnij listę identyfikatorów boards")
                result = sources.fetch_greenhouse(boards=boards, timeout=collection["request_timeout"])
            else:
                if name in dynamic_names:
                    from . import dynamic_boards as board_adapter
                elif name in {"nofluffjobs", "bulldogjob"}:
                    from . import nfj_bulldog as board_adapter
                elif name in {"jobsora", "czyjesteldorado"}:
                    from . import jobsora_eldorado as board_adapter
                else:
                    from . import html_boards as board_adapter
                result = board_adapter.fetch(name, keywords=search["keywords"],
                    location=search["location"], pages=collection["max_pages"],
                    timeout=collection["request_timeout"], options=cfg)
            if isinstance(result, sources.SourceBatch):
                items, partial_error = result.offers, result.error
            else:
                items = result
            matched = []
            for item in items:
                text = item.get("_match_text", "").casefold()
                if not any(word in text for word in search["keywords"]):
                    continue
                if any(word in text for word in search["exclude_keywords"]):
                    continue
                target = _location_key(search["location"])
                if target and item.get("source") != "adzuna" and target not in _location_key(item["location"]):
                    continue
                matched.append({key: item.get(key, "") for key in
                               ("id", "title", "company", "location", "url", "source")})
            raw_offers.extend(matched)
            batches.append({"source": name, "status": "warning" if partial_error else "ok",
                            "count": len(matched), "message": partial_error or ""})

        except sources.SourceError as exc:
            batches.append({"source": name, "status": "error", "count": 0, "message": str(exc)})
        except Exception:
            batches.append({"source": name, "status": "error", "count": 0, "message": "nieoczekiwany błąd źródła"})
    if batches and all(batch["status"] == "error" for batch in batches):
        detail = "; ".join(f"{x['source']}: {x['message']}" for x in batches)
        raise CollectionError(f"żadne źródło nie zadziałało — stan nie został zmieniony ({detail})")
    dedup: dict[str, dict[str, str]] = {}
    for item in raw_offers:
        dedup[item["id"]] = item
    return list(dedup.values()), batches

def _public_offer(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    offer = {key: str(raw.get(key, "")) for key in OFFER_KEYS}
    allow_http = offer["source"] == "adzuna"
    safe_url = sources.clean_url(offer["url"], allow_http=allow_http)
    if not offer["id"] or not offer["title"] or safe_url != offer["url"]:
        return None
    offer["url"] = safe_url or ""
    return offer

def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "last_run": None, "offers": [], "pending_email": [], "pending_email_offers": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise CollectionError("plik stanu jest nieprawidłowy; zachowano go bez zmian") from None
    if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("offers"), list):
        raise CollectionError("nieobsługiwany format pliku stanu; zachowano go bez zmian")
    offers = [_public_offer(x) for x in state["offers"]]
    if any(x is None for x in offers):
        raise CollectionError("plik stanu zawiera nieprawidłowy wpis; zachowano go bez zmian")
    pending, pending_offers = state.get("pending_email", []), state.get("pending_email_offers", [])
    if not isinstance(pending, list) or not isinstance(pending_offers, list):
        raise CollectionError("nieprawidłowy stan kolejki email; zachowano plik")
    public_pending = [_public_offer(x) for x in pending_offers]
    if any(x is None for x in public_pending):
        raise CollectionError("nieprawidłowy wpis kolejki email; zachowano plik")
    return {"version": 1, "last_run": state.get("last_run"), "offers": offers,
            "pending_email": [str(x) for x in pending if isinstance(x, str)],
            "pending_email_offers": public_pending}

def run_collection(config: dict[str, Any], state_path: Path, new_path: Path,
                   summary_path: Path, env: dict[str, str] | None = None,
                   now: datetime | None = None) -> dict[str, Any]:
    state = load_state(state_path)
    incoming, statuses = collect(config, env)
    moment = now or utc_now()
    stamp = iso_utc(moment)
    previous = {offer["id"]: offer for offer in state["offers"]}
    fresh: list[dict[str, str]] = []
    merged = dict(previous)
    for raw in incoming:
        job_id = raw["id"]
        if job_id in previous:
            offer = dict(previous[job_id])
            offer.update({key: raw.get(key, "") for key in ("title","company","location","url","source")})
            offer["last_seen"] = stamp
        else:
            offer = {key: raw.get(key, "") for key in ("id","title","company","location","url","source")}
            offer.update({"first_seen":stamp, "last_seen":stamp})
            fresh.append(offer)
        merged[job_id] = offer
    cutoff = moment - timedelta(days=config["collection"]["history_days"])
    retained = {}
    for job_id, offer in merged.items():
        try:
            last_seen = datetime.fromisoformat(offer["last_seen"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            last_seen = moment
        if last_seen >= cutoff or job_id in state["pending_email"]:
            retained[job_id] = offer
    all_offers = sorted(retained.values(), key=lambda x: (x["first_seen"], x["id"]), reverse=True)
    email_enabled = _enabled(config.get("email", {}))
    pending_by_id = {offer["id"]: offer for offer in state["pending_email_offers"]}
    if email_enabled:
        for offer in fresh:
            pending_by_id[offer["id"]] = offer
    current_new = {offer["id"]: offer for offer in fresh}
    if email_enabled:
        current_new.update(pending_by_id)
    current_new_list = sorted(current_new.values(), key=lambda x:(x["first_seen"], x["id"]), reverse=True)
    state_out = {"version":1, "last_run":stamp, "offers":all_offers,
                 "pending_email":list(pending_by_id) if email_enabled else [],
                 "pending_email_offers":list(pending_by_id.values()) if email_enabled else []}
    summary = {"last_run":stamp,"source_status":statuses,"fetched":len(incoming),
               "new":len(fresh),"total":len(all_offers)}
    atomic_write_json(state_path, state_out)
    atomic_write_json(new_path, current_new_list)
    atomic_write_json(summary_path, summary)
    return summary

def clear_pending_email(state_path: Path) -> None:
    state = load_state(state_path)
    state["pending_email"] = []
    state["pending_email_offers"] = []
    atomic_write_json(state_path, state)

def load_new_jobs(path: Path) -> list[dict[str, str]]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise CollectionError("nie można odczytać listy nowych ofert") from None
    if not isinstance(rows, list):
        raise CollectionError("lista nowych ofert musi być tablicą JSON")
    offers = [_public_offer(x) for x in rows]
    if any(x is None for x in offers):
        raise CollectionError("lista nowych ofert zawiera nieprawidłowy wpis")
    seen = set()
    return [x for x in offers if not (x["id"] in seen or seen.add(x["id"]))]
