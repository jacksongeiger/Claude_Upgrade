#!/usr/bin/env python3
"""Nightshift lighthouse scorer.

python3 scorers/lighthouse.py --config '<json of the scorer's config.json entry>' --workdir <dir>

Needs, in the scorer config:
  serve_cmd   (required) command, run via `bash -c` in --workdir, that serves
              the app and keeps running (started in the background, killed
              when this scorer finishes)
  urls        (required) list of URLs to audit (should target `port` below)
  port        (required) the port serve_cmd binds to; polled until it accepts
              connections before auditing starts

Without serve_cmd + urls + port this prints
    {"ok":false,"error":"not configured: serve_cmd, urls, and port required",...}
If `npx`/`lighthouse` aren't available, ok:false with that error instead.

Runs `npx lighthouse <url> --output=json --output-path=stdout
--chrome-flags='--headless=new' --quiet` twice per url and takes the median of
each category's score (0-100). Per-url value = 0.4*a11y + 0.3*perf + 0.2*best
+ 0.1*seo. `value` is the mean of per-url values.
"""
import argparse
import json
import os
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import time

SERVER_READY_TIMEOUT_S = 20
LIGHTHOUSE_RUN_TIMEOUT_S = 5 * 60
RUNS_PER_URL = 2


def emit(name, value, ok, error, raw):
    print(json.dumps({"name": name, "value": value, "ok": ok, "error": error, "raw": raw}))


def wait_for_port(port, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def find_chrome():
    """CHROME_PATH if set, else the Chromium Playwright installed (this is
    what the persona driver uses too), else whatever is on PATH."""
    if os.environ.get("CHROME_PATH"):
        return os.environ["CHROME_PATH"]
    import glob
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""), os.path.expanduser("~/.cache/ms-playwright"), "/opt/pw-browsers"]
    for r in roots:
        if not r:
            continue
        for pat in ("chromium-*/chrome-linux/chrome", "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
                    "chromium_headless_shell-*/chrome-linux/headless_shell"):
            hits = sorted(glob.glob(os.path.join(r, pat)))
            if hits:
                return hits[-1]
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def run_lighthouse(url):
    env = dict(os.environ)
    chrome = find_chrome()
    if chrome:
        env["CHROME_PATH"] = chrome
    flags = "--headless=new --no-sandbox --disable-gpu"
    proc = subprocess.run(
        ["npx", "--no-install", "lighthouse", url, "--output=json", "--output-path=stdout",
         f"--chrome-flags={flags}", "--quiet"],
        capture_output=True, text=True, timeout=LIGHTHOUSE_RUN_TIMEOUT_S, env=env,
    )
    out = proc.stdout
    start = out.find("{")
    if start < 0:
        raise RuntimeError("lighthouse printed no JSON (chrome=%s): %s" % (chrome, (proc.stderr or "")[-400:].strip()))
    data = json.loads(out[start:])
    cats = data["categories"]
    return {
        "a11y": cats["accessibility"]["score"] * 100.0,
        "perf": cats["performance"]["score"] * 100.0,
        "best": cats["best-practices"]["score"] * 100.0,
        "seo": cats["seo"]["score"] * 100.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    name = "lighthouse"
    server = None
    try:
        cfg = json.loads(args.config)
        name = cfg.get("name", "lighthouse")
        workdir = args.workdir
        serve_cmd = cfg.get("serve_cmd")
        urls = cfg.get("urls")
        port = cfg.get("port")

        if not serve_cmd or not urls or not port:
            emit(name, 0, False, "not configured: serve_cmd, urls, and port required", {})
            return 0

        if shutil.which("npx") is None:
            emit(name, 0, False, "npx not found; install Node.js to run lighthouse", {})
            return 0

        try:
            server = subprocess.Popen(
                ["bash", "-c", serve_cmd], cwd=workdir,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            if not wait_for_port(port, SERVER_READY_TIMEOUT_S):
                emit(name, 0, False, f"serve_cmd did not open port {port} within {SERVER_READY_TIMEOUT_S}s", {})
                return 0

            per_url = {}
            for url in urls:
                cat_runs = []
                try:
                    for _ in range(RUNS_PER_URL):
                        cat_runs.append(run_lighthouse(url))
                except (subprocess.TimeoutExpired, OSError, ValueError, KeyError) as e:
                    emit(name, 0, False, f"lighthouse failed for {url}: {e}", {})
                    return 0
                medians = {
                    k: statistics.median(r[k] for r in cat_runs)
                    for k in ("a11y", "perf", "best", "seo")
                }
                per_url[url] = medians

            per_url_value = {
                url: 0.4 * m["a11y"] + 0.3 * m["perf"] + 0.2 * m["best"] + 0.1 * m["seo"]
                for url, m in per_url.items()
            }
            value = sum(per_url_value.values()) / len(per_url_value)
            raw = {"per_url": per_url, "per_url_value": per_url_value}
            emit(name, value, True, None, raw)
            return 0
        finally:
            if server is not None and server.poll() is None:
                try:
                    os.killpg(os.getpgid(server.pid), signal.SIGTERM)
                    server.wait(timeout=5)
                except Exception:
                    try:
                        os.killpg(os.getpgid(server.pid), signal.SIGKILL)
                    except Exception:
                        pass
    except Exception as e:
        emit(name, 0, False, f"scorer crashed: {e}", {})
        return 0


if __name__ == "__main__":
    sys.exit(main())
