"""Browser journeys for subject-wise enrollment (driven by tests/e2e/subject_enrollment_ui_disposable.py, which owns the disposable
environment and checks the persisted results afterwards). Real Institute portal in Chromium; nothing is mocked.
Usage: python subject_enrollment_ui_browser.py <params.json>   (needs playwright==1.48)."""
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

P = json.loads(Path(sys.argv[1]).read_text())
UI, SHOTS = P["ui"], Path(P["shots"])
SHOTS.mkdir(parents=True, exist_ok=True)
results: list[tuple[str, bool]] = []
counter = [0]


def shot(page, name):
    counter[0] += 1
    page.screenshot(path=str(SHOTS / f"se-{counter[0]:02d}-{name}.png"), full_page=True)


def check(name, ok, detail="", page=None):
    results.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {str(detail)[:300]}"), flush=True)
    if not ok:
        if page is not None:
            shot(page, "FAIL")
        done()


def done():
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} browser checks passed" + (f"; FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


def step(name, page, fn):
    try:
        fn()
        check(name, True)
    except Exception as exc:  # noqa: BLE001 - reported as a failed check
        check(name, False, f"{type(exc).__name__}: {exc}", page)


def mail_lines() -> list[str]:
    path = Path(P["mail_log"])
    return path.read_text().splitlines() if path.exists() else []


def sign_in(browser, code):
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, timezone_id="Asia/Kolkata")
    page = ctx.new_page()
    page.set_default_timeout(20000)
    before = len(mail_lines())
    page.goto(UI + "/login")
    page.get_by_label("Staff ID").fill(code)
    page.get_by_label("Password").fill(P["password"])
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(re.compile(r"/(mfa)?$|/mfa"), timeout=20000)
    if "/mfa" in page.url:
        otp = None
        for _ in range(40):
            codes = [m.group(1) for b in (json.loads(x)["body"] for x in mail_lines()[before:]) for m in [re.search(r"code is (\d{6})", b)] if m]
            if codes:
                otp = codes[-1]
                break
            time.sleep(0.25)
        page.get_by_label("Verification code").fill(otp or "")
        page.get_by_role("button", name="Verify").click()
    page.wait_for_url(re.compile(r"^(?!.*/(login|mfa)).*$"), timeout=20000)
    page.get_by_role("complementary", name="Primary").wait_for()
    return ctx, page


def row(page, text):
    return page.get_by_role("row").filter(has_text=text)


def roster(page):
    page.goto(UI + "/students?tab=branch")
    expect(row(page, "Kiran Existing")).to_be_visible()


with sync_playwright() as pw:
    browser = pw.chromium.connect(P["ws"])
    print("Provider Admin")
    ctx, page = sign_in(browser, P["pa"])

    def existing_unchanged():
        roster(page)
        for name in ("Kiran Existing", "Meera Existing"):
            expect(row(page, name).get_by_role("cell", name="NEET A", exact=True)).to_be_visible()   # no selection: no subject list
        shot(page, "roster-before")
    step("existing memberships without a selection show the batch only (every subject)", page, existing_unchanged)

    def enroll_one_subject():
        page.get_by_role("button", name="Enroll student").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_role("button", name="New student").click()
        dialog.get_by_label("First name").fill("Nisha")
        dialog.get_by_label("Last name").fill("Physics")
        dialog.get_by_label("Email").fill("nisha@family.example.com")
        dialog.get_by_label("NEET A", exact=True).check()
        group = dialog.get_by_role("group", name="Subjects in NEET A")
        expect(group.get_by_label("Physics")).to_be_checked()
        expect(group.get_by_label("Chemistry")).to_be_checked()                  # every subject by default
        group.get_by_label("Chemistry").uncheck()
        expect(group.get_by_label("Physics")).to_be_disabled()                    # the last subject stays ticked
        dialog.get_by_label(re.compile(r"Class / level")).select_option("11TH")      # Class and Academic Year are required
        dialog.get_by_label(re.compile(r"Academic year")).fill("2026-2027")
        shot(page, "enroll-physics-only")
        dialog.get_by_role("button", name="Enroll", exact=True).click()
        expect(dialog).to_be_hidden()
        page.reload()
        expect(row(page, "Nisha Physics").get_by_role("cell", name="NEET A (Physics)")).to_be_visible()
        shot(page, "enrolled-reloaded")
    step("enrollment form: choose Physics only, save, reload: the selection persists", page, enroll_one_subject)

    def manage_one_subject():
        row(page, "Meera Existing").get_by_role("button", name="Manage").click()
        dialog = page.get_by_role("dialog")
        group = dialog.get_by_role("group", name="Subjects in NEET A")
        expect(group.get_by_label("Physics")).to_be_checked()
        expect(group.get_by_label("Chemistry")).to_be_checked()
        group.get_by_label("Physics").click()       # saved at once; the box clears when the server confirms (not instantly, so no .uncheck())
        expect(dialog.get_by_text("Subjects updated.")).to_be_visible()
        expect(group.get_by_label("Physics")).not_to_be_checked()
        shot(page, "manage-chemistry-only")
        page.reload()
        expect(row(page, "Meera Existing").get_by_role("cell", name="NEET A (Chemistry)")).to_be_visible()
        row(page, "Meera Existing").get_by_role("button", name="Manage").click()
        group = page.get_by_role("dialog").get_by_role("group", name="Subjects in NEET A")
        expect(group.get_by_label("Chemistry")).to_be_checked()
        expect(group.get_by_label("Physics")).not_to_be_checked()
        shot(page, "manage-reopened")
    step("Manage dialog: choose Chemistry only, reload: the selection persists and reopens as saved", page, manage_one_subject)

    def untouched_still_all():
        page.reload()
        expect(row(page, "Kiran Existing").get_by_role("cell", name="NEET A", exact=True)).to_be_visible()
        row(page, "Kiran Existing").get_by_role("button", name="Manage").click()
        group = page.get_by_role("dialog").get_by_role("group", name="Subjects in NEET A")
        expect(group.get_by_label("Physics")).to_be_checked()
        expect(group.get_by_label("Chemistry")).to_be_checked()
        shot(page, "untouched")
    step("the untouched existing membership still takes every subject", page, untouched_still_all)
    ctx.close()
    browser.close()

done()
