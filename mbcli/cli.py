"""Command-line entry point for mbcli."""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import sys
import threading
import time
from datetime import datetime, timezone

from . import DASHBOARD_URL, __version__
from .credentials import (
    clear_credentials,
    credentials_path,
    load_credentials,
    save_credentials,
)
from .api import (
    ApiError,
    fetch_status,
    load_api_session,
    load_vehicles,
    save_vehicles,
)
from .extract import (
    Vehicle,
    find_status_json,
    flatten_responses,
    parse_live_data,
    vehicles_from_responses,
)
from .paths import captures_dir, ensure_parent, session_marker
from .render import render_statuses, vehicle_kind
from .session import SessionError, capture, login


def _save_raw(responses) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = captures_dir() / f"capture-{stamp}.json"
    ensure_parent(path)
    bundle = [{"url": r.url, "status": r.status, "json": r.json} for r in responses]
    path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False))
    return str(path)


def cmd_login(args: argparse.Namespace) -> int:
    try:
        login(autofill=not args.no_autofill)
    except SessionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("\nSigned in. You can now run `mbcli status`.")
    return 0


def cmd_creds(args: argparse.Namespace) -> int:
    action = args.creds_action
    path = credentials_path()
    if action == "set":
        username = args.username or input("Mercedes me username/email: ").strip()
        if not username:
            print("error: username is required", file=sys.stderr)
            return 1
        if args.username_only:
            password = ""
        else:
            password = getpass.getpass(
                "Mercedes me password (leave blank to type it in the browser): "
            )
        dest = save_credentials(username, password)
        print(f"Credentials saved to {dest} (permissions 0600).")
        if password:
            print("`mbcli login` will auto-fill email & password; you still complete OTP.")
        else:
            print("`mbcli login` will auto-fill your email and click Next; "
                  "type your password (and OTP) in the browser.")
        return 0
    if action == "show":
        creds = load_credentials()
        if not creds:
            print(f"No credentials found (looked at {path} and env vars).")
            return 1
        print(f"username: {creds.username}")
        if creds.password:
            print(f"password: {'*' * max(len(creds.password), 6)}")
        else:
            print("password: (not stored — typed in the browser at login)")
        return 0
    if action == "clear":
        removed = clear_credentials()
        print("Credentials removed." if removed else f"Nothing to remove at {path}.")
        return 0
    if action == "path":
        print(path)
        return 0
    return 1


@contextlib.contextmanager
def _spinner(message: str):
    """Animate '<message> (Ns)' on stderr while the wrapped block runs.

    No-op (single static line) when stderr isn't a TTY, so piped output stays
    clean.
    """
    if not sys.stderr.isatty():
        print(message, file=sys.stderr)
        yield
        return

    stop = threading.Event()
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def run() -> None:
        start = time.monotonic()
        i = 0
        while not stop.wait(0.1):
            elapsed = time.monotonic() - start
            sys.stderr.write(f"\r{frames[i % len(frames)]} {message} ({elapsed:.0f}s)")
            sys.stderr.flush()
            i += 1

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)
        sys.stderr.write("\r\033[K")  # clear the spinner line
        sys.stderr.flush()


def _capture_or_login(url: str, headless: bool):
    """Capture the dashboard, transparently running the interactive sign-in if
    there is no session yet (or it expired). The first login prompts for
    credentials and saves them; later ones auto-fill."""
    if not session_marker().exists():
        print("No session yet — opening Chrome to sign in. "
              "Complete the OTP; it continues automatically.\n", file=sys.stderr)
        login()
    try:
        with _spinner("Reading your dashboard…"):
            return capture(url=url, headless=headless)
    except SessionError as e:
        if "login" in str(e).lower():
            print("Session lapsed — opening Chrome to sign in again. "
                  "Complete the OTP; it continues automatically.\n", file=sys.stderr)
            login()
            with _spinner("Reading your dashboard…"):
                return capture(url=url, headless=headless)
        raise


def _fetch_statuses(headers: dict, vehicles: list[Vehicle]):
    """API-fetch live status for each vehicle. Returns (statuses, raw responses).
    Raises ApiError if any call fails (e.g. expired token)."""
    statuses, raw = [], []
    for v in vehicles:
        resp = fetch_status(headers, finorvin=v.selector)
        raw.append(resp)
        statuses.append(parse_live_data(resp.json, v))
    return statuses, raw


def _collect_statuses(url: str, headless: bool):
    """Status for every vehicle via the fast API path, falling back to a
    browser login/refresh when there's no token, no cached list, or it expired.
    Returns (list[VehicleStatus], raw responses)."""
    headers = load_api_session()
    cached = load_vehicles()
    if headers and cached and session_marker().exists():
        try:
            vehicles = [Vehicle(**v) for v in cached]
            with _spinner("Reading vehicle status…"):
                return _fetch_statuses(headers, vehicles)
        except ApiError:
            pass  # token expired — refresh via the browser below

    result = _capture_or_login(url, headless=headless)  # refreshes token
    headers = load_api_session()
    vehicles = vehicles_from_responses(result.responses)
    if vehicles:
        save_vehicles([v.as_dict() for v in vehicles])
    if headers and vehicles:
        with _spinner("Reading vehicle status…"):
            return _fetch_statuses(headers, vehicles)

    # Last resort: use whatever the browser captured (primary vehicle only).
    st = parse_live_data(find_status_json(result.responses),
                         vehicles[0] if vehicles else None)
    return ([st] if st.found else []), list(result.responses)


def _status_json(st, *, hide: bool = False) -> dict:
    def pair(p):
        return None if not p or p[0] is None else {"value": p[0], "unit": p[1]}
    v = st.vehicle
    vehicle = ({"type": vehicle_kind(st)} if hide
               else {"name": v.name, "vin": v.vin, "model": v.model})
    return {
        "vehicle": vehicle,
        "state_of_charge": pair(st.soc),
        "electric_range": pair(st.electric_range),
        "fuel_level": pair(st.fuel_level),
        "fuel_range": pair(st.fuel_range),
        "odometer": pair(st.mileage),
        "tires": st.tires,
        "last_update": st.last_update,
    }


def cmd_status(args: argparse.Namespace) -> int:
    # Model/VIN are hidden by default for privacy; --vin reveals them.
    hide = not args.vin
    try:
        statuses, raw = _collect_statuses(args.url, headless=not args.headful)
    except SessionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not statuses:
        print("error: no vehicle status found.", file=sys.stderr)
        return 1

    if args.vehicle:
        matched = [s for s in statuses if s.vehicle.name
                   and args.vehicle.lower() in s.vehicle.name.lower()]
        if matched:
            statuses = matched
        else:
            print(f"warning: no vehicle matched {args.vehicle!r}; showing all.",
                  file=sys.stderr)

    if args.save_raw:
        path = _save_raw(raw)
        print(f"(raw capture saved to {path})\n", file=sys.stderr)

    if args.json:
        print(json.dumps([_status_json(s, hide=hide) for s in statuses],
                         indent=2, ensure_ascii=False))
        return 0

    print(render_statuses(statuses, hide=hide))
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    try:
        result = _capture_or_login(args.url, headless=not args.headful)
    except SessionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    path = _save_raw(result.responses)
    flat = flatten_responses(result.responses)

    print(f"Captured {len(result.responses)} JSON responses "
          f"({len(flat)} leaf fields). Raw bundle: {path}\n",
          file=sys.stderr)

    term = (args.grep or "").lower()
    want_value = (args.value or "").strip().lower()
    shown = 0
    for key in sorted(flat):
        value = flat[key]
        sval = str(value).lower()
        if term and term not in key.lower() and term not in sval:
            continue
        if want_value and want_value != sval and want_value not in sval:
            continue
        print(f"{key} = {value!r}")
        shown += 1
    if shown == 0 and (term or want_value):
        crit = args.value if want_value else args.grep
        print(f"(no fields matched {crit!r})", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    # Shared status flags, so they work both as `mbcli --vin` (bare → status)
    # and `mbcli status --vin`.
    status_args = argparse.ArgumentParser(add_help=False)
    status_args.add_argument("--url", default=DASHBOARD_URL)
    status_args.add_argument("--vehicle", help='limit to a vehicle by name, e.g. "EQB 300"')
    status_args.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    status_args.add_argument("--vin", action="store_true",
                             help="show model & VIN (hidden by default — shows a generic type)")
    status_args.add_argument("--save-raw", action="store_true", help="also save the raw capture bundle")
    status_args.add_argument("--headful", action="store_true", help="run with a visible browser (debug)")

    p = argparse.ArgumentParser(
        prog="mbcli",
        description="Read Mercedes me vehicle status via a saved browser session.",
        parents=[status_args],
    )
    p.add_argument("--version", action="version", version=f"mbcli {__version__}")
    # Bare `mbcli` behaves like `mbcli status` (the primary command).
    p.set_defaults(func=cmd_status)
    sub = p.add_subparsers(dest="command")

    pl = sub.add_parser("login", help="open your Chrome to sign in (persistent profile)")
    pl.add_argument("--no-autofill", action="store_true",
                    help="do not auto-fill stored credentials")
    pl.set_defaults(func=cmd_login)

    pc = sub.add_parser("creds", help="manage stored login credentials (0600 file)")
    csub = pc.add_subparsers(dest="creds_action", required=True)
    cset = csub.add_parser("set", help="store username (password via secure prompt, optional)")
    cset.add_argument("--username", help="username/email")
    cset.add_argument("--username-only", action="store_true",
                      help="store just the email; type the password in the browser")
    csub.add_parser("show", help="show stored username (password masked)")
    csub.add_parser("clear", help="delete the stored credentials file")
    csub.add_parser("path", help="print the credentials file path")
    pc.set_defaults(func=cmd_creds)

    ps = sub.add_parser("status", parents=[status_args],
                        help="print vehicle status (SoC, range, mileage, tires)")
    ps.set_defaults(func=cmd_status)

    pd = sub.add_parser("discover", help="dump captured JSON fields to find endpoints/fields")
    pd.add_argument("--url", default=DASHBOARD_URL)
    pd.add_argument("--grep", help="only show fields whose key or value contains TERM")
    pd.add_argument("--value", help="find fields holding VALUE (e.g. 15126 -> mileage field)")
    pd.add_argument("--headful", action="store_true", help="run with a visible browser (debug)")
    pd.set_defaults(func=cmd_discover)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
