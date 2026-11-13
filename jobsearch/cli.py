"""Interfejs wiersza poleceń projektu."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
from .core import (CollectionError, ConfigError, clear_pending_email, load_config,
                   load_new_jobs, load_state, run_collection)
from .dashboard import write_dashboard
from .emailer import EmailError, send_new_jobs

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobsearch", description="Zbieraj i przeglądaj publiczne oferty pracy.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="zbierz oferty, zaktualizuj stan i wygeneruj dashboard")
    run.add_argument("--config", type=Path, default=Path("config.toml"))
    run.add_argument("--state", type=Path, default=Path("data/jobs.json"))
    run.add_argument("--new-jobs", type=Path, default=Path("output/new-jobs.json"))
    run.add_argument("--summary", type=Path, default=Path("output/summary.json"))
    run.add_argument("--dashboard", type=Path, default=Path("output/dashboard.html"))
    email = commands.add_parser("email", help="wyślij kolejkę nowych ofert po zapisaniu stanu")
    email.add_argument("--config", type=Path, default=Path("config.toml"))
    email.add_argument("--state", type=Path, default=Path("data/jobs.json"))
    email.add_argument("--new-jobs", type=Path, default=Path("output/new-jobs.json"))
    return parser

def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "run":
            summary = run_collection(config, args.state, args.new_jobs, args.summary)
            write_dashboard(args.dashboard, load_state(args.state)["offers"], summary["last_run"])
            print(f"Uruchomienie: {summary['last_run']}; nowe: {summary['new']}; razem: {summary['total']}")
            for status in summary["source_status"]:
                print(f"{status['source']}: {status['status']} ({status['count']})"
                      + (f" — {status['message']}" if status["message"] else ""))
            return 0
        if not config["email"].get("enabled", False):
            print("Email wyłączony w konfiguracji; nie wysłano wiadomości.")
            return 0
        offers = load_new_jobs(args.new_jobs)
        if not offers and not config["email"].get("send_empty", False):
            print("Brak nowych ofert; wiadomość nie została wysłana.")
            return 0
        outcome = send_new_jobs(offers)
        clear_pending_email(args.state)
        print(outcome)
        return 0
    except (ConfigError, CollectionError, EmailError) as exc:
        print(f"Błąd: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("Błąd pliku lub katalogu; sprawdź ścieżki i uprawnienia.", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
