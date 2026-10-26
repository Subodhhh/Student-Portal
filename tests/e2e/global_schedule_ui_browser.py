"""Browser journeys for the Global Test schedule and photo papers (driven by tests/e2e/global_schedule_ui_disposable.py, which owns the
disposable environment and checks the persisted results). Every step goes through the real portals in Chromium; nothing is mocked.
Usage: python global_schedule_ui_browser.py <params.json> staff|student   (needs playwright==1.48)."""
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

P = json.loads(Path(sys.argv[1]).read_text())
PHASE = sys.argv[2]
UI, SUI, SHOTS, PHOTOS = P["ui"], P["sui"], Path(P["shots"]), Path(P["photos_host"])
SHOTS.mkdir(parents=True, exist_ok=True)
results: list[tuple[str, bool]] = []
counter = [0 if PHASE == "staff" else 50]


def shot(page, name):
    counter[0] += 1
    page.screenshot(path=str(SHOTS / f"gs-{counter[0]:02d}-{name}.png"), full_page=True)


def check(name, ok, detail="", page=None):
    results.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {str(detail)[:300]}"), flush=True)
    if not ok:
        if page is not None:
            shot(page, "FAIL")
        done()


def done():
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} browser checks passed ({PHASE})" + (f"; FAILED: {failed}" if failed else ""))
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


def new_context(browser):
    return browser.new_context(viewport={"width": 1366, "height": 900}, timezone_id="Asia/Kolkata")


def sign_in(browser, code):
    ctx = new_context(browser)
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


def nav_links(page):
    return [x.strip() for x in page.get_by_role("complementary", name="Primary").get_by_role("link").all_inner_texts()]


def row(page, text):
    return page.get_by_role("row").filter(has_text=text)


def first_date(page):
    """Open the first (earliest) date of National Mock from the schedule list."""
    page.goto(UI + "/tests/global?tab=schedule")
    r = row(page, "National Mock").first
    expect(r).to_be_visible()
    r.get_by_role("link").click()
    expect(page.get_by_role("heading", level=2, name="Papers")).to_be_visible()


def submit_paper(page, paper, *, subject=None, branches=(), whole=(), batches=(), students=(), absent=()):
    page.get_by_role("button", name="Submit a paper").click()
    dialog = page.get_by_role("dialog")
    if subject:
        dialog.get_by_label("Subject", exact=True).select_option(label=subject)
    picker = dialog.get_by_label("Your paper")
    picker.select_option(label=picker.locator("option", has_text=paper).first.inner_text())
    for b in branches:
        dialog.get_by_label(b, exact=True).check()
    for w in whole:
        dialog.get_by_label(f"Every student of {w}").check()
    for b in batches:
        dialog.get_by_label(b, exact=True).check()
    for a in absent:
        expect(dialog.get_by_label(a, exact=True)).to_have_count(0)
    for s in students:
        dialog.get_by_label("Find students by name or Student ID").fill(s)
        dialog.get_by_label(f"Select {s}").check()
    return dialog


with sync_playwright() as pw:
    browser = pw.chromium.connect(P["ws"])

    if PHASE == "staff":
        # ---------------------------------------------------------------- Super Admin: definition, months of dates, one publication
        print("Super Admin")
        ctx, page = sign_in(browser, P["sa"])
        nxt = date.today().replace(day=1)
        first_month = f"{nxt.year + (nxt.month // 12)}-{nxt.month % 12 + 1:02d}"
        planned = {}

        def create():
            page.goto(UI + "/tests/global")
            page.get_by_role("button", name="New Global Test").click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_label("Name", exact=True).fill("National Mock")
            dialog.get_by_label("Exam type").fill("NEET")
            dialog.get_by_label("Duration (minutes)").fill("30")
            dialog.get_by_label("Physics", exact=True).check()
            dialog.get_by_label("Chemistry", exact=True).check()
            dialog.get_by_label("Biology", exact=True).check()
            dialog.get_by_label("Paper deadline (hours before each test opens)").fill("2")
            expect(dialog.get_by_text("approved platform papers")).to_have_count(0)
            shot(page, "sa-new-test")
            dialog.get_by_role("button", name="Save draft").click()
            expect(page.get_by_text("Open it to plan and publish its dates")).to_be_visible()
            page.get_by_role("link", name="National Mock").click()
            expect(page.get_by_role("heading", level=2, name="2. Schedule")).to_be_visible()
        step("Super Admin creates a Global Test from subjects, duration and marking, with no platform paper", page, create)

        def plan():
            page.get_by_role("button", name="Plan dates for months").click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_label("First month").fill(first_month)
            dialog.get_by_label("Number of months").fill("3")
            expect(dialog.get_by_label("Sat", exact=True)).to_be_checked()                       # the default day: Saturday only
            expect(dialog.get_by_label("Sun", exact=True)).not_to_be_checked()
            dialog.get_by_label("Time zone").select_option("Asia/Kolkata")
            dialog.get_by_label("Opens at (local time)").fill("10:00")
            dialog.get_by_role("button", name="Plan dates").click()
            msg = page.get_by_text(re.compile(r"Planned \d+ new draft date\(s\)\.")).inner_text()
            planned["n"] = int(re.search(r"\d+", msg).group())
            expect(page.get_by_role("button", name="Edit", exact=True)).to_have_count(planned["n"])
            shot(page, "sa-planned")
        step("plans every Saturday 10:00 (Asia/Kolkata) for 3 months as draft dates", page, plan)

        def delete_one():
            page.get_by_role("button", name="Delete", exact=True).last.click()
            page.get_by_role("dialog").get_by_role("button", name="Delete", exact=True).click()
            expect(page.get_by_text("Draft date deleted.")).to_be_visible()
            expect(page.get_by_role("button", name="Edit", exact=True)).to_have_count(planned["n"] - 1)
        step("deletes one draft date before publication", page, delete_one)

        def publish():
            page.get_by_role("button", name="Publish test and dates").click()
            page.get_by_role("dialog").get_by_role("button", name="Publish").click()
            expect(page.get_by_text(re.compile(r"Providers can now submit their papers"))).to_be_visible()
            expect(page.get_by_role("button", name="Edit", exact=True)).to_have_count(0)
            shot(page, "sa-published")
        step("publishes the test and all its dates once; published dates are locked", page, publish)

        def schedule_tab():
            page.goto(UI + "/tests/global?tab=schedule")
            expect(row(page, "National Mock")).to_have_count(planned["n"] - 1)
        step("the published schedule lists every date", page, schedule_tab)
        ctx.close()

        # ---------------------------------------------------------------- Admin (junior): may manage Global Tests, nothing beyond the role
        print("Admin (junior)")
        ctx, page = sign_in(browser, P["admin"])

        def admin():
            page.goto(UI + "/tests/global")
            page.get_by_role("button", name="New Global Test").click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_label("Name", exact=True).fill("Admin Draft Mock")
            dialog.get_by_label("Physics", exact=True).check()
            dialog.get_by_role("button", name="Save draft").click()
            expect(page.get_by_text("Open it to plan and publish its dates")).to_be_visible()
        step("Admin creates a draft Global Test (examplan.manage)", page, admin)

        def admin_limits():
            first_date(page)
            expect(page.get_by_role("button", name="Submit a paper")).to_have_count(0)        # papers come from provider staff only
            page.goto(UI + "/system-health")
            expect(page.get_by_text("Access denied")).to_be_visible()                         # settings.platform is Super Admin only
            shot(page, "admin-denied")
        step("Admin cannot submit provider papers and cannot open Super Admin-only pages", page, admin_limits)
        ctx.close()

        # ---------------------------------------------------------------- Teacher Tara: photo paper with a separate key, then submit it
        print("Teacher (Tara): photo paper")
        ctx, page = sign_in(browser, P["ta"])
        draft = {}

        def schedule_visible():
            check("teacher sidebar offers Global Tests", "Global Tests" in nav_links(page), nav_links(page), page)
            page.goto(UI + "/tests/global")
            r = row(page, "National Mock").first
            expect(r.get_by_text("· you can submit")).to_have_count(1)                        # Physics only, not Chemistry
            expect(r.get_by_role("link", name="Submit paper")).to_be_visible()
            shot(page, "teacher-schedule")
        step("the teacher sees the published dates, with Submit paper for Physics (the subject taught) only", page, schedule_visible)

        def start_photo():
            page.goto(UI + "/papers?tab=imports")
            page.get_by_role("link", name="Upload photos").click()
            page.get_by_label("Subject").select_option(label="Physics")
            page.get_by_label("Paper title").fill("Tara photo paper")
            page.get_by_role("button", name="Start").click()
            expect(page.get_by_role("heading", level=2, name="Tara photo paper")).to_be_visible()
            draft["url"] = page.url
        step("Question Bank & Papers > Upload photos: a new photo paper for Physics", page, start_photo)

        def upload_pages():
            page.get_by_label("Add page photos").set_input_files([str(PHOTOS / "page2-sideways.jpg"), str(PHOTOS / "page1.jpg"), str(PHOTOS / "blank.jpg")])
            expect(page.get_by_text(re.compile(r"Page 3 · "))).to_be_visible(timeout=120000)
            shot(page, "photo-uploaded")
        step("three phone photos (one sideways, one blank) uploaded with progress, each read by OCR on upload", page, upload_pages)

        def order_and_remove():
            page.get_by_role("button", name="Remove 3").click()
            expect(page.get_by_text(re.compile(r"Page 3 · "))).to_have_count(0)
            page.get_by_role("button", name="Move 1 down").click()                              # the sideways page 2 was picked first
            expect(page.locator(".photo-list li").first.get_by_role("img")).to_have_attribute("alt", "Page 1")
            expect(page.get_by_role("img", name="Page 1")).to_be_visible()
        step("the blank photo is removed and the pages are put in order", page, order_and_remove)

        def key():
            page.get_by_label("Add answer-key photos or file").set_input_files(str(PHOTOS / "key.jpg"))
            expect(page.get_by_text(re.compile(r"Key 1 · "))).to_be_visible(timeout=120000)
        step("the answer key is uploaded separately, as a photo", page, key)

        ocr = {}

        def extract():
            page.get_by_role("button", name="Read questions from the photos").click()
            expect(page.get_by_role("region", name="Question 4")).to_be_visible(timeout=120000)
            shot(page, "photo-preview")
            regions = page.get_by_role("region")
            ocr["n"] = page.locator("section.photo-question").count()
            ocr["flagged"] = page.locator("section.photo-question.needs-review").count()
            ocr["fields"] = []
            for i, e in enumerate(P["expected"], start=1):
                q = page.get_by_role("region", name=f"Question {i}")
                ocr["fields"].append((q.get_by_label(f"Question {i} text").input_value(), [q.get_by_label(f"Question {i} option {k}").input_value() for k in "ABCD"],
                                      q.get_by_label(f"Question {i} correct answer").input_value()))
            check(f"OCR read {ocr['n']} questions; {ocr['flagged']} highlighted for review", ocr["n"] == 4, ocr, page)
            del regions
        step("questions are read from the photos and shown as an editable preview", page, extract)
        exact = sum(1 for (q, o, _c), e in zip(ocr["fields"], P["expected"], strict=True) if q == e["question"] and o == e["options"])
        answers = sum(1 for (_q, _o, c), e in zip(ocr["fields"], P["expected"], strict=True) if c == e["correct"])
        wrong_answers = sum(1 for (_q, _o, c), e in zip(ocr["fields"], P["expected"], strict=True) if c and c != e["correct"])
        print(f"    OCR: {exact}/4 questions with exact text and options; {answers}/4 answers from the key photo, {wrong_answers} wrong", flush=True)
        check("no answer is guessed: every answer taken from the key is the printed one (unreadable ones stay empty)", wrong_answers == 0, ocr["fields"], page)

        def diagram_blocks():
            q = page.get_by_role("region", name="Question 1")
            q.get_by_label("Question 1 needs its diagram").check()
            page.get_by_role("button", name="Import paper").click()
            page.get_by_role("dialog").get_by_role("button", name="Import").click()
            expect(page.get_by_text(re.compile("diagram", re.IGNORECASE)).first).to_be_visible()
            expect(page.get_by_text("Imported as a draft paper version")).to_have_count(0)
            page.get_by_role("region", name="Question 1").get_by_label("Question 1 needs its diagram").uncheck()
        step("a question marked as needing its diagram blocks the import (diagrams are never silently dropped)", page, diagram_blocks)

        def review_needed():
            page.get_by_role("button", name="Import paper").click()
            page.get_by_role("dialog").get_by_role("button", name="Import").click()
            expect(page.get_by_text("Nothing was imported").first).to_be_visible()
            expect(page.get_by_text("Imported as a draft paper version")).to_have_count(0)
        step("importing before the highlighted questions are checked is refused, nothing imported", page, review_needed)

        def correct():
            for i, e in enumerate(P["expected"], start=1):
                q = page.get_by_role("region", name=f"Question {i}")
                if q.get_by_label(f"Question {i} text").input_value() != e["question"]:
                    q.get_by_label(f"Question {i} text").fill(e["question"])
                for k, want in zip("ABCD", e["options"], strict=True):
                    f = q.get_by_label(f"Question {i} option {k}")
                    if f.input_value() != want:
                        f.fill(want)
                if q.get_by_label(f"Question {i} correct answer").input_value() != e["correct"]:
                    q.get_by_label(f"Question {i} correct answer").select_option(e["correct"])
                box = q.get_by_label(f"I checked question {i} against the photo")
                if box.count():
                    box.check()
            marks = page.get_by_role("group", name="Marks for questions without marks")
            marks.get_by_label("Marks for a correct answer").fill("4")
            marks.get_by_label("Negative marks for a wrong answer").fill("1")
            marks.get_by_role("button", name="Apply to questions without marks").click()
            page.get_by_role("button", name="Save corrections").click()
            expect(page.get_by_text("still to check")).to_have_count(0)
            expect(page.get_by_role("group", name="Marks for questions without marks")).to_have_count(0)
            shot(page, "photo-corrected")
        step("the teacher compares each question with its photo, corrects the OCR output and enters the unreadable answers", page, correct)

        def accept():
            page.get_by_role("button", name="Import paper").click()
            page.get_by_role("dialog").get_by_role("button", name="Import").click()
            expect(page.get_by_text("Imported as a draft paper version")).to_be_visible()
            page.get_by_role("link", name="open the paper").click()
            expect(page.get_by_text("VALIDATED").first).to_be_visible()                       # OCR never publishes: a draft version
            page.get_by_role("button", name="Approve").click()
            page.get_by_role("dialog").get_by_role("button", name=re.compile("Approve|Confirm")).click()
            page.get_by_role("button", name="Publish").click()
            page.get_by_role("dialog").get_by_role("button", name=re.compile("Publish|Confirm")).click()
            expect(page.get_by_text("PUBLISHED").first).to_be_visible()
            shot(page, "photo-paper-published")
        step("after explicit confirmation the paper is imported as a draft version, then approved and published as usual", page, accept)

        def torn():
            page.goto(UI + "/papers/photo")
            page.get_by_label("Subject").select_option(label="Physics")
            page.get_by_label("Paper title").fill("Torn chemistry page")
            page.get_by_role("button", name="Start").click()
            page.get_by_label("Add page photos").set_input_files(str(PHOTOS / "torn.jpg"))
            expect(page.get_by_text(re.compile(r"Page 1 · "))).to_be_visible(timeout=120000)
            page.get_by_role("button", name="Read questions from the photos").click()
            expect(page.get_by_role("region", name="Question 2")).to_be_visible(timeout=120000)
            expect(page.get_by_role("region", name="Question 2").get_by_text("Option C could not be read.")).to_be_visible()
            for i in (1, 2):
                page.get_by_role("region", name=f"Question {i}").get_by_label(f"I checked question {i} against the photo").check()
            marks = page.get_by_role("group", name="Marks for questions without marks")
            marks.get_by_label("Marks for a correct answer").fill("4")
            marks.get_by_label("Negative marks for a wrong answer").fill("1")
            marks.get_by_role("button", name="Apply to questions without marks").click()
            page.get_by_role("button", name="Import paper").click()
            page.get_by_role("dialog").get_by_role("button", name="Import").click()
            expect(page.get_by_text("Nothing was imported").first).to_be_visible()
            expect(page.get_by_text(re.compile(r"Question 2: .*option", re.IGNORECASE))).to_be_visible()
            shot(page, "torn-refused")
            page.reload()
            expect(page.get_by_role("region", name="Question 2")).to_be_visible()                  # the draft is preserved
            page.get_by_role("button", name="Discard").click()
            page.get_by_role("dialog").get_by_role("button", name="Discard").click()
            expect(page.get_by_role("heading", level=2, name="Photo papers in progress")).to_be_visible()
        step("a torn page (options and answers missing) is refused as a whole, the draft is kept, then discarded", page, torn)

        def submit():
            first_date(page)
            dialog = submit_paper(page, "Tara photo paper", branches=["Pune"], batches=["NEET A"], absent=["NEET B", "Every student of Pune"])
            dialog.get_by_role("button", name="Submit paper").click()
            expect(page.get_by_text("Submitted “Tara photo paper” for 2 student(s).")).to_be_visible()
            shot(page, "tara-submitted")
        step("Tara submits her photo paper for her own batch (NEET A); NEET B and the whole branch are not offered", page, submit)
        ctx.close()

        print("Teacher (Tom)")
        ctx, page = sign_in(browser, P["tb"])

        def tom():
            first_date(page)
            expect(page.get_by_text("Tara photo paper")).to_have_count(0)                         # teachers see their own submissions
            dialog = submit_paper(page, "Tom Physics Set", branches=["Pune"], batches=["NEET B"], absent=["NEET A"])
            dialog.get_by_role("button", name="Submit paper").click()
            expect(page.get_by_text("Submitted “Tom Physics Set” for 1 student(s).")).to_be_visible()
        step("a second Physics teacher submits his own paper for his own batch (NEET B) and does not see Tara's", page, tom)
        ctx.close()

        print("Branch Admin")
        ctx, page = sign_in(browser, P["ba"])

        def branch_admin():
            first_date(page)
            dialog = submit_paper(page, "Pune Chemistry Set", subject="Chemistry", branches=["Pune"], whole=["Pune"], absent=["Mumbai"])
            dialog.get_by_role("button", name="Submit paper").click()
            expect(page.get_by_text("Submitted “Pune Chemistry Set” for 3 student(s).")).to_be_visible()
            expect(row(page, "Tara photo paper")).to_be_visible()                                  # the branch's submissions
        step("Branch Admin submits Chemistry for all of Pune (Mumbai is not offered) and sees the branch's submissions", page, branch_admin)
        ctx.close()

        print("Provider Admin")
        ctx, page = sign_in(browser, P["pa"])

        def provider_admin():
            first_date(page)
            dialog = submit_paper(page, "Mumbai Chemistry Draft", subject="Chemistry", branches=["Pune", "Mumbai"], whole=["Pune", "Mumbai"])
            expect(dialog.get_by_text("This version is not approved yet")).to_be_visible()
            dialog.get_by_role("button", name="Submit paper").click()
            expect(dialog.get_by_text(re.compile("already receive another paper"))).to_be_visible()
            shot(page, "pa-overlap-refused")
            dialog.get_by_label("Every student of Pune").uncheck()
            dialog.get_by_label("Pune", exact=True).uncheck()
            dialog.get_by_role("button", name="Submit paper").click()
            expect(page.get_by_text("Submitted “Mumbai Chemistry Draft” for 1 student(s).")).to_be_visible()
            for title in ("Tara photo paper", "Tom Physics Set", "Pune Chemistry Set"):
                expect(row(page, title)).to_be_visible()
            expect(page.get_by_text("Ravi Biology Set")).to_have_count(0)
            shot(page, "pa-coverage")
        step("Provider Admin: an overlapping audience is refused; the corrected one (Mumbai, unapproved paper) is accepted; "
             "all of North's submissions are visible, no other provider's", page, provider_admin)
        ctx.close()

        print("Independent tutor")
        ctx, page = sign_in(browser, P["tutor"])

        def tutor():
            first_date(page)
            dialog = submit_paper(page, "Ravi Biology Set", subject="Biology", branches=["Home classes"], students=P["tutor_students"])
            dialog.get_by_role("button", name="Submit paper").click()
            expect(page.get_by_text("Submitted “Ravi Biology Set” for 2 student(s).")).to_be_visible()
            expect(page.get_by_text("Tara photo paper")).to_have_count(0)
        step("an independent tutor without batches submits for two students picked individually; sees only their own", page, tutor)
        ctx.close()

    else:
        # ---------------------------------------------------------------- Students, in the Student portal
        def student_sign_in(sid):
            ctx = new_context(browser)
            page = ctx.new_page()
            page.set_default_timeout(20000)
            page.goto(SUI + "/login")
            page.get_by_label("Student ID").fill(sid)
            page.get_by_label("Password", exact=True).fill(P["password"])
            page.locator("button[type=submit]").click()
            page.wait_for_url(re.compile(r"/dashboard"), timeout=20000)
            return ctx, page

        print("Student of another branch (Chitra)")
        ctx, page = student_sign_in(P["s3"])

        def chitra():
            page.goto(SUI + "/scheduled-tests")
            card = page.locator("article").filter(has_text="National Mock")
            expect(card).to_have_count(1)                                                        # one card, nothing guessed or borrowed
            expect(card.get_by_text("No paper available")).to_be_visible()
            expect(card.get_by_role("button", name=re.compile("Start Test|Resume Test"))).to_have_count(0)
            page.goto(SUI + "/tests")
            card = page.locator("article").filter(has_text="National Mock")
            expect(card.get_by_role("button", name=re.compile("Start Test|Resume Test"))).to_have_count(0)
            card.get_by_role("button", name=re.compile("No paper available")).click()
            page.wait_for_url(re.compile(r"/tests/[0-9a-f-]+$"))
            items = page.locator(".test-details-sections li")
            expect(items).to_have_count(len(P["subject_order"]))
            check("her card lists every configured subject in order, each with the reason she has no paper",
                  [items.nth(i).locator("strong").inner_text() for i in range(items.count())] == P["subject_order"]
                  and "not approved in time" in page.locator(".test-details-sections").inner_text(), page.locator(".test-details-sections").inner_text(), page)
            expect(page.get_by_text(re.compile("cannot be started"))).to_be_visible()
            expect(page.get_by_role("button", name="Take Test")).to_have_count(0)
            shot(page, "chitra-card")
        step("a student with no paper for the date gets ONE Global Test card that explains each subject and cannot be started", page, chitra)
        ctx.close()

        print("Eligible student (Asha)")
        ctx, page = student_sign_in(P["s1"])
        leaks: list[str] = []
        submitted = {"done": False}

        def watch(resp):
            if submitted["done"] or "application/json" not in (resp.headers.get("content-type") or ""):
                return
            try:
                body = resp.text()
            except Exception:  # noqa: BLE001 - body not available (redirect etc.)
                return
            if re.search(r'"(correct_answer|correct_option|correct|answer_key|is_correct)"\s*:', body):
                leaks.append(resp.url)
        ctx.on("response", watch)                                                                    # every tab of this session

        def one():
            for path in ("/tests", "/scheduled-tests"):
                page.goto(SUI + path)
                expect(page.get_by_text("National Mock").first).to_be_visible()
                expect(page.locator("text=National Mock >> visible=true")).to_have_count(1)        # ONE card, not one per provider
                card = page.locator("article").filter(has_text="National Mock")
                expect(card.get_by_text("Global Test")).to_be_visible()                             # shown as the Global Test ...
                expect(card.get_by_text("North Academy")).to_have_count(0)                          # ... never as one of her institutes
                expect(card.get_by_text("Ravi Tutorials")).to_have_count(0)
            shot(page, "asha-tests")
        step("Asha (enrolled with two providers) sees ONE Global Test card for the date", page, one)

        def take():
            page.goto(SUI + "/tests")
            card = page.locator("div, article, li").filter(has_text="National Mock").filter(has=page.get_by_role("button", name="Start Test")).last
            with ctx.expect_page() as opened:                                                    # the portal opens the attempt in a new tab
                card.get_by_role("button", name="Start Test").click()
            tab = opened.value
            tab.set_default_timeout(20000)
            tab.wait_for_url(re.compile(rf"/tests/{P['asha_test']}/attempt"))
            expect(tab.get_by_text("Before you begin")).to_be_visible()                          # exam rules before anything starts
            tab.get_by_role("button", name="I understand, start in fullscreen").click()
            header = tab.locator(".test-attempt-question-header strong")
            expect(tab.locator(".test-attempt-question h2")).to_be_visible()
            expect(tab.locator(".test-attempt-header h1")).to_have_text("National Mock")       # the canonical name, no provider or date
            tabs = [t.strip() for t in tab.locator(".test-attempt-section").all_inner_texts()]
            check("tabs: every configured subject, in the configured order", tabs == ["All Sections", *P["subject_order"]], tabs, tab)

            def index():
                return int(re.search(r"Question (\d+) of", header.inner_text()).group(1))
            total = int(re.search(r"of (\d+)", header.inner_text()).group(1))
            check("one exam with the 9 questions of her three papers (4 Physics photo paper + 3 Chemistry from North, 2 Biology from the tutor)",
                  total == 9, total, tab)
            subjects_seen = []
            stems = []
            answered = 0
            for i in range(1, total + 1):
                expect(header).to_contain_text(f"Question {i} of")
                stem = tab.locator(".test-attempt-question h2").inner_text().strip()
                stems.append(stem)
                subjects_seen.append("Biology" if stem.startswith("Ravi Biology") else "Chemistry" if stem.startswith("Pune Chemistry") else "Physics")
                want = P["answers"].get(stem)
                if want:
                    tab.locator(".test-attempt-option").filter(has_text=re.compile(rf"^\s*[A-D]\.\s*{re.escape(want)}\s*$")).click()
                    answered += 1
                if i < total:
                    tab.get_by_role("button", name="Save & Next").click()
                if i == 2:                                                                       # resume: reload in the middle of the attempt
                    tab.reload()
                    tab.get_by_role("button", name="I understand, start in fullscreen").click()     # resuming: rules again, same attempt
                    expect(tab.locator(".test-attempt-question h2")).to_be_visible()
                    while index() > 1:
                        tab.get_by_role("button", name="Previous").click()
                    for q in (1, 2):
                        stem_q = tab.locator(".test-attempt-question h2").inner_text().strip()
                        check(f"after the reload question {q} is the same question as before", stem_q == stems[q - 1], (stem_q, stems[:2]), tab)
                        if P["answers"].get(stem_q):
                            expect(tab.locator(".test-attempt-option.selected")).to_have_count(1)   # the saved answer survived the reload
                        tab.get_by_role("button", name="Save & Next").click()
                        del q
            groups = [x for i, x in enumerate(subjects_seen) if i == 0 or subjects_seen[i - 1] != x]
            check("subjects in the configured order; inside each, her own stable order (the one computed for her alone)",
                  groups == P["subject_order"] and [x.strip() for x in P["expected_order"]] == stems, (groups, stems, P["expected_order"]), tab)
            check("answered 7 of 9 questions as planned, questions grouped by subject (4 Physics, 3 Chemistry, 2 Biology, each subject together)",
                  answered == 7 and len(groups) == 3 and {x: subjects_seen.count(x) for x in groups} == {"Physics": 4, "Chemistry": 3, "Biology": 2},
                  (answered, subjects_seen), tab)
            shot(tab, "asha-attempt")
            tab.once("dialog", lambda d: d.accept())
            submitted["done"] = True
            tab.get_by_role("button", name="Submit Test").click()
            tab.wait_for_url(re.compile(r"/result"), timeout=30000)
            expect(tab.get_by_text(re.compile(r"\b23\b")).first).to_be_visible(timeout=20000)
            shot(tab, "asha-result")
        step("she takes her one Global Test exam: answers, reloads mid-way (resume keeps answers), submits, sees one overall score", page, take)
        check("no correct answer reached the browser before submission", not leaks, leaks, page)
        ctx.close()

    browser.close()

done()
