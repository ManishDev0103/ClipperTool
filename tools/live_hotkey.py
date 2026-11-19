import argparse
import ctypes
import json
import sys
import urllib.error
import urllib.request

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
WM_HOTKEY = 0x0312
HOTKEY_ID = 1
VK_C = ord("C")


def main() -> int:
    parser = argparse.ArgumentParser(description="Clip the latest live buffer with Ctrl+Alt+C.")
    parser.add_argument("--seconds", type=int, default=60, help="Clip length from 1 to 600 seconds.")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="Local Clipper API base URL.")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600:
        parser.error("--seconds must be between 1 and 600.")
    if sys.platform != "win32":
        parser.error("The global hotkey helper requires Windows.")

    from ctypes import wintypes

    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_C):
        print("Could not register Ctrl+Alt+C. Check that another application is not using it.", file=sys.stderr)
        return 1
    print(f"Global hotkey active: Ctrl+Alt+C clips the last {args.seconds} seconds.")
    print("Press Ctrl+C in this window to stop the helper.")
    message = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message != WM_HOTKEY or message.wParam != HOTKEY_ID:
                continue
            request = urllib.request.Request(
                f"{args.api.rstrip('/')}/api/live/clip-last",
                data=json.dumps({"seconds": args.seconds}).encode("utf-8"),
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
