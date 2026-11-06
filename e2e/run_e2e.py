"""End-to-end check in a real headless browser:
register → create project → upload → analysis → concepts → refine → camera/lighting/storyboard/3D → shot list → export → delete.

Usage:  python e2e/run_e2e.py [--shots DIR] [--clip PATH]
Requires: built frontend (frontend/dist), playwright with Chromium, ffmpeg.
"""
from __future__ import annotations

import argparse
import base64
import os
import re
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


def assembly_clips(d: Path) -> list[Path]:
    """Title card, two scenes cut from the sample footage, end credits (shuffled file order on upload)."""
    src = Path(__file__).resolve().parents[1] / "samples" / "sample-two-shots.mp4"
    card = ("drawtext=text='{t}':fontcolor=white:fontsize=64:x=(w-tw)/2:y=280,"
            "drawtext=text='{s}':fontcolor=white:fontsize=36:x=(w-tw)/2:y=400,"
            "drawtext=text='Music by Another Person':fontcolor=white:fontsize=36:x=(w-tw)/2:y=460")
    run = lambda *a: subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *a], check=True)  # noqa: E731
    out = [d / "A001_titles.mp4", d / "A002_rocket.mp4", d / "A003_astronaut.mp4", d / "Z999_credits.mp4"]
    run("-f", "lavfi", "-i", "color=black:s=1280x720:d=3:r=24", "-vf", card.format(t="LAUNCH DAY", s="A film by Someone"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out[0]))
    run("-ss", "3.1", "-i", str(src), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out[1]))
    run("-i", str(src), "-t", "2.9", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out[2]))
    run("-f", "lavfi", "-i", "color=black:s=1280x720:d=3:r=24", "-vf", card.format(t="THE END", s="Directed by Name Surname"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out[3]))
    return [out[3], out[2], out[0], out[1]]


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
            page.on("response", lambda r: errors.append(f"http {r.status}: {r.request.method} {r.url.split('?')[0]} (on {page.url.split('?')[0]})") if (r.status == 404 or r.status >= 500 or (r.status == 409 and "ai-preview" not in r.url)) else None)
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
            # zero-input flow: a name and the video are all that's needed
            page.get_by_label("Name of your film or scene").fill("Sc. 14 — Launch pad, day")
            page.screenshot(path=shots / "02-new-project.png", full_page=True)
            page.get_by_role("button", name="Create and upload video").click()
            page.locator('input[type="file"]').set_input_files(str(clip))
            page.wait_for_url("**/review", timeout=60000)
            expect(page.locator("section.verdict")).to_be_visible(timeout=180000)
            expect(page.locator(".dept-chip")).to_have_count(8)
            expect(page.locator("#dept-sound")).to_be_visible()
            expect(page.get_by_role("heading", name="Ways to edit this video")).to_be_visible()
            # every alternative plays as an animated 3D animatic (canvas pixels change over time)
            anim = page.locator(".anim-card canvas").first
            anim.scroll_into_view_if_needed()
            expect(page.locator(".anim-card canvas")).to_have_count(4, timeout=30000)
            time.sleep(0.4)
            f1 = anim.evaluate("c => c.toDataURL()")
            time.sleep(1.2)
            assert anim.evaluate("c => c.toDataURL()") != f1, "3D animatic did not move"
            page.screenshot(path=shots / "03e-review-3d.png", full_page=False)
            page.locator("section.verdict").scroll_into_view_if_needed()
            time.sleep(1)
            page.screenshot(path=shots / "03-review.png", full_page=False)
            # language switch: the review is re-rendered in the chosen language (RTL for Arabic)
            page.locator("select.lang-pick").select_option("te")
            expect(page.locator(".review[lang=te]")).to_be_visible(timeout=10000)
            expect(page.get_by_role("heading", name="దర్శకుడి సమీక్ష")).to_be_visible()
            time.sleep(0.8)
            page.screenshot(path=shots / "03c-review-telugu.png")
            page.locator("select.lang-pick").select_option("ar")
            expect(page.locator(".review[dir=rtl]")).to_be_visible(timeout=10000)
            time.sleep(0.8)
            page.screenshot(path=shots / "03d-review-arabic.png")
            page.locator("select.lang-pick").select_option("en")
            expect(page.locator(".review[dir=rtl]")).to_have_count(0, timeout=10000)
            expect(page.get_by_role("heading", name="Director's review")).to_be_visible()

            page.get_by_role("navigation", name="Project sections").get_by_role("link", name="Video analysis").click()
            expect(page.get_by_text("Light and colour")).to_be_visible(timeout=60000)
            expect(page.get_by_role("heading", name="Sound")).to_be_visible()
            time.sleep(1)
            page.screenshot(path=shots / "03b-analysis.png", full_page=False)

            page.get_by_role("navigation", name="Project sections").get_by_role("link", name="Cinematography concepts").click()
            expect(page.locator("article.concept")).to_have_count(4, timeout=60000)   # generated automatically
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

            page.get_by_role("navigation", name="Project sections").get_by_role("link", name="Camera planner").click()
            expect(page.get_by_role("heading", name="Camera planner")).to_be_visible()
            page.get_by_label("Shot size").select_option("close-up")
            time.sleep(0.4)
            page.screenshot(path=shots / "06-camera.png")
            page.get_by_role("button", name="Save camera plan").click()
            expect(page.get_by_text("Saved to this concept's camera plan.")).to_be_visible()

            page.get_by_role("navigation", name="Project sections").get_by_role("link", name="Lighting studio").click()
            expect(page.get_by_role("heading", name="Lighting studio")).to_be_visible()
            page.get_by_role("button", name="Add backlight").click()
            time.sleep(0.4)
            page.screenshot(path=shots / "07-lighting.png")
            page.get_by_role("button", name="Save lighting plan").click()
            expect(page.get_by_text("Saved. Shot lists generated")).to_be_visible()

            page.get_by_role("navigation", name="Project sections").get_by_role("link", name="Storyboard & animatic").click()
            expect(page.locator("figure.board").first).to_be_visible()
            page.get_by_role("button", name="Play animatic").click()
            time.sleep(1.6)
            tc = page.locator(".ws-bottom .tc").inner_text()
            assert not tc.startswith("00:00.0 "), f"animatic did not advance: {tc}"
            page.screenshot(path=shots / "08-storyboard.png")
            page.get_by_role("button", name="Pause animatic").click()

            page.get_by_role("navigation", name="Project sections").get_by_role("link", name="3D previsualisation").click()
            expect(page.get_by_role("heading", name="3D previsualisation")).to_be_visible()
            page.get_by_role("button", name="Request AI preview").click()
            expect(page.get_by_text("no data left the server")).to_be_visible()
            time.sleep(0.4)
            page.screenshot(path=shots / "09-previs3d.png")

            page.get_by_role("navigation", name="Project sections").get_by_role("link", name="Shot list").click()
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
            page.get_by_role("navigation", name="Project sections").get_by_role("link", name="Project settings").click()
            page.get_by_label("Type the project name to confirm").fill("Sc. 14 — Launch pad, day")
            page.get_by_role("button", name="Delete project permanently").click()
            expect(page.get_by_text("No projects yet")).to_be_visible()
            # learn directing: chapters with lessons built from the review, practice focus from this user's videos
            page.get_by_role("link", name="Learn directing").click()
            expect(page.locator("section.chapter")).to_have_count(8)
            page.locator("section.chapter button").nth(5).click()
            expect(page.locator(".lesson").first).to_be_visible()
            page.screenshot(path=shots / "11a-learn.png", full_page=False)
            # movie assembly: many clips + a short screenplay → ordered cut with reasons
            page.get_by_role("link", name="Movie assembly").click()
            page.get_by_label("Name of the film").fill("Launch day")
            page.get_by_label("Story, scene description or screenplay (optional)").fill(
                "Opening titles.\nEXT. LAUNCH PAD - DAY. The rocket stands in the sun.\nINT. CREW ROOM - DAY. The astronaut sits alone, quiet.\nEnd credits.")
            page.get_by_role("button", name="Create and add clips").click()
            page.wait_for_url("**/a/*")
            page.locator('input[type="file"][multiple]').set_input_files([str(p) for p in assembly_clips(data)])
            expect(page.locator(".clip-card")).to_have_count(4, timeout=60000)
            expect(page.get_by_text("4 of 4 uploaded · 0 uploading · 0 failed")).to_be_visible(timeout=60000)   # 3 at a time
            expect(page.locator("ol.cutlist > li")).to_have_count(4, timeout=240000)
            order = page.locator("ol.cutlist h4").all_inner_texts()
            print("assembly order:", order)
            assert order[0].endswith("A001_titles.mp4") and order[-1].endswith("Z999_credits.mp4"), order
            time.sleep(1)
            page.screenshot(path=shots / "11b-assembly.png", full_page=True)
            # the director's own order: keyboard move (focus stays on the moved clip, announced), drag and drop,
            # "Check my order" with plain-language notes, EDL download of that order, reset to suggested
            used = page.locator("ol.order-list.used .order-name")
            expect(page.get_by_role("heading", name="Your order")).to_be_visible()
            names = [re.sub(r"^\d+\.\s*", "", n) for n in used.all_inner_texts()]
            assert names == [re.sub(r"^\d+\.\s*", "", o) for o in order], (names, order)
            mover = page.get_by_role("button", name=f"Move down: {names[1]}")
            mover.focus()
            page.keyboard.press("Enter")
            expect(page.get_by_role("status").filter(has_text=f"{names[1]} moved to position 3 of 4")).to_have_count(1)
            expect(mover).to_be_focused()                 # focus follows the moved clip (React re-renders first)
            assert [re.sub(r"^\d+\.\s*", "", n) for n in used.all_inner_texts()] == [names[0], names[2], names[1], names[3]]
            items = page.locator("ol.order-list.used > li.order-item")
            items.nth(3).drag_to(items.nth(0))                                    # credits dragged to the top
            after = [re.sub(r"^\d+\.\s*", "", n) for n in used.all_inner_texts()]
            assert after[0] == "Z999_credits.mp4", after
            expect(page.get_by_text("Changed since the last check.")).to_be_visible()
            page.get_by_role("button", name="Check my order").click()
            expect(page.locator(".check-note").filter(has_text="end credits")).to_be_visible(timeout=30000)
            expect(page.locator(".check-note").filter(has_text="Do this:")).not_to_have_count(0)
            page.screenshot(path=shots / "11c-assembly-my-order.png", full_page=True)
            with page.expect_download() as dl:
                page.get_by_role("button", name="EDL — my order").click()
            edl = Path(dl.value.path()).read_text(encoding="utf-8")
            assert dl.value.suggested_filename.endswith(".edl"), dl.value.suggested_filename
            assert edl.startswith("TITLE: Launch day") and edl.index("Z999_credits.mp4") < edl.index("A001_titles.mp4"), edl
            with page.expect_download() as dl:
                page.get_by_role("button", name="FCPXML — suggested cut").click()
            assert "<fcpxml version=\"1.9\">" in Path(dl.value.path()).read_text(encoding="utf-8")
            page.get_by_role("button", name="Reset to suggested").click()
            expect(page.get_by_role("status").filter(has_text="Back to the suggested cut.")).to_have_count(1)
            assert [re.sub(r"^\d+\.\s*", "", n) for n in used.all_inner_texts()] == names
            page.get_by_role("link", name="Private dashboard").click()
            expect(page.locator(".reel")).to_have_count(1)                       # only the assembly; its clips are inside it
            page.locator(".reel a.title").click()
            page.get_by_role("button", name="Delete assembly").click()
            page.get_by_role("button", name="Click again to delete everything").click()
            expect(page.get_by_text("No projects yet")).to_be_visible()          # assembly + every clip deleted
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
