"""End-to-end test of the browser edition (GitHub Pages build), served from a /cinevision-studio/ sub-path like Pages.

    cd frontend && node build.mjs --edition=browser && cd ..
    python e2e/run_pages_e2e.py [--shots DIR]

Checks the whole flow AND that the page makes no network requests beyond loading its own static files.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "tests"))
from conftest import make_clip  # noqa: E402  (synthetic clips with known camera motion)


def serve(root: Path) -> tuple[http.server.ThreadingHTTPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    args = ap.parse_args()
    shots = Path(args.shots or tempfile.mkdtemp(prefix="cv-pages-"))
    shots.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="cv-pages-site-"))
    (work / "cinevision-studio").symlink_to(ROOT / "frontend" / "dist-pages")
    clips = Path(tempfile.mkdtemp(prefix="cv-pages-clips-"))
    pan = make_clip(clips / "pan.webm", pattern="pan", codec="libvpx-vp9", duration=4, extra=["-b:v", "1M"])
    cut = make_clip(clips / "cut.webm", pattern="cut", codec="libvpx-vp9", duration=6, extra=["-b:v", "1M"])
    bad = clips / "notes.mp4"
    bad.write_bytes(b"this is not a video" * 200)

    srv, port = serve(work)
    base = f"http://127.0.0.1:{port}/cinevision-studio/"
    errors, outside = [], []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(viewport={"width": 1500, "height": 950}, accept_downloads=True)
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
            page.on("request", lambda r: outside.append(r.url) if not (r.url.startswith(base) or r.url.startswith(("blob:", "data:"))) else None)
            page.set_default_timeout(30000)

            page.goto(base)
            expect(page.get_by_role("heading", name="Your projects")).to_be_visible()
            expect(page.get_by_text("Browser edition")).to_be_visible()
            page.screenshot(path=shots / "p01-dashboard.png")

            # invalid file is rejected with a clear message
            page.get_by_role("button", name="New project").click()
            page.get_by_label("Project or scene name").fill("Bad file")
            page.get_by_role("button", name="Create project").click()
            page.locator('input[type="file"]').set_input_files(str(bad))
            expect(page.get_by_role("alert")).to_contain_text("can't play")

            # real flow with a panning shot
            page.goto(base + "#/new")
            page.get_by_label("Project or scene name").fill("Sc. 3 — Rooftop")
            page.get_by_label("Genre").fill("thriller")
            page.get_by_label("Desired emotion").fill("dread")
            page.get_by_role("button", name="Create project").click()
            page.locator('input[type="file"]').set_input_files(str(pan))
            page.wait_for_url("**#/p/*/analysis", timeout=60000)
            expect(page.get_by_text("Light and colour")).to_be_visible(timeout=180000)
            expect(page.locator(".fact", has_text="Camera movement")).to_contain_text("pan right")
            page.screenshot(path=shots / "p02-analysis.png")

            page.get_by_role("link", name="Cinematography concepts").click()
            page.get_by_role("button", name="Generate 4 concepts").click()
            expect(page.locator("article.concept")).to_have_count(4, timeout=60000)
            expect(page.locator("section.verdict")).to_be_visible()
            print("verdict:", page.locator("section.verdict h3").inner_text())
            first = page.locator("article.concept h3").first.inner_text()
            print("top concept:", first)
            page.screenshot(path=shots / "p03-concepts.png")
            page.get_by_role("button", name="Make the lighting more dramatic").click()
            expect(page.get_by_text("Made the lighting more dramatic")).to_be_visible()
            page.get_by_role("button", name="Choose this concept").click()

            page.get_by_role("link", name="Camera planner").click()
            page.get_by_label("Shot size").select_option("close-up")
            page.get_by_role("button", name="Save camera plan").click()
            expect(page.get_by_text("Saved to this concept's camera plan.")).to_be_visible()
            page.screenshot(path=shots / "p04-camera.png")

            page.get_by_role("link", name="Lighting studio").click()
            page.get_by_role("button", name="Add backlight").click()
            page.get_by_role("button", name="Save lighting plan").click()
            expect(page.get_by_text("Saved. Shot lists generated")).to_be_visible()

            page.get_by_role("link", name="Storyboard & animatic").click()
            page.get_by_role("button", name="Play animatic").click()
            time.sleep(1.5)
            assert not page.locator(".ws-bottom .tc").inner_text().startswith("00:00.0 "), "animatic did not advance"
            page.get_by_role("button", name="Pause animatic").click()
            page.screenshot(path=shots / "p05-storyboard.png")

            page.get_by_role("link", name="3D previsualisation").click()
            page.get_by_role("button", name="Request AI preview").click()
            expect(page.get_by_text("no data left your device")).to_be_visible()

            page.get_by_role("link", name="Shot list").click()
            page.get_by_role("button", name="Build shot list").click()
            expect(page.locator("table.list tbody tr").first).to_be_visible()
            with page.expect_download() as dl:
                page.get_by_role("button", name="Download CSV (spreadsheets)").click()
            csv_path = shots / dl.value.suggested_filename
            dl.value.save_as(csv_path)
            assert csv_path.read_text(encoding="utf-8-sig").startswith("Shot,"), "CSV header missing"
            page.screenshot(path=shots / "p06-shotlist.png", full_page=True)

            # data survives a reload (IndexedDB), deep link works
            url = page.url
            page.reload()
            expect(page.get_by_role("heading", name="Shot list")).to_be_visible()
            assert page.url == url

            # second clip: two shots detected
            page.goto(base + "#/new")
            page.get_by_label("Project or scene name").fill("Cut test")
            page.get_by_role("button", name="Create project").click()
            page.locator('input[type="file"]').set_input_files(str(cut))
            expect(page.get_by_text("Light and colour")).to_be_visible(timeout=180000)
            expect(page.locator(".fact", has_text="Shots detected").locator(".v")).to_have_text("2")

            page.get_by_role("link", name="Security & privacy").click()
            expect(page.get_by_text("Your clips never leave this device")).to_be_visible()
            page.screenshot(path=shots / "p07-privacy.png")
            page.once("dialog", lambda d: d.accept())
            page.get_by_role("button", name="Delete all CineVision data from this browser").click()
            expect(page.get_by_text("No projects yet")).to_be_visible(timeout=15000)

            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=shots / "p08-mobile.png")
            b.close()
    finally:
        srv.shutdown()
    errors = [e for e in errors if "status of 409" not in e]
    print("screenshots:", shots)
    if outside:
        print("UNEXPECTED NETWORK REQUESTS:\n" + "\n".join(outside))
        sys.exit(1)
    if errors:
        print("BROWSER ERRORS:\n" + "\n".join(errors))
        sys.exit(1)
    print("PAGES E2E PASSED (no network requests beyond the site's own files)")


if __name__ == "__main__":
    main()
