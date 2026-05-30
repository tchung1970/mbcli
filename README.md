# mbcli

Read your **Mercedes me** vehicle status — state of charge / fuel, range,
mileage, and tire pressure — from the command line, for every car on your
account.

By default the model and VIN are hidden — each car shows a generic type. Add
`--vin` to reveal the real name and VIN.

```text
$ mbcli
Electric Vehicle
  State of charge : 62%
  Electric range  : 146 mi
  Odometer        : 15126 mi
  Tire pressure:
    Front Left   47.5 PSI
    Front Right  47.9 PSI
    Rear Left    47.9 PSI
    Rear Right   46.0 PSI
  Updated         : 2026-05-29 01:28 PM

Gas Vehicle
  Fuel level      : 59%
  Fuel range      : 205 mi
  Odometer        : 53307 mi
  Tire pressure:
    Front Left   36.3 PSI
    ...
  Updated         : 2026-05-29 01:15 PM
```

The official Mercedes developer API is gated to EU companies with a VAT ID, so
mbcli takes a different route. You sign in **once** through your real Google
Chrome (handling the OTP yourself); mbcli captures the dashboard's API token and
then calls the Mercedes JSON API directly on later runs — typically **~2–3
seconds**, no browser window.

> Personal automation against your own account. The login lives in a dedicated
> Chrome profile and the captured token is stored locally — treat both like
> passwords (see [Data & security](#data--security)).

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
python -m playwright install chromium   # fallback browser; mbcli prefers your real Chrome
```

Put `mbcli` on your `PATH` (the editable install creates the launcher in the
venv). For a global shortcut:

```bash
ln -sf "$(pwd)/.venv/bin/mbcli" ~/.local/bin/mbcli
```

## Usage

### `mbcli` / `mbcli status` — read your vehicles

```bash
mbcli                        # same as `mbcli status`; shows every car (model/VIN hidden)
mbcli --vin                  # reveal each car's real name & VIN
mbcli --vehicle "EQB"        # just one car (substring match on the real name)
mbcli --json                 # machine-readable (a JSON array, one object per car)
```

- **First run** opens Chrome so you can sign in (see below), then caches the
  token + vehicle list.
- **Later runs** (~2–3s) call the API directly — one request per car. If the
  token has expired it falls back to the browser to refresh, then resumes.

Flags (work as `mbcli <flag>` or `mbcli status <flag>`):

| Flag | Effect |
|------|--------|
| `--vehicle NAME` | Only the car whose name contains NAME (matches the real name) |
| `--vin` | Show the real model & VIN (hidden by default; otherwise a generic Electric/Gas/Hybrid Vehicle type is shown) |
| `--json` | Emit a JSON array (one object per vehicle) |
| `--save-raw` | Also save the raw API JSON to `~/.config/mbcli/captures/` |
| `--headful` | Run the browser fallback visibly (debugging) |

### Signing in

```bash
mbcli login                 # opens your Chrome; sign in + OTP
mbcli login --no-autofill   # never auto-fill stored credentials
```

Your real Google Chrome opens with a dedicated profile. Complete the sign-in and
OTP **in that window** — mbcli auto-detects success and continues (no Enter
needed). The profile keeps the session, so you rarely sign in again. You don't
have to run `login` explicitly: `mbcli` opens it for you the first time or
whenever the session lapses.

> **Why a separate Chrome window and not a tab in your everyday Chrome?**
> Chrome 136+ blocks automation from attaching to your *default* profile (an
> anti-cookie-theft measure), so mbcli uses its own persistent profile. The
> window only appears for sign-in.

### `mbcli creds` — optional stored credentials

By default you type your email/password into the Chrome window. To pre-fill them:

```bash
mbcli creds set --username you@example.com                  # password via hidden prompt
mbcli creds set --username you@example.com --username-only  # store email only
mbcli creds show | path | clear
```

Stored at `~/.config/mbcli/credentials.json` (`0600`). The `MBCLI_USERNAME` /
`MBCLI_PASSWORD` env vars take precedence. Credentials are only ever typed into
the visible Chrome window; **OTP is always completed by you**.

### `mbcli discover` — inspect the raw API JSON

For debugging or extending the parser. Always runs the browser path and saves a
full JSON bundle to `~/.config/mbcli/captures/`.

```bash
mbcli discover                 # dump every captured field
mbcli discover --grep charge   # only fields whose key/value contains "charge"
mbcli discover --value 15126   # find which field holds a known value
```

## How it works

mbcli has two paths to the same data, sharing one parser/renderer:

1. **Fast path (default)** — `mbcli/api.py` replays the captured
   `Authorization: Bearer …` token against the JSON API over plain HTTP. It
   fetches the live status **once per car**, selecting each with the
   `x-me-finorvin` header (its VIN).
2. **Browser path (fallback / first run)** — `mbcli/session.py` drives your real
   Chrome (`channel="chrome"`) with a persistent profile, loads the dashboard,
   and captures the API responses **and** the bearer token (refreshing the fast
   path). Runs with HTTP/2 disabled and the automation fingerprint hidden so the
   login's reCAPTCHA doesn't block sign-in; cleans up orphaned Chrome/locks
   before launching.

Endpoints (on `api.oneweb.mercedes-benz.com`):

- `me/vsc/v1/user/vehicles` — vehicle list: `vehicleName`, `vin`, `fin`, `order`
- `me/vsc/v1/user/vehicle/status-information` — for the vehicle named by
  `x-me-finorvin`: `liveData.{mileage, levels (type ELECTRIC → SoC, else fuel),
  ranges (type ELECTRIC → electric, else fuel), tires (type → position), …}`

`mbcli/extract.py` — `parse_vehicles` builds the car list, `parse_live_data`
maps one `liveData` block to a `VehicleStatus`. `mbcli/render.py` formats it
(units normalized, epoch timestamp → local 12-hour time). The vehicle list is
cached so the fast path can label cars without re-fetching it. `flatten` /
`flatten_responses` back the `discover` command.

## Data scope — what's available (and what isn't)

mbcli rides the **web** dashboard's API, which serves only *live data*
(`useCase: DISPLAY_LIVEDATA`):

- ✅ State of charge / fuel level, electric/fuel range, odometer, tire
  pressures, brake-fluid warning.
- ❌ **Lock / door / window / trunk / hood / charging-flap status.** The iPhone
  app shows these, but they come from Mercedes' **native app API** (a different
  OAuth client and backend, often a websocket stream) that the web dashboard
  never calls — so the web token can't reach them. Adding them would be a
  separate, more fragile integration (cf. the Home Assistant `mbapi2020`
  project).

## Data & security

Everything lives under `~/.config/mbcli/` (all git-ignored):

| Path | Contents | Sensitivity |
|------|----------|-------------|
| `chrome-profile/` | Persistent Chrome profile holding the login | **High** (account access) |
| `api.json` | Captured bearer token + request headers (`0600`) | **High** (account access) |
| `credentials.json` | Optional stored email/password (`0600`) | **High** |
| `vehicles.json` | Cached vehicle names/VINs | Low |
| `.logged-in` | Marker that a login has succeeded | — |
| `captures/` | Raw JSON bundles from `discover`/`--save-raw` | May contain vehicle data |

mbcli never sends your data anywhere except to Mercedes' own servers.

## Limitations

- **Live data only** — see [Data scope](#data-scope--whats-available-and-what-isnt).
- **Brittle by nature.** Mercedes can change the SPA, rotate auth, or invalidate
  the session. The fix is usually re-running (which falls back to the browser
  and re-logs-in). `discover` helps when field names change.

## Development

```bash
python -m pytest tests/ -q
```

Tested on Python 3.14, Playwright 1.60, Google Chrome 148 (macOS).
