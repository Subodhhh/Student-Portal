"""The paper Review journey on the RUNNING local instance (scripts/local-stack.sh up; http://localhost:18081), in Chromium:
sign in -> Question Bank & Papers > Import Processing -> upload -> Review -> resolve doubts -> import -> open the stored paper ->
approve -> REOPEN it (a fresh page load from the server) and check the rendering again, with a close-up of every question.

Usage: python localhost_review_demo.py <params.json>   (params as for review_ui_browser.py; driven by scripts/local-stack.sh demo)."""
import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

import review_ui_browser as rv

P = rv.P


def reopen(page, paper):
    """The approved paper again from the server: every question typeset from stored data, nothing raw; a close-up of each."""
    title = paper["title"]
    url = page.url
    page.goto(url)                                                                  # a fresh load: nothing from the import screen
    expect(page.get_by_text("Questions (preview")).to_be_visible()
    card = page.locator(".card, section").filter(has_text="Questions (preview").first
    stored = card.evaluate("""(root) => { const c = root.cloneNode(true); c.querySelectorAll('.katex, code').forEach((e) => e.remove());
      return {katex: root.querySelectorAll('.katex').length, errors: root.querySelectorAll('.katex-error, .math-fallback').length,
              items: root.querySelectorAll('article').length, mfrac: root.querySelectorAll('.katex .mfrac').length,
              sqrt: root.querySelectorAll('.katex .sqrt').length, limits: root.querySelectorAll('.katex .op-limits').length,
              mtable: root.querySelectorAll('.katex .mtable').length, scripts: root.querySelectorAll('.katex .msupsub').length,
              text: c.innerText}; }""")
    hits = rv.RAW.findall(stored["text"])
    rv.check(f"{title}: reopened from the server, every question typeset again (no fallback, no raw LaTeX)",
             stored["katex"] > 0 and not stored["errors"] and not hits, {**stored, "text": None, "raw": hits[:5]}, page)
    expect(page.get_by_role("button", name="Publish")).to_be_visible()                # the reopened paper is APPROVED
    slug = re.sub(r"\W+", "-", title.lower())
    for i in range(stored["items"]):
        card.locator("article").nth(i).screenshot(path=str(rv.SHOTS / f"{slug}-q{i + 1:02d}.png"))
    rv.report.setdefault(title, {})["reopened"] = {k: v for k, v in stored.items() if k != "text"}


with sync_playwright() as pw:
    browser = pw.chromium.connect(P["ws"])
    ctx, page = rv.sign_in(browser, P["teacher"])
    console, duplicates = [], [0]
    page.on("console", lambda msg: console.append(msg.text) if msg.type == "error" else None)
    page.add_init_script("document.addEventListener('securitypolicyviolation', (e) => console.error('CSP ' + e.violatedDirective))")
    for paper in P["papers"]:
        print(f"-- {paper['title']}")
        try:
            rv.review(page, paper)
            if rv.report.get(paper["title"], {}).get("approved"):
                reopen(page, paper)
        except Exception as exc:  # noqa: BLE001 - a failed step is a failed check with a screenshot
            if page.get_by_text("An identical paper already exists").count():
                # imported by an earlier run on this instance: the product refuses the identical file (409 DUPLICATE_PAPER) -
                # open the stored paper from the Papers list instead and check it the same way
                duplicates[0] += 1
                rv.check(f"{paper['title']}: the identical file is refused (already imported earlier on this instance)", True)
                page.goto(rv.UI + "/papers?tab=papers")
                page.get_by_role("link", name=paper["title"], exact=True).first.click()
                page.wait_for_url(re.compile(r"/papers/[0-9a-f-]+$"))
                reopen(page, paper)
            else:
                rv.check(f"{paper['title']}: localhost journey", False, f"{type(exc).__name__}: {exc}", page)
    other = [m for m in console if "status of 409" not in m]
    rv.check("no console errors and no Content-Security-Policy violations on the local instance (only the refused duplicates' 409)",
             not other and sum("status of 409" in m for m in console) == duplicates[0], console[:5])
    ctx.close()

Path(P["report"]).write_text(__import__("json").dumps(rv.report, ensure_ascii=False, indent=1))
failed = [n for n, ok in rv.results if not ok]
print(f"\n{len(rv.results) - len(failed)}/{len(rv.results)} localhost checks passed" + (f"; FAILED: {failed}" if failed else ""))
sys.exit(1 if failed else 0)
