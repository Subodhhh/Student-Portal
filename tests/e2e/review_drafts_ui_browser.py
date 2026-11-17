"""Browser journey for SAVED import reviews (review drafts), driven by tests/e2e/review_ui_disposable.py after the Review journey.
Usage: python review_drafts_ui_browser.py <params.json>   (needs playwright==1.48).

A teacher starts a review, decisions autosave, a reload and choosing the same file again resume it; a provider admin opens the same
review in a second browser and both resolve same-field conflicts (keep mine / use theirs); Import stays disabled while a save is
pending or failing and is refused when someone changed the review meanwhile; Close keeps the review, Discard ends it; a review saved
before an engine update asks to confirm each decision before importing."""
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

import review_ui_browser as rv
from review_ui_browser import P, UI, check, results, shot, sign_in

D = P["drafts"]
SAVED = re.compile(r"All changes saved at")


def field(page, n):
    return page.get_by_test_id(f"rev-{n}-question")


def keep(page, n):
    field(page, n).get_by_label(re.compile("Keep the text exactly as in the file")).check()


def correct(page, n, text):
    f = field(page, n)
    f.get_by_label("Correct it").check()
    f.get_by_label(re.compile("Corrected text")).fill(text)


def status(page):
    return page.locator(".save-status")


def importer(page):
    return page.locator(".recognition-review").get_by_role("button", name=re.compile(r"Import as a draft paper|still need a decision|Saving your decisions|Resolve the changes"))


def start(page, title):
    page.goto(UI + "/papers?tab=imports")
    page.get_by_label("Subject").select_option(label=D["subject"])
    page.get_by_label("Paper title").fill(title)
    page.get_by_label(re.compile("Question file")).set_input_files(D["file"])
    page.get_by_role("button", name="Validate & preview").click()
    expect(page.get_by_text("Preview before import")).to_be_visible(timeout=60000)


def step(name, fn, page):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - a failed step is a failed check with a screenshot
        check(name, False, f"{type(exc).__name__}: {exc}", page)


def main():
    report = {}
    with sync_playwright() as pw:
        browser = pw.chromium.connect(P["ws"])
        tctx, t = sign_in(browser, P["teacher"])
        console = []
        t.on("console", lambda msg: console.append(msg.text) if msg.type == "error" else None)

        def started():
            start(t, D["title"])
            keep(t, 1)
            saving = status(t).inner_text()
            check("a decision is saved shortly after it is made; Import stays disabled while it is pending",
                  saving == "Saving…" and importer(t).is_disabled(), [saving, importer(t).inner_text()], t)
            expect(status(t)).to_have_text(SAVED, timeout=15000)
            report["draft_id"] = re.search(r"draft=([0-9a-f-]+)", t.url).group(1)
            check("the review's address names the saved draft (a reload reopens it)", bool(report["draft_id"]), t.url, t)
            shot(t, "draft-autosaved")
        step("start a review and autosave", started, t)

        def reload():
            t.reload()
            expect(field(t, 1)).to_be_visible(timeout=60000)
            check("after a browser reload the same review reopens with the decision made",
                  field(t, 1).get_by_label(re.compile("Keep the text exactly")).is_checked(), t.url, t)
        step("reload and resume", reload, t)

        def failing_save():
            t.route("**/api/v1/review-drafts/*", lambda route: route.abort() if route.request.method == "PATCH" else route.continue_())
            keep(t, 2)
            expect(status(t)).to_have_text(re.compile("Not saved - retrying"), timeout=15000)
            check("a failed save is shown and Import stays disabled until it succeeds", importer(t).is_disabled(), importer(t).inner_text(), t)
            shot(t, "draft-save-failed")
            t.unroute("**/api/v1/review-drafts/*")
            expect(status(t)).to_have_text(SAVED, timeout=20000)                     # retried automatically
            expect(importer(t)).to_have_text("Import as a draft paper", timeout=10000)
            check("the failed save is retried and the review becomes importable", importer(t).is_enabled(), importer(t).inner_text(), t)
        step("failed save blocks import", failing_save, t)

        def close_and_list():
            t.get_by_role("button", name="Close (continue later)").click()
            card = t.locator(".card").filter(has=t.get_by_role("heading", name="Reviews in progress"))
            expect(card).to_be_visible(timeout=15000)
            check("Close keeps the review: it is listed under Reviews in progress", card.get_by_text(D["title"]).count() == 1, card.inner_text(), t)
            shot(t, "draft-listed")
            start(t, D["title"])                                                      # the same source file chosen again
            check("choosing the same file again resumes that review with its decisions",
                  t.get_by_text("You had already started reviewing this file").is_visible()
                  and field(t, 1).get_by_label(re.compile("Keep the text exactly")).is_checked()
                  and field(t, 2).get_by_label(re.compile("Keep the text exactly")).is_checked()
                  and report["draft_id"] in t.url, t.url, t)
        step("close, list and resume by file", close_and_list, t)

        # ---- a second editor in another browser: the provider admin opens the same review
        actx, a = sign_in(browser, P["admin"])
        a.on("console", lambda msg: console.append(msg.text) if msg.type == "error" else None)

        def conflicts():
            a.goto(UI + f"/papers?tab=imports&draft={report['draft_id']}")
            expect(field(a, 2)).to_be_visible(timeout=60000)
            # the teacher changes question 2 first; the admin, still on the older revision, then changes the same field
            correct(t, 2, D["teacher_text"])
            expect(status(t)).to_have_text(SAVED, timeout=15000)
            correct(a, 2, D["admin_text"])
            alert = a.get_by_text(re.compile("was changed by someone else"))
            expect(alert).to_be_visible(timeout=15000)
            check("a same-field change by another editor is never overwritten: both versions are shown",
                  alert.is_visible() and importer(a).is_disabled(), importer(a).inner_text(), a)
            shot(a, "draft-conflict")
            a.get_by_role("button", name="Keep mine").click()
            expect(status(a)).to_have_text(SAVED, timeout=15000)
            check("'Keep mine' saves this editor's version deliberately", True)
            # now the teacher, on the older revision, changes the same field again: conflict the other way; the teacher takes theirs
            correct(t, 2, D["teacher_text"] + " ")
            expect(t.get_by_role("button", name="Use their version")).to_be_visible(timeout=15000)
            t.get_by_role("button", name="Use their version").click()
            box = field(t, 2).get_by_label(re.compile("Corrected text"))
            check("'Use their version' shows the other editor's correction in this browser", box.input_value() == D["admin_text"], box.input_value(), t)
        step("two editors, same field", conflicts, a)

        def stale_import():
            expect(importer(t)).to_have_text("Import as a draft paper", timeout=15000)
            correct(a, 1, D["admin_q1"])                                             # the admin changes the review meanwhile
            expect(status(a)).to_have_text(SAVED, timeout=15000)
            importer(t).click()
            msg = t.get_by_text(re.compile("Someone else changed this review just now"))
            expect(msg).to_be_visible(timeout=15000)
            check("Import is refused when the review changed since this browser last saw it", msg.is_visible(), "", t)
            shot(t, "draft-import-refused")
        step("import refused on a newer revision", stale_import, t)
        actx.close()

        def discard():
            t.get_by_role("button", name="Discard this review").click()
            expect(t.get_by_text("Preview before import")).to_have_count(0, timeout=15000)
            listed = t.locator(".card").filter(has=t.get_by_role("heading", name="Reviews in progress"))
            check("Discard ends the review: it is no longer offered to continue",
                  listed.count() == 0 or listed.get_by_text(D["title"]).count() == 0, "", t)
        step("discard", discard, t)

        def engine_update():
            t.goto(UI + f"/papers?tab=imports&draft={D['stale_draft']}")
            warn = t.get_by_text(re.compile(r"recognition was improved since you saved this review"))
            expect(warn).to_be_visible(timeout=60000)
            check("a review saved before an engine update is marked and its decision must be confirmed before importing",
                  importer(t).is_disabled() and field(t, 1).get_by_role("button", name="Confirm this decision").is_visible(), importer(t).inner_text(), t)
            shot(t, "draft-engine-changed")
            field(t, 1).get_by_role("button", name="Confirm this decision").click()
            expect(importer(t)).to_have_text("Import as a draft paper", timeout=15000)
            importer(t).click()
            expect(t.get_by_text(re.compile(r"\d+ questions validated"))).to_be_visible(timeout=60000)
            check("after confirming, the review imports", True)
        step("engine update", engine_update, t)

        expected = [m for m in console if "status of 409" in m or "ERR_FAILED" in m or "net::" in m]   # the conflicts and the aborted saves
        other = [m for m in console if m not in expected and "CSP" not in m]
        check("no console errors other than the expected conflicts and aborted saves, and no CSP violation",
              not other and not [m for m in console if m.startswith("CSP")], console[:6])
        tctx.close()

    Path(P["drafts_report"]).write_text(json.dumps(report))
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} draft browser checks passed" + (f"; FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    rv.SHOTS.mkdir(parents=True, exist_ok=True)
    main()
