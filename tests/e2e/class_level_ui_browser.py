"""Browser journeys for Class / Academic Year (driven by tests/e2e/class_level_ui_disposable.py, which owns the disposable
environment and checks the persisted results afterwards). Real Institute portal in Chromium; nothing is mocked.
Usage: python class_level_ui_browser.py <params.json>   (needs playwright==1.48)."""
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
    page.screenshot(path=str(SHOTS / f"cl-{counter[0]:02d}-{name}.png"), full_page=True)


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


CLASS, YEAR = re.compile(r"Class / level"), re.compile(r"Academic year")


def roster(page):
    page.goto(UI + "/students?tab=branch")
    expect(row(page, "Kiran Existing")).to_be_visible()


def new_student(page, first, last, mail):
    page.get_by_role("button", name="Enroll student").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_role("button", name="New student").click()
    dialog.get_by_label("First name").fill(first)
    dialog.get_by_label("Last name").fill(last)
    dialog.get_by_label("Email").fill(mail)
    return dialog


with sync_playwright() as pw:
    browser = pw.chromium.connect(P["ws"])
    print("Provider Admin")
    ctx, page = sign_in(browser, P["pa"])

    def batch_defaults():
        page.goto(UI + "/batches")
        expect(row(page, "NEET 11").get_by_role("cell", name="11th · 2026-2027")).to_be_visible()
        page.get_by_role("button", name="New batch").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Name").fill("JEE 12")
        dialog.get_by_label(re.compile(r"Default class")).select_option("12TH")
        dialog.get_by_label(re.compile(r"Default academic year")).fill("2027")
        dialog.get_by_role("button", name="Create").click()
        expect(dialog.get_by_text(re.compile(r"Use the form 2026-2027"))).to_be_visible()     # validated before saving
        dialog.get_by_label(re.compile(r"Default academic year")).fill("2027-2028")
        dialog.get_by_role("button", name="Create").click()
        expect(dialog).to_be_hidden()
        page.reload()
        expect(row(page, "JEE 12").get_by_role("cell", name="12th · 2027-2028")).to_be_visible()
        shot(page, "batch-defaults")
    step("batch: optional Class defaults validated, saved and shown", page, batch_defaults)

    def enroll_prefilled():
        roster(page)
        expect(row(page, "Lata Existing").get_by_role("cell", name="Not set")).to_be_visible()   # legacy enrollment flagged
        dialog = new_student(page, "Nisha", "Eleven", "nisha@family.example.com")
        dialog.get_by_label("NEET 11", exact=True).check()
        expect(dialog.get_by_label(CLASS)).to_have_value("11TH")
        expect(dialog.get_by_label(YEAR)).to_have_value("2026-2027")
        expect(dialog.get_by_text(re.compile(r"Prefilled from the batch defaults"))).to_be_visible()
        shot(page, "enroll-prefilled")
        dialog.get_by_role("button", name="Enroll", exact=True).click()
        expect(dialog).to_be_hidden()
        page.reload()
        expect(row(page, "Nisha Eleven").get_by_role("cell", name="11th · 2026-2027")).to_be_visible()
    step("enrollment form: Class / Year prefilled from the batch default, saved, persists after reload", page, enroll_prefilled)

    def enroll_conflict():
        dialog = new_student(page, "Om", "Twelve", "om@family.example.com")
        dialog.get_by_label("NEET 11", exact=True).check()
        dialog.get_by_label("NEET 12", exact=True).check()
        expect(dialog.get_by_text(re.compile(r"suggest different classes"))).to_be_visible()
        expect(dialog.get_by_label(CLASS)).to_have_value("")
        dialog.get_by_role("button", name="Enroll", exact=True).click()
        expect(dialog.get_by_text("Choose the class.")).to_be_visible()                       # never picked silently
        dialog.get_by_label(CLASS).select_option("12TH")
        dialog.get_by_label(YEAR).fill("2026-2027")
        shot(page, "enroll-conflict-chosen")
        dialog.get_by_role("button", name="Enroll", exact=True).click()
        expect(dialog).to_be_hidden()
        page.reload()
        expect(row(page, "Om Twelve").get_by_role("cell", name="12th · 2026-2027")).to_be_visible()
    step("conflicting batch defaults: staff must choose; the chosen Class persists", page, enroll_conflict)

    def complete_legacy():
        row(page, "Lata Existing").get_by_role("button", name="Manage").click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_text(re.compile(r"No class / academic year yet"))).to_be_visible()
        dialog.get_by_label(CLASS).select_option("OTHER")
        dialog.get_by_label(YEAR).fill("2027-2028")
        dialog.get_by_role("button", name="Save class").click()
        expect(dialog.get_by_text("Describe the class.")).to_be_visible()
        dialog.get_by_label(re.compile(r"Describe the class")).fill("NIOS bridge")
        dialog.get_by_role("button", name="Save class").click()
        expect(dialog.get_by_text("Class and academic year saved.")).to_be_visible()
        shot(page, "manage-legacy-saved")
        page.reload()
        expect(row(page, "Lata Existing").get_by_role("cell", name="Other (NIOS bridge) · 2027-2028")).to_be_visible()
        row(page, "Lata Existing").get_by_role("button", name="Manage").click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_label(CLASS)).to_have_value("OTHER")
        expect(dialog.get_by_label(re.compile(r"Describe the class"))).to_have_value("NIOS bridge")
        expect(dialog.get_by_label("NEET 11", exact=True)).to_be_checked()                  # batch membership untouched
        shot(page, "manage-reopened")
    step("Manage dialog: a legacy enrollment is completed (Other + description) and persists after reload", page, complete_legacy)
    ctx.close()
    browser.close()

done()
