#!/usr/bin/env python3
"""Reimplementation of Wendy Quest's coupon client.

Source: `WendyQuest.t` in package.nw/js/plugins/WendyQuest.js (javascript-obfuscator
output; the coupon module's members were additionally mangled to single letters).

The game generates neither coupon codes nor session IDs; both are issued by the
server. This script reproduces everything the client *does* compute locally (API base
URL, X-Pmmf-Ac header, request URLs, stone -> type mapping, response handling) and,
by default, actually dispatches the three calls the client would make; with
`--offline` it only prints them.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

API_BASE_B64 = "aHR0cHM6Ly93cXNlcnZlci44MW1vbmtleXMuY29tL2ZyZXNoLXF1ZXN0LWNvdXBvbnMtcHJvZC9hcGkv"  # t.m()
HEADER_SALT = "e67Y2i"  # t.P() / t.k()
APP_DEEP_LINK = "https://m-wendys.app.link/9B57RO3lj2b"  # t.M(), opened by the coupon window's second button

# Stone index -> (item id in data/Items.json, name). See DataManager.makeSavefileInfo in MonkeyWrenchMZ.js.
STONES = (
    (31, "Fry Stone"),
    (32, "Frosty Stone"),
    (33, "Spicy Stone"),
    (34, "Breakfast Stone"),
    (35, "Beef Stone"),
)

PLACEHOLDER_SESSION_ID = "<session_id from GET app/session>"

PROMOTION_STATUS = {  # t.R()
    "1": "active -> calls coupons/retrieve",
    "2": "coupons unavailable (ui_texts.couponsUnavailable)",
    "3": "promotion ended (ui_texts.promotionEndedPopup)",
}


@dataclass(frozen=True)
class Request:
    method: str
    url: str
    headers: dict[str, str]


@dataclass
class Response:
    status: int
    payload: dict | None
    raw: bytes


def _as_json(raw: bytes) -> dict | None:
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return None


def session_id_of(payload: dict | None) -> str | None:
    """Extract the session ID the server mints (t.A(): `r.session_id`)."""
    if not payload:
        return None
    value = payload.get("session_id")
    return value if isinstance(value, str) and value else None


def send(request: Request, now: datetime) -> Response:
    """Dispatch a single GET, the way the client's t.P()/t.k() would.

    The client sends every request as a bare GET with only the headers it
    computes; the three transport headers (Keep-Alive / Connection /
    X-Pmmf-Ac) are attached verbatim. The response body is JSON-decoded when
    possible; anything else (e.g. an HTML 404) is returned raw so a caller
    can decide how to surface it.
    """
    http = urllib.request.Request(request.url, headers=request.headers, method=request.method)
    try:
        with urllib.request.urlopen(http, timeout=10) as response:
            raw = response.read()
            return Response(response.status, _as_json(raw), raw)
    except urllib.error.HTTPError as err:
        raw = err.read()
        return Response(err.code, _as_json(raw), raw)
    except (urllib.error.URLError, OSError) as err:
        raise err


def api_base() -> str:
    """t.m(), which every call site wraps in atob()."""
    return base64.b64decode(API_BASE_B64).decode()


def auth_token(now: datetime) -> str:
    """X-Pmmf-Ac: md5(new Date().toISOString().replace(/\\D/g, "").slice(0, 12) + "e67Y2i").

    toISOString() is UTC, so the first 12 digits are YYYYMMDDHHMM: the token rotates every minute.
    """
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
    return hashlib.md5((stamp + HEADER_SALT).encode()).hexdigest()


def request_headers(now: datetime) -> dict[str, str]:
    """t.P(), the NW.js (Steam) transport. The Capacitor/mobile transport t.k() sends only X-Pmmf-Ac."""
    return {"Keep-Alive": "300", "Connection": "keep-alive", "X-Pmmf-Ac": auth_token(now)}


def session_request(now: datetime) -> Request:
    """t.A(). The session ID is minted server-side; the client only stores what comes back.

    Lifted verbatim (after webcrack deobfuscation):

        A: async function () {
          var _0x20b396 = atob(this.m()) + "app/session";
          var _0x20b396 = await this.U(_0x20b396);
          return !!_0x20b396 && !!_0x20b396.session_id && !(_0x20b396 = _0x20b396.session_id,
            DataManager.setCustomGlobalData("session_id", _0x20b396), 0);
        },
    """
    return Request("GET", api_base() + "app/session", request_headers(now))


def init_request(session_id: str, now: datetime) -> Request:
    """t._(): returns promotion_status and the ui_texts used for the popups."""
    return Request("GET", f"{api_base()}app/init?session_id={session_id}", request_headers(now))


def retrieve_request(session_id: str, stone_index: int, now: datetime) -> Request:
    """t.F(): stone index 0-4 is sent as type 1-5. The session ID is not URL-encoded, matching the client."""
    if not 0 <= stone_index < len(STONES):
        raise ValueError("stone index out of bounds!")
    url = f"{api_base()}coupons/retrieve?session_id={session_id}&type={stone_index + 1}"
    return Request("GET", url, request_headers(now))


def coupon_from_response(payload: dict | None) -> str | None:
    """t.F()'s success check: this is the entirety of the client's "code generation"."""
    if payload and payload.get("result") == "successful" and payload.get("coupon_code"):
        return payload["coupon_code"]
    return None


def live_dispatch(stone_index: int, now: datetime, session_id: str | None = None) -> None:
    """Walk t.A() -> t._() -> t.F() against the live server, reusing the
    session_id the server mints in step 1. If a session_id is passed in,
    step 1 is skipped and that ID is used instead.

    Any HTTP status, JSON error body, or missing fields aborts with a
    message that names which of the three calls failed and why, so the
    trace is readable end-to-end.
    """
    _, name = STONES[stone_index]
    print(f"Dispatching t.A() -> t._() -> t.F() for {name} (type={stone_index + 1})\n")
    if session_id:
        print(f"1. t.A() skipped; reusing session_id = {session_id!r}\n")
    else:
        # 1. t.A() - mint the session
        req = session_request(now)
        print(f"1. {req.method} {req.url}")
        response = send(req, now)
        session_id = session_id_of(response.payload)
        if not session_id:
            body = _short(response)
            print(f"   ! no session_id in response (status {response.status}): {body}")
            return
        print(f"   session_id = {session_id!r}\n")

    # 2. t._() - promotion_status + ui_texts
    req = init_request(session_id, now)
    print(f"2. {req.method} {req.url}")
    response = send(req, now)
    if not response.payload:
        print(f"   ! non-JSON body (status {response.status}): {response.raw!r}")
        return
    status = str(response.payload.get("promotion_status") or "")
    if status not in PROMOTION_STATUS:
        print(f"   ! unexpected promotion_status {status!r}")
        return
    print(f"   promotion_status = {status!r} -> {PROMOTION_STATUS[status]}\n")
    if status != "1":
        print("   server says coupons are unavailable; not calling coupons/retrieve.")
        return

    # 3. t.F() - redeem the stone
    req = retrieve_request(session_id, stone_index, now)
    print(f"3. {req.method} {req.url}")
    response = send(req, now)
    code = coupon_from_response(response.payload)
    if not code:
        print(f"   ! no coupon_code in response (status {response.status}): {_short(response)}")
        return
    print(f"   coupon_code = {code!r}")
    print(f"\n  On success the game caches the code in global save data (couponCodes[{stone_index}])")
    print(f"  and offers a deep link to {APP_DEEP_LINK}")


def _short(response: Response, limit: int = 120) -> str:
    text = (response.raw or b"").decode("utf-8", "replace")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def parse_stone(arg: str) -> int:
    if arg.isdigit():
        if 1 <= int(arg) <= len(STONES):
            return int(arg) - 1
    else:
        key = arg.lower().removesuffix(" stone")
        for index, (_, name) in enumerate(STONES):
            if name.lower().removesuffix(" stone") == key:
                return index
    raise argparse.ArgumentTypeError(f"unknown stone {arg!r}; use 1-5 or one of: fry, frosty, spicy, breakfast, beef")


def parse_time(arg: str) -> datetime:
    parsed = datetime.fromisoformat(arg)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stone", type=parse_stone, help="1-5 or fry/frosty/spicy/breakfast/beef")
    parser.add_argument("--at", type=parse_time, default=None,
                        help="compute X-Pmmf-Ac for this ISO time instead of now (naive = UTC)")
    parser.add_argument("--offline", action="store_true",
                        help="do not dispatch; just print the requests the client would send")
    parser.add_argument("--session", metavar="SESSION_ID", default=None,
                        help="reuse this session_id instead of minting one via app/session (skips t.A())")
    args = parser.parse_args()

    now = args.at or datetime.now(timezone.utc)
    item_id, name = STONES[args.stone]
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%d%H%M")

    print("Wendy Quest coupon client\n")
    print(f"API base   t.m() -> {api_base()}")
    print(f"Stone      {name} (item {item_id}) -> type={args.stone + 1}")
    print(f'X-Pmmf-Ac  md5("{stamp}{HEADER_SALT}") = {auth_token(now)}\n')

    if args.offline:
        session_id = args.session or PLACEHOLDER_SESSION_ID
        steps = (
            ("t.A()", session_request(now),
             'server returns {"session_id": ...}; stored in global save data, never generated locally'),
            ("t._()", init_request(session_id, now),
             "promotion_status: " + "; ".join(f"{k} = {v}" for k, v in PROMOTION_STATUS.items())),
            ("t.F()", retrieve_request(session_id, args.stone, now),
             'coupon code = response["coupon_code"] if response["result"] == "successful"'),
        )
        for number, (origin, request, note) in enumerate(steps, 1):
            if number == 1 and args.session:
                print(f"{number}. {origin}  skipped; reusing session_id = {args.session!r}")
                continue
            print(f"{number}. {origin}  {request.method} {request.url}")
            print(f"          -> {note}")

        print("\nHeaders on every request (t.P, NW.js transport):")
        for key, value in request_headers(now).items():
            print(f"  {key}: {value}")
        print(f"\nOn success the game offers 'copy code' and a deep link to {APP_DEEP_LINK}")
        return

    # live: actually hit the three endpoints with the headers the client would send
    live_dispatch(args.stone, now, args.session)


if __name__ == "__main__":
    main()
