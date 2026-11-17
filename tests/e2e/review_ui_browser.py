"""Browser journey for the Institute paper Review (driven by tests/e2e/review_ui_disposable.py, which owns the data and the servers).
Usage: python review_ui_browser.py <params.json>   (needs playwright==1.48)."""
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
report: dict = {}
refusals = [0]
n_shot = [0]
# raw LaTeX, XML or internal markers must never be visible outside the "In the file" source (<code>) or a correction box
RAW = re.compile(r"\\(?:frac|mathrm|mathbf|lim|limits|sum|int|sqrt|begin|left|right|alpha|times)\b|\\\(|\\\)|[\ue000-\uf8ff]|</?m:")


def shot(page, name):
    n_shot[0] += 1
    page.screenshot(path=str(SHOTS / f"rv-{n_shot[0]:02d}-{name}.png"), full_page=True)


def check(name, ok, detail="", page=None):
    results.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {str(detail)[:400]}"), flush=True)
    if not ok and page is not None:
        shot(page, "FAIL")


def mail_lines() -> list[str]:
    path = Path(P["mail_log"])
    return path.read_text().splitlines() if path.exists() else []


def sign_in(browser, code, viewport=(1366, 900)):
    ctx = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, timezone_id="Asia/Kolkata")
    page = ctx.new_page()
    page.set_default_timeout(30000)
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
    return ctx, page


MEASURE = """(root) => {
  const q = (s) => root.querySelectorAll(s).length;
  const clone = root.cloneNode(true);
  clone.querySelectorAll('code, textarea, .katex').forEach((e) => e.remove());
  const fields = [...root.querySelectorAll('.recognition-field')];
  return {katex: q('.katex'), errors: q('.katex-error') + q('.math-fallback'), mfrac: q('.katex .mfrac'), sqrt: q('.katex .sqrt'),
          'op-limits': q('.katex .op-limits'), mtable: q('.katex .mtable'), msupsub: q('.katex .msupsub'), mathbf: q('.katex .mathbf'),
          needs: q('.recognition-field.needs-attention'), text: clone.innerText,
          overflow: fields.filter((f) => f.scrollWidth > f.clientWidth + 1).map((f) => f.dataset.testid)};
}"""


def review(page, paper):
    title = paper["title"]
    page.goto(UI + "/papers?tab=imports")
    page.get_by_label("Subject").select_option(label=paper["subject"])
    page.get_by_label("Paper title").fill(title)
    page.get_by_label(re.compile("Question file")).set_input_files(paper["file"])
    page.get_by_role("button", name="Validate & preview").click()
    heading = page.get_by_text("Preview before import")
    refused = page.get_by_text("The paper was rejected")
    expect(heading.or_(refused)).to_be_visible(timeout=60000)
    if not heading.is_visible():
        # a genuine file the import refuses as a whole: the reasons are shown, nothing is imported
        text = page.locator("main").inner_text()
        shot(page, re.sub(r"\W+", "-", title.lower()) + "-refused")
        refusals[0] += 1                                                  # the browser logs the refused upload's 422 itself
        check(f"{title}: refused as a whole with every reason shown (nothing imported)", "Option A is empty" in text, text[:300], page)
        report[title] = {"imported": False, "refused": text[:600]}
        return
    box = page.locator(".recognition-review")
    only = box.get_by_label("Show only questions that need attention")
    if only.is_checked():
        only.uncheck()                                                     # every question, to check every rendering
    page.wait_for_timeout(500)
    m = box.evaluate(MEASURE)
    questions = box.locator("article.recognition-question").count()
    shot(page, re.sub(r"\W+", "-", title.lower()) + "-review")
    want_q = paper["questions"] if paper["questions"] is not None else questions
    check(f"{title}: Review shows all {want_q} questions", questions == want_q, questions, page)
    check(f"{title}: every converted expression is typeset (KaTeX), none fell back to its source", m["katex"] > 0 and m["errors"] == 0,
          {k: m[k] for k in ("katex", "errors")}, page)
    hits = RAW.findall(m["text"])
    check(f"{title}: no raw LaTeX, XML or internal marker visible as the displayed text", not hits, hits[:10], page)
    for k, n in paper["structures"].items():
        check(f"{title}: rendered structure '{k}' present at least {n} time(s)", m[k] >= n, {k: m[k]}, page)
    check(f"{title}: no field overflows its box (long equations stay inside)", not m["overflow"], m["overflow"], page)
    # uncertain fields: marked, explained, and blocking until the teacher decides
    flagged = box.locator(".recognition-field.needs-attention")
    n_flagged = flagged.count()
    imp = box.get_by_role("button", name=re.compile("Import as a draft paper|still need a decision"))
    if n_flagged:
        check(f"{title}: {n_flagged} uncertain field(s) marked 'Needs review'/'Not extracted' with the reason, the import blocked",
              imp.is_disabled() and flagged.first.locator(".recognition-reasons li").count() > 0, imp.inner_text(), page)
        flagged.first.scroll_into_view_if_needed()
        shot(page, re.sub(r"\W+", "-", title.lower()) + "-flagged")
    while (pending := box.locator(".recognition-field.needs-attention").first).count():
        tid = pending.get_attribute("data-testid")
        f = box.get_by_test_id(tid)                          # a decision removes "needs-attention": address the field by its id
        key = re.match(r"rev-(\d+)-(\w+)", tid)
        k = f"{key.group(1)}:{key.group(2)}"
        readings = f.get_by_role("group", name=re.compile("How should .* be read"))
        if k in paper["edits"]:
            f.get_by_label("Correct it").check()
            f.get_by_label(re.compile("Corrected text")).fill(paper["edits"][k])
            expect(f.locator(".recognition-preview .katex").first).to_be_visible()
            shot(page, re.sub(r"\W+", "-", title.lower()) + "-corrected")
        elif readings.count():
            # unit or variable: the teacher picks the upright unit reading offered (a quantity after a number in a science paper)
            readings.first.get_by_role("button").first.click()
            expect(f.locator(".recognition-preview .katex").first).to_be_visible()
            shot(page, re.sub(r"\W+", "-", title.lower()) + "-reading-chosen")
        elif f.get_by_label(re.compile("Keep the text exactly as in the file")).count():
            f.get_by_label(re.compile("Keep the text exactly as in the file")).check()
        else:
            f.get_by_label("Correct it").check()                         # not extracted: the teacher types it (here: as in the file)
        page.wait_for_timeout(100)
    expect(imp).to_be_enabled()
    imp.click()
    expect(page.get_by_text(re.compile(r"\d+ questions validated"))).to_be_visible(timeout=60000)
    shot(page, re.sub(r"\W+", "-", title.lower()) + "-imported")
    check(f"{title}: imported as a draft paper after every uncertain field had a decision", True)
    report[title] = {"imported": True, "questions": questions, "flagged": n_flagged, **{k: m[k] for k in m if k not in ("text", "overflow")}}
    if paper.get("approve"):
        # the imported draft as stored: every question, option and explanation typeset again from the database, then approved
        page.get_by_role("link", name="Review it").click()
        page.wait_for_url(re.compile(r"/papers/[0-9a-f-]+$"))
        expect(page.get_by_text("Questions (preview")).to_be_visible()
        card = page.locator(".card, section").filter(has_text="Questions (preview").first
        stored = card.evaluate("""(root) => { const c = root.cloneNode(true); c.querySelectorAll('.katex, code').forEach((e) => e.remove());
          return {katex: root.querySelectorAll('.katex').length, errors: root.querySelectorAll('.katex-error, .math-fallback').length,
                  items: root.querySelectorAll('article').length, text: c.innerText}; }""")
        hits = RAW.findall(stored["text"])
        check(f"{title}: the stored draft shows all {questions} questions typeset (no fallback, no raw LaTeX)",
              stored["items"] == questions and stored["katex"] > 0 and not stored["errors"] and not hits, {**stored, "text": None, "raw": hits[:5]}, page)
        shot(page, re.sub(r"\W+", "-", title.lower()) + "-stored")
        page.get_by_role("button", name="Approve").click()
        page.get_by_role("dialog").get_by_role("button", name=re.compile("Approve|Confirm")).click()
        expect(page.get_by_role("button", name="Publish")).to_be_visible(timeout=30000)        # APPROVED: the next step is offered
        shot(page, re.sub(r"\W+", "-", title.lower()) + "-approved")
        check(f"{title}: approved through the portal", True)
        report[title]["approved"] = True


def main():
    """The Review journey of review_ui_disposable.py (other journeys import review() and the helpers)."""
    with sync_playwright() as pw:
        browser = pw.chromium.connect(P["ws"])
        ctx, page = sign_in(browser, P["teacher"])
        console = []                                                   # CSP violations arrive here too (securitypolicyviolation)
        page.on("console", lambda msg: console.append(msg.text) if msg.type == "error" else None)
        page.add_init_script("document.addEventListener('securitypolicyviolation', (e) => console.error('CSP ' + e.violatedDirective + ' ' + e.blockedURI))")
        # evidence for a navigation that never finishes (an intermittent 30 s goto timeout was seen once): what was still in flight
        inflight: dict = {}
        page.on("request", lambda r: inflight.__setitem__(id(r), (time.monotonic(), r.method, r.url, r.resource_type)))
        page.on("requestfinished", lambda r: inflight.pop(id(r), None))
        page.on("requestfailed", lambda r: inflight.pop(id(r), None))
        # a 'leave page?' prompt would block goto; recorded, then dismissed exactly as Playwright does without a listener
        page.on("dialog", lambda d: (console.append(f"DIALOG {d.type}: {d.message}"), d.dismiss()))
        for paper in P["papers"]:
            print(f"-- {paper['title']}")
            try:
                review(page, paper)
            except Exception as exc:  # noqa: BLE001 - a failed step is a failed check with a screenshot
                now = time.monotonic()
                pending = sorted(((round(now - t0, 1), m, u, k) for t0, m, u, k in inflight.values()), reverse=True)[:8]
                check(f"{paper['title']}: Review journey", False, f"{type(exc).__name__}: {exc} | url={page.url} | in flight={pending} "
                      f"| dialogs={[m for m in console if m.startswith('DIALOG')]}", page)
        expected = [m for m in console if "status of 422" in m][:refusals[0]]       # one per refused upload, nothing else
        other = [m for m in console if m not in expected]
        check("no console errors and no Content-Security-Policy violations in the whole journey (only the refused upload's 422)",
              not other and len(expected) == refusals[0], console[:5])
        ctx.close()

        # a phone-sized screen: the same Review stays readable, nothing wider than the screen
        ctx, page = sign_in(browser, P["teacher"], viewport=(390, 844))
        math = P["papers"][0]
        page.goto(UI + "/papers?tab=imports")
        page.get_by_label("Subject").select_option(label=math["subject"])
        page.get_by_label("Paper title").fill(math["title"] + " (phone)")
        page.get_by_label(re.compile("Question file")).set_input_files(math["file"])
        page.get_by_role("button", name="Validate & preview").click()
        expect(page.get_by_text("Preview before import")).to_be_visible(timeout=60000)
        only = page.get_by_label("Show only questions that need attention")
        if only.is_checked():
            only.uncheck()
        page.wait_for_timeout(500)
        wide = page.evaluate("() => ({page: document.documentElement.scrollWidth, view: window.innerWidth, "
                             "fields: [...document.querySelectorAll('.recognition-field')].filter((f) => f.scrollWidth > f.clientWidth + 1).length})")
        shot(page, "phone-review")
        check("on a 390 px phone screen the Review has no horizontal page scroll and no field overflows", wide["page"] <= wide["view"] and not wide["fields"], wide, page)
        ctx.close()

    Path(P["report"]).write_text(json.dumps(report, ensure_ascii=False, indent=1))
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} browser checks passed" + (f"; FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
