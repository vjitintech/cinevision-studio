"""End-to-end check in a real headless browser:
register → create project → upload → analysis → concepts → refine → camera/lighting/storyboard/3D → shot list → export → delete.

Usage:  python e2e/run_e2e.py [--shots DIR] [--clip PATH]
Requires: built frontend (frontend/dist), playwright with Chromium, ffmpeg.
"""
from __future__ import annotations

import argparse
import base64
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def demo_clip(path: Path) -> Path:
    """A slow push-in on a still with a person in it (no audio)."""
    src = None
    for cand in ("/usr/local/lib/python3.13/dist-packages/skimage/data/astronaut.png",):
        if os.path.exists(cand):
            src = cand
    if src is None:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24:duration=5",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
        return path
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "24", "-t", "5", "-i", src,
                    "-vf", "scale=1280:1280,crop=1280:720:0:200,zoompan=z='1+0.0015*on':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1280x720:fps=24",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--clip", default=None)
    args = ap.parse_args()
    shots = Path(args.shots or tempfile.mkdtemp(prefix="cv-shots-"))
    shots.mkdir(parents=True, exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix="cv-e2e-"))
    clip = Path(args.clip) if args.clip else demo_clip(data / "scene.mp4")
    port = free_port()
    origin = f"http://127.0.0.1:{port}"
    env = {**os.environ, "CINEVISION_MASTER_KEY": base64.b64encode(os.urandom(32)).decode(), "CINEVISION_DATA_DIR": str(data / "d"),
           "CINEVISION_COOKIE_SECURE": "false", "CINEVISION_PUBLIC_ORIGIN": origin, "CINEVISION_EMBEDDED_WORKER": "true",
           "CINEVISION_FRONTEND_DIST": str(ROOT / "frontend" / "dist"), "PYTHONPATH": str(ROOT / "backend")}
    log = open(data / "server.log", "w")
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "cinevision.app:create_app", "--factory", "--port", str(port)],
                           cwd=ROOT / "backend", env=env, stdout=log, stderr=log)
    errors: list[str] = []
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(viewport={"width": 1500, "height": 950}, accept_downloads=True)
            page = ctx.new_page()
            page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type in ("error",) else None)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.set_default_timeout(30000)

            page.goto(origin)
            expect(page.get_by_role("heading", name="Create the owner account")).to_be_visible()
            page.screenshot(path=shots / "01-auth.png")
            page.get_by_label("Email").fill("director@example.test")
            page.get_by_label("Password").fill("a long and private passphrase")
            page.get_by_role("button", name="Create account").click()
            expect(page.get_by_role("heading", name="Your projects")).to_be_visible()

            page.get_by_role("button", name="New project").click()
            page.get_by_label("Project or scene name").fill("Sc. 14 — Launch pad, day")
            page.get_by_label("Director's brief").fill("Quiet pride before the launch. She knows this may be the last calm moment.")
            page.get_by_label("Genre").fill("drama")
            page.get_by_label("Desired emotion").fill("tender, wistful")
            page.screenshot(path=shots / "02-new-project.png", full_page=True)
            page.get_by_role("button", name="Create project").click()
            page.locator('input[type="file"]').set_input_files(str(clip))
            page.wait_for_url("**/analysis", timeout=60000)
            expect(page.get_by_text("Light and colour")).to_be_visible(timeout=120000)
            time.sleep(1)
            page.screenshot(path=shots / "03-analysis.png", full_page=False)

            page.get_by_role("link", name="Cinematography concepts").click()
            page.get_by_role("button", name="Generate 4 concepts").click()
            expect(page.locator("article.concept")).to_have_count(4, timeout=60000)
            expect(page.locator("section.verdict")).to_be_visible()
            print("verdict:", page.locator("section.verdict h3").inner_text())
            time.sleep(0.8)
            page.screenshot(path=shots / "04-concepts.png")
            page.get_by_label("Compare with the original").check()
            page.locator("article.concept").nth(1).get_by_role("button", name="Open").click()
            time.sleep(0.5)
            page.screenshot(path=shots / "05-concept-detail.png", full_page=True)

            page.get_by_role("button", name="Make the lighting more dramatic").click()
            expect(page.get_by_text("Made the lighting more dramatic")).to_be_visible()
            page.get_by_label("Refinement request").fill("move the camera lower and teleport to the moon")
            page.get_by_role("button", name="Apply change").click()
            expect(page.get_by_text("Not understood")).to_be_visible()
            page.get_by_role("button", name="Choose this concept").click()

            page.get_by_role("link", name="Camera planner").click()
            expect(page.get_by_role("heading", name="Camera planner")).to_be_visible()
            page.get_by_label("Shot size").select_option("close-up")
            time.sleep(0.4)
            page.screenshot(path=shots / "06-camera.png")
            page.get_by_role("button", name="Save camera plan").click()
            expect(page.get_by_text("Saved to this concept's camera plan.")).to_be_visible()

            page.get_by_role("link", name="Lighting studio").click()
            expect(page.get_by_role("heading", name="Lighting studio")).to_be_visible()
            page.get_by_role("button", name="Add backlight").click()
            time.sleep(0.4)
            page.screenshot(path=shots / "07-lighting.png")
            page.get_by_role("button", name="Save lighting plan").click()
            expect(page.get_by_text("Saved. Shot lists generated")).to_be_visible()

            page.get_by_role("link", name="Storyboard & animatic").click()
            expect(page.locator("figure.board").first).to_be_visible()
            page.get_by_role("button", name="Play animatic").click()
            time.sleep(1.6)
            tc = page.locator(".ws-bottom .tc").inner_text()
            assert not tc.startswith("00:00.0 "), f"animatic did not advance: {tc}"
            page.screenshot(path=shots / "08-storyboard.png")
            page.get_by_role("button", name="Pause animatic").click()

            page.get_by_role("link", name="3D previsualisation").click()
            expect(page.get_by_role("heading", name="3D previsualisation")).to_be_visible()
            page.get_by_role("button", name="Request AI preview").click()
            expect(page.get_by_text("no data left the server")).to_be_visible()
            time.sleep(0.4)
            page.screenshot(path=shots / "09-previs3d.png")

            page.get_by_role("link", name="Shot list").click()
            page.get_by_role("button", name="Build shot list").click()
            expect(page.locator("table.list tbody tr").first).to_be_visible()
            page.screenshot(path=shots / "10-shotlist.png", full_page=True)
            for fmt in ("PDF", "CSV (spreadsheets)"):
                with page.expect_download() as dl:
                    page.get_by_role("button", name=f"Download {fmt}").click()
                f = dl.value
                path = shots / f.suggested_filename
                f.save_as(path)
                assert path.stat().st_size > 200, path
            print("downloads ok")

            page.get_by_role("link", name="Security & privacy").click()
            expect(page.get_by_role("heading", name="AI services")).to_be_visible()
            page.screenshot(path=shots / "11-security.png", full_page=True)

            page.go_back()
            page.get_by_role("link", name="Project settings").click()
            page.get_by_label("Type the project name to confirm").fill("Sc. 14 — Launch pad, day")
            page.get_by_role("button", name="Delete project permanently").click()
            expect(page.get_by_text("No projects yet")).to_be_visible()
            # mobile layout sanity
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=shots / "12-mobile-dashboard.png")
            b.close()
    except Exception:
        print("BROWSER ERRORS SO FAR:\n" + "\n".join(errors))
        raise
    finally:
        srv.terminate()
        srv.wait(10)
    leftovers = list((data / "d" / "blobs").rglob("*.cve"))
    print("encrypted files left after delete:", len(leftovers))
    print("screenshots:", shots)
    # the AI-preview request is expected to be refused (409 not_configured); browsers log that as a failed resource
    errors = [e for e in errors if "status of 409" not in e]
    if errors:
        print("BROWSER ERRORS:\n" + "\n".join(errors))
        sys.exit(1)
    if leftovers:
        sys.exit(1)
    print("E2E PASSED")


if __name__ == "__main__":
    main()
