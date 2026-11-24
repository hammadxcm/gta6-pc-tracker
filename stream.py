#!/usr/bin/env python3
"""Render the dashboard in Chromium on a virtual display and push it to YouTube Live.

    export YT_STREAM_KEY=xxxx-xxxx-xxxx-xxxx-xxxx     # YouTube Studio > Go live > Stream key
    .venv/bin/python stream.py                        # go live
    .venv/bin/python stream.py --test 20              # record 20 s to data/test.mp4 instead

Optional env:
    DASHBOARD_URL   default http://localhost:8080
    YT_RTMP_URL     default rtmps://a.rtmps.youtube.com:443/live2   (use rtmp://a.rtmp.youtube.com/live2 if RTMPS fails)
    VIDEO_BITRATE   default 6000k  (YouTube: 1080p30, H.264 CBR, 2 s keyframes)
    MUSIC_FILE      optional audio file to loop (must be music you have rights to); silence otherwise
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

from playwright.sync_api import sync_playwright

from pathlib import Path

W, H, FPS = 1920, 1080, 30
SECRETS = Path(os.environ.get("REPO_LIVE_SECRETS", Path.home() / ".config" / "repo-live"))
KEY_FILE = SECRETS / "yt_stream_key"
HERE = Path(__file__).resolve().parent
MUSIC_MIX = HERE / "music" / "mix.m4a"
MUSIC_CLOCK = HERE / "data" / "music_started.json"
DISPLAY = ":99"


def start_xvfb():
    if not shutil.which("Xvfb"):
        sys.exit("Xvfb missing: apt-get install -y xvfb")
    p = subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", f"{W}x{H}x24", "-nolisten", "tcp", "-ac"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    if p.poll() is not None:
        sys.exit(f"Xvfb failed to start on {DISPLAY} (another one running? try: pkill Xvfb)")
    return p


def ffmpeg_cmd(out: str, test_seconds: int | None):
    bitrate = os.environ.get("VIDEO_BITRATE", "6000k")
    kbps = int(bitrate.rstrip("kK"))
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-stats",
           "-thread_queue_size", "512", "-f", "x11grab", "-draw_mouse", "0",
           "-video_size", f"{W}x{H}", "-framerate", str(FPS), "-i", f"{DISPLAY}.0"]
    music = os.environ.get("MUSIC_FILE") or str(MUSIC_MIX)
    if music and os.path.exists(music):
        cmd += ["-stream_loop", "-1", "-i", music]
        vol = os.environ.get("MUSIC_VOLUME", "1.0")   # mix is pre-levelled quiet (about -24 LUFS)
        cmd += ["-filter:a", f"volume={vol},afade=t=in:st=0:d=6"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    cmd += ["-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
            "-b:v", bitrate, "-minrate", bitrate, "-maxrate", bitrate, "-bufsize", f"{kbps * 2}k",
            "-g", str(FPS * 2), "-keyint_min", str(FPS * 2), "-sc_threshold", "0", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100"]
    if test_seconds:
        return cmd + ["-t", str(test_seconds), "-y", out]
    return cmd + ["-f", "flv", "-flvflags", "no_duration_filesize", out]


def redacted_pipe(stream, secret):
    """Forward ffmpeg's stderr, masking the stream key so it never lands in logs.
    Keeps a tail of len(secret) chars so a key split across reads is still masked."""
    pending = ""
    hold = len(secret) if secret else 0
    while True:
        chunk = stream.read1(4096)
        if not chunk:
            break
        pending += chunk.decode("utf-8", "replace")
        if secret:
            pending = pending.replace(secret, "<stream-key>")
        emit, pending = (pending[:-hold], pending[-hold:]) if hold and len(pending) > hold else ("", pending) if hold else (pending, "")
        sys.stdout.write(emit)
        sys.stdout.flush()
    sys.stdout.write(pending.replace(secret, "<stream-key>") if secret else pending)
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", type=int, metavar="SECONDS", help="record to data/test.mp4 instead of streaming")
    args = ap.parse_args()

    url = os.environ.get("DASHBOARD_URL", "http://localhost:8080")
    key = None
    if args.test:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "test.mp4")
    else:
        key = os.environ.get("YT_STREAM_KEY", "").strip()
        if not key and KEY_FILE.is_file():
            key = KEY_FILE.read_text().strip()
        if not key:
            sys.exit(f"No stream key. Put it in {KEY_FILE} (chmod 600) or export YT_STREAM_KEY.")
        out = os.environ.get("YT_RTMP_URL", "rtmps://a.rtmps.youtube.com:443/live2").rstrip("/") + "/" + key

    xvfb = start_xvfb()
    os.environ["DISPLAY"] = DISPLAY
    stop = False

    def on_signal(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, args=[
            f"--window-size={W},{H}", "--window-position=0,0", "--kiosk", "--start-fullscreen",
            "--disable-infobars", "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1",
            "--disable-features=TranslateUI", "--autoplay-policy=no-user-gesture-required"])
        ctx = browser.new_context(no_viewport=True)
        page = ctx.new_page()
        # Playwright ignores --kiosk; force real fullscreen so no tab/address bar is captured
        cdp = ctx.new_cdp_session(page)
        win = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds", {"windowId": win, "bounds": {"windowState": "fullscreen"}})
        sep = "&" if "?" in url else "?"
        page.goto(f"{url}{sep}since={int(time.time() * 1000)}", wait_until="networkidle")
        page.wait_for_timeout(4000)  # let fonts, data and the intro animation settle
        last_reload = time.time()

        backoff = 2
        try:
            while not stop:
                print("▶ " + ("recording test clip" if args.test else "streaming to YouTube") + " …", flush=True)
                ff = subprocess.Popen(ffmpeg_cmd(out, args.test), stderr=subprocess.PIPE)
                MUSIC_CLOCK.write_text(json.dumps({"started_ms": int(time.time() * 1000)}))  # for "now playing"
                threading.Thread(target=redacted_pipe, args=(ff.stderr, key if not args.test else None), daemon=True).start()
                while ff.poll() is None and not stop:
                    page.wait_for_timeout(1000)          # keeps the browser event loop alive
                    if time.time() - last_reload > 6 * 3600:  # fresh page every 6 h guards against leaks
                        page.reload(wait_until="networkidle")
                        last_reload = time.time()
                if stop or args.test:
                    if ff.poll() is None:
                        ff.terminate()
                    ff.wait()
                    break
                print(f"ffmpeg exited ({ff.returncode}); reconnecting in {backoff}s", flush=True)
                # note: never print `out` — it contains the stream key
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
        finally:
            browser.close()
            xvfb.terminate()
    if args.test:
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
