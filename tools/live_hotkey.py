import argparse
import ctypes
import json
import sys
import urllib.error
import urllib.request

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
WM_HOTKEY = 0x0312
HOTKEY_ID = 1
HOTKEYS = {
    "Ctrl+Alt+C": (MOD_CONTROL | MOD_ALT, ord("C")),
    "Ctrl+Alt+X": (MOD_CONTROL | MOD_ALT, ord("X")),
    "Ctrl+Shift+C": (MOD_CONTROL | MOD_SHIFT, ord("C")),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Clip the latest live buffer with the configured Windows hotkey.")
    parser.add_argument("--seconds", type=int, help="Override the configured clip length (1–600 seconds).")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="Local Clipper API base URL.")
    args = parser.parse_args()
    if args.seconds is not None and not 1 <= args.seconds <= 600:
        parser.error("--seconds must be between 1 and 600.")
    if sys.platform != "win32":
        parser.error("The global hotkey helper requires Windows.")

    try:
        with urllib.request.urlopen(f"{args.api.rstrip('/')}/api/settings", timeout=10) as response:
            settings = json.loads(response.read().decode("utf-8"))
        preferences = settings["preferences"]
        hotkey = preferences["hotkey"]
        modifiers, virtual_key = HOTKEYS[hotkey]
        seconds = args.seconds if args.seconds is not None else int(preferences["default_clip_length"])
    except urllib.error.URLError as exc:
        print(f"Could not load hotkey settings from the local API: {exc.reason}", file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"The local API returned invalid hotkey settings: {exc}", file=sys.stderr)
        return 1

    from ctypes import wintypes

    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, HOTKEY_ID, modifiers, virtual_key):
        print(f"Could not register {hotkey}. Check that another application is not using it.", file=sys.stderr)
        return 1
    print(f"Global hotkey active: {hotkey} clips the last {seconds} seconds.")
    print("Press Ctrl+C in this window to stop the helper.")
    message = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message != WM_HOTKEY or message.wParam != HOTKEY_ID:
                continue
            request = urllib.request.Request(
                f"{args.api.rstrip('/')}/api/live/clip-last",
                data=json.dumps({"seconds": seconds}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    result = json.loads(response.read().decode("utf-8"))
                print(f"Queued live clip job {result['job_id']}.")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                print(f"Could not queue live clip ({exc.code}): {detail}", file=sys.stderr)
            except urllib.error.URLError as exc:
                print(f"Could not reach the local Clipper API: {exc.reason}", file=sys.stderr)
    except KeyboardInterrupt:
        return 0
    finally:
        user32.UnregisterHotKey(None, HOTKEY_ID)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
