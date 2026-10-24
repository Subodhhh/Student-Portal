"""Browser journeys for Daily, Weekly and Global Tests (driven by tests/e2e/test_types_ui_disposable.py, which owns the disposable
environment and checks the persisted results afterwards). Every step goes through the real Institute portal in Chromium; nothing is
mocked. Usage: python test_types_ui_browser.py <params.json>   (needs playwright==1.48)."""
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
    page.screenshot(path=str(SHOTS / f"tt-{counter[0]:02d}-{name}.png"), full_page=True)


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
    """Run one journey step; any timeout or assertion inside is a failed check with a screenshot."""
    try:
        detail = fn()
        check(name, True)
        return detail
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
            new = [json.loads(x)["body"] for x in mail_lines()[before:]]
            codes = [m.group(1) for b in new for m in [re.search(r"code is (\d{6})", b)] if m]
            if codes:
                otp = codes[-1]
                break
            time.sleep(0.25)
        page.get_by_label("Verification code").fill(otp or "")
        page.get_by_role("button", name="Verify").click()
    page.wait_for_url(re.compile(r"^(?!.*/(login|mfa)).*$"), timeout=20000)
    page.get_by_role("complementary", name="Primary").wait_for()
    return ctx, page


def test_section(page):
    nav = page.get_by_role("complementary", name="Primary")
    return [x.strip() for x in nav.get_by_role("link").all_inner_texts()]


def row(page, text):
    return page.get_by_role("row").filter(has_text=text)


with sync_playwright() as pw:
    browser = pw.chromium.connect(P["ws"])

    # ---------------------------------------------------------------- Provider Admin: Daily Test with an approved bank paper
    print("Provider Admin")
    ctx, page = sign_in(browser, P["pa"])
    links = test_section(page)
    check("sidebar: Daily Test, Weekly Test, Global Tests, Scheduled Tests; no Composite Tests",
          all(x in links for x in ("Daily Test", "Weekly Test", "Global Tests", "Scheduled Tests")) and "Composite Tests" not in links, links, page)

    def daily():
        page.goto(UI + "/tests/new")
        page.get_by_label("Pune").check()
        page.get_by_label("NEET A").check()
        page.get_by_role("button", name="Next").click()
        page.get_by_label("Test name").fill("Bank Physics Daily")
        page.get_by_role("button", name="Next").click()
        page.get_by_label("Physics", exact=True).check()
        page.get_by_role("button", name="Next").click()
        page.get_by_label("Duration (minutes)").fill("45")
        page.get_by_role("button", name="Next").click()
        page.get_by_label("Use an approved paper").check()
        picker = page.get_by_label("Approved paper", exact=True)
        picker.select_option(label=picker.locator("option", has_text="North Physics Set").first.inner_text())
        expect(page.get_by_text("approved paper chosen")).to_be_visible()
        page.get_by_role("button", name="Next").click()
        expect(page.get_by_text("in the approved paper chosen from the bank")).to_be_visible()
        page.get_by_role("button", name="Next").click()
        expect(page.get_by_role("heading", name="Review & publish")).to_be_visible()
        shot(page, "daily-review")
        page.get_by_role("button", name="Publish test").click()
        page.get_by_role("dialog").get_by_role("button", name="Publish").click()
        expect(page.get_by_text("Daily Test published")).to_be_visible()
        shot(page, "daily-published")
    step("Daily Test: audience -> name -> subject -> details -> approved bank paper -> review -> published", page, daily)

    def scheduled():
        page.goto(UI + "/tests")
        r = row(page, "Bank Physics Daily")
        expect(r).to_be_visible()
        expect(r.get_by_text("Daily", exact=True)).to_be_visible()
        shot(page, "scheduled-daily")
    step("Scheduled Tests lists the Daily Test with its type", page, scheduled)

    def offer(paper_title):
        def go():
            page.goto(UI + "/tests/global?tab=tests")   # Provider Admins land on the Schedule tab since the Global Test schedule rework (af85c73)
            row(page, "National Mock").get_by_role("button", name="Offer a paper").click()
            dialog = page.get_by_role("dialog")
            select = dialog.get_by_label("Paper", exact=True)
            select.select_option(label=select.locator("option", has_text=paper_title).first.inner_text())
            dialog.get_by_label("Note for the reviewer (optional)").fill("Fresh set for this term")
            dialog.get_by_role("button", name="Offer paper").click()
            expect(page.get_by_text(f"Offered “{paper_title}”")).to_be_visible()
            expect(row(page, paper_title).get_by_text("PENDING")).to_be_visible()
            shot(page, f"offered-{paper_title.split()[0].lower()}")
        return go
    step("Global Test: the provider offers its approved Chemistry paper; it is listed as pending", page, offer("North Chemistry Set"))
    ctx.close()

    print("second provider")
    ctx, page = sign_in(browser, P["pa2"])
    step("Global Test: a second provider offers a competing Chemistry paper", page, offer("South Chemistry Set"))
    check("the second provider does not see the first provider's contribution", page.get_by_text("North Chemistry Set").count() == 0, page=page)
    ctx.close()

    # ---------------------------------------------------------------- Branch Admin: Weekly Test for a month
    print("Branch Admin")
    ctx, page = sign_in(browser, P["ba"])
    weekly_rows = {}

    def weekly():
        page.goto(UI + "/tests/weekly/new")
        page.get_by_label("Pune").check()
        page.get_by_label("NEET A").check()
        page.get_by_label("Name", exact=True).fill("Saturday Physics")
        page.get_by_label("Physics", exact=True).check()
        picker = page.get_by_label("Paper for Physics")
        picker.select_option(label=picker.locator("option", has_text="North Physics Set").first.inner_text())
        page.get_by_label("Time zone").select_option("Asia/Kolkata")
        page.get_by_label("Opens at (local time)").fill("10:00")
        page.get_by_role("button", name="Preview dates").click()
        table = page.get_by_role("table").last
        expect(table.get_by_role("row").nth(4)).to_be_visible()
        weekly_rows["n"] = table.get_by_role("row").count() - 1
        shot(page, "weekly-preview")
        page.get_by_role("button", name="Create Weekly Test").click()
        expect(page.get_by_role("heading", level=2, name="Saturday Physics")).to_be_visible()
        expect(page.get_by_text(f"{weekly_rows['n']} of {weekly_rows['n']} dates")).to_be_visible()
        shot(page, "weekly-created")
    step("Weekly Test: audience, subject with its paper, Saturdays at 10:00 Asia/Kolkata -> preview -> created", page, weekly)

    def retry():
        page.get_by_role("button", name="Generate missing tests").click()
        expect(page.get_by_text("Every test is already scheduled; nothing was duplicated.")).to_be_visible()
        expect(page.get_by_text(f"{weekly_rows['n']} of {weekly_rows['n']} dates")).to_be_visible()
    step("Weekly Test: a retried generation creates no duplicates", page, retry)

    def weekly_listed():
        page.goto(UI + "/tests")
        page.get_by_label("Type").select_option("WEEKLY")
        expect(page.get_by_role("row").filter(has_text="Saturday Physics")).to_have_count(weekly_rows["n"])
        shot(page, "scheduled-weekly")
    step("Scheduled Tests filtered to Weekly shows one test per date", page, weekly_listed)
    ctx.close()

    # ---------------------------------------------------------------- Super Admin: review, resolve the conflict, copy, new version
    print("Super Admin")
    ctx, page = sign_in(browser, P["sa"])

    def review():
        page.goto(UI + "/tests/global?tab=contributions")
        expect(page.get_by_text("Conflict: 2 offers")).to_have_count(2)
        shot(page, "review-conflict")
        row(page, "North Chemistry Set").get_by_role("button", name="Approve").click()
        page.get_by_role("dialog").get_by_role("button", name="Approve").click()
        expect(page.get_by_text("now needs the normal paper approval")).to_be_visible()
    step("platform sees the conflict and approves one contribution explicitly (copy made)", page, review)

    def conflict():
        row(page, "South Chemistry Set").get_by_role("button", name="Approve").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_role("button", name="Approve").click()
        expect(dialog.get_by_text("Another contribution for this subject is already approved")).to_be_visible()
        shot(page, "review-refused")
        dialog.get_by_role("button", name="Cancel").click()
        row(page, "South Chemistry Set").get_by_role("button", name="Reject").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Reason (required)").fill("Chemistry is already covered by another provider")
        dialog.get_by_role("button", name="Reject").click()
        expect(page.get_by_text("Rejected “South Chemistry Set”.")).to_be_visible()
    step("a second approval for the same subject is refused; the competing offer is rejected with a reason", page, conflict)

    def approve_copy():
        page.get_by_label("Status").select_option("APPROVED")
        r = row(page, "North Chemistry Set")
        expect(r.get_by_text("Platform copy: validated")).to_be_visible()
        r.get_by_role("link", name="Review platform copy").click()
        page.get_by_role("button", name="Approve").click()
        page.get_by_role("dialog").get_by_role("button", name=re.compile("Approve|Confirm")).click()
        page.get_by_role("button", name="Publish").click()
        page.get_by_role("dialog").get_by_role("button", name=re.compile("Publish|Confirm")).click()
        expect(page.get_by_text("PUBLISHED").first).to_be_visible()
        shot(page, "copy-published")
    step("the platform copy goes through the normal paper approval and publication", page, approve_copy)

    def new_version():
        page.goto(UI + "/tests/global")
        row(page, "National Mock").filter(has_text="PUBLISHED").get_by_role("button", name="Create new version").click()
        expect(page.get_by_text("Created the draft “National Mock (copy)”")).to_be_visible()
        row(page, "National Mock (copy)").get_by_role("button", name="Edit").click()
        dialog = page.get_by_role("dialog")
        dialog.locator("label", has_text="contributed by North Academy").locator("input").check()
        shot(page, "derive-edit")
        dialog.get_by_role("button", name="Save draft").click()
        expect(page.get_by_text("Saved the draft")).to_be_visible()
        row(page, "National Mock (copy)").get_by_role("button", name="Publish").click()
        page.get_by_role("dialog").get_by_role("button", name="Publish").click()
        expect(page.get_by_text("“National Mock (copy)” is published.")).to_be_visible()
        expect(row(page, "National Mock (copy)").get_by_text("Physics, Chemistry")).to_be_visible()
        shot(page, "new-version-published")
    step("a new test version (derived draft) includes the copied paper and is published by the platform", page, new_version)

    def original_unchanged():
        r = page.get_by_role("row").filter(has_text=re.compile(r"^National Mock(?! \(copy\))"))
        expect(r.filter(has_text="PUBLISHED").first.get_by_role("cell").nth(2)).to_have_text("Physics")
    step("the original published Global Test is unchanged (Physics only)", page, original_unchanged)
    ctx.close()

    # ---------------------------------------------------------------- Provider Admin: schedule the new version from Global Tests
    print("Provider Admin schedules the Global Test")
    ctx, page = sign_in(browser, P["pa"])

    def schedule_global():
        page.goto(UI + "/tests/global?tab=tests")   # Provider Admins land on the Schedule tab since the Global Test schedule rework (af85c73)
        page.get_by_role("link", name="National Mock (copy)").click()
        expect(page.get_by_role("heading", level=2, name="National Mock (copy)")).to_be_visible()
        expect(page.get_by_role("button", name="Withdraw")).to_have_count(0)                  # platform-only actions are not offered
        page.get_by_role("link", name="Schedule this Global Test").click()
        expect(page.get_by_role("heading", level=2, name="Schedule Global Test")).to_be_visible()
        page.get_by_role("group", name="Working in").get_by_label("Branch", exact=True).select_option(label="Pune")
        page.get_by_label("NEET A").check()
        page.get_by_role("button", name="Next").click()
        expect(page.get_by_label("Test name")).to_have_value("National Mock (copy)")
        page.get_by_role("button", name="Next").click()
        shot(page, "global-schedule-review")
        page.get_by_role("button", name="Schedule", exact=True).click()
        expect(page.get_by_text("Test scheduled")).to_be_visible()
        page.get_by_role("link", name="Back to the Global Test").click()
        page.get_by_role("button", name="Generate now").click()
        expect(page.get_by_text("Generation succeeded.")).to_be_visible()
        expect(page.get_by_role("row").filter(has_text="Pune").get_by_role("link", name="National Mock (copy)")).to_be_visible()
        shot(page, "global-scheduled")
    step("Global Tests: the provider schedules the new version from its own page (Global Tests > test > Schedule) and generates it",
         page, schedule_global)
    ctx.close()

    # ---------------------------------------------------------------- Teacher: sees, never creates
    print("Teacher")
    ctx, page = sign_in(browser, P["teacher"])
    links = test_section(page)
    check("teacher sidebar: Global Tests and Scheduled Tests only (no test creation)", "Daily Test" not in links and "Weekly Test" not in links
          and "Global Tests" in links and "Scheduled Tests" in links, links, page)

    def teacher_view():
        page.goto(UI + "/tests")
        expect(row(page, "Bank Physics Daily")).to_be_visible()
        expect(row(page, "National Mock (copy)")).to_be_visible()     # Physics + Chemistry test: one matching subject is enough
        page.goto(UI + "/tests/new")
        expect(page.get_by_text("Access denied")).to_be_visible()
        shot(page, "teacher-denied")
    step("Physics teacher sees the tests targeting their students (incl. the Physics + Chemistry Global Test); cannot open Daily Test",
         page, teacher_view)
    ctx.close()

    ctx, page = sign_in(browser, P["teacher2"])

    def other_teacher():
        page.goto(UI + "/tests")
        expect(page.get_by_text("No scheduled tests yet")).to_be_visible()  # teaches Chemistry, but only in Mumbai: nobody targeted is theirs
        shot(page, "other-teacher-empty")
    step("a Chemistry teacher of another branch sees none of these tests", page, other_teacher)
    ctx.close()


    browser.close()

done()
