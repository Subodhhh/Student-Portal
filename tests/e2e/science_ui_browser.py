"""Student portal journey for the Mathematics / Physics / Chemistry E2E (driven by tests/e2e/science_pipeline_disposable.py, which owns
the disposable environment). Runs INSIDE the Playwright image (mcr.microsoft.com/playwright/python:v1.48.0-noble), launching its own
headless Chromium. Checks: sign-in, the attempt, KaTeX rendering of every question (fractions, matrices, scripts, Greek letters, bold
vectors), no raw LaTeX and no rendering fallback on screen, no JavaScript errors, no Content-Security-Policy violation, answers,
submission and the result. Writes screenshots and browser_report.json to params["shots"].
Usage: python3 science_ui_browser.py <params.json>"""
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

P = json.loads(Path(sys.argv[1]).read_text())
SUI, SHOTS = P["sui"], Path(P["shots"])
results: list[tuple[str, bool, str]] = []
report: dict = {"questions": [], "console_errors": [], "page_errors": [], "csp_violations": [], "failed_requests": [], "http_errors": []}
RAW = re.compile(r"\\[A-Za-z]+|\\\(|\\\)|\\\[|\\\]|\^\{|_\{|\$\$")          # LaTeX source that should never be visible
n_shot = [0]
state = {"signed_in": False}


def shot(page, name):
    n_shot[0] += 1
    page.screenshot(path=str(SHOTS / f"sci-{n_shot[0]:02d}-{name}.png"), full_page=True)


def check(name, ok, detail=""):
    results.append((name, bool(ok), "" if ok else str(detail)[:500]))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {str(detail)[:500]}"), flush=True)


def finish():
    report["checks"] = [{"check": n, "passed": ok, "detail": d} for n, ok, d in results]
    (SHOTS / "browser_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} browser checks passed" + (f"; FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


CSP_HOOK = """window.__csp = [];
document.addEventListener('securitypolicyviolation', e => window.__csp.push(e.violatedDirective + ' ' + (e.blockedURI || '') + ' ' + (e.sourceFile || '')));"""


def watch(page):
    page.on("console", lambda m: report["console_errors"].append(f"{page.url} :: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: report["page_errors"].append(f"{page.url} :: {e}"))
    page.on("requestfailed", lambda r: report["failed_requests"].append(f"{r.method} {r.url} :: {r.failure}"))
    page.on("response", lambda r: report["http_errors"].append(
        {"status": r.status, "method": r.request.method, "url": r.url, "before_sign_in": not state["signed_in"]}) if r.status >= 400 else None)


def csp_of(page):
    try:
        return page.evaluate("window.__csp || []")
    except Exception:  # noqa: BLE001 - the page is already closed
        return []


try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, timezone_id="Asia/Kolkata")
        ctx.add_init_script(CSP_HOOK)
        ctx.on("page", watch)
        page = ctx.new_page()
        page.set_default_timeout(25000)

        # ------------------------------------------------------------------ sign-in
        resp = page.goto(SUI + "/login")
        report["document_csp_header"] = resp.headers.get("content-security-policy") if resp else None
        check("the portal document arrives with a Content-Security-Policy header", bool(report["document_csp_header"]), resp and resp.headers)
        page.get_by_label("Student ID").fill(P["student"])
        page.get_by_label("Password", exact=True).fill(P["password"])
        page.locator("button[type=submit]").click()
        page.wait_for_url(re.compile(r"/dashboard"), timeout=25000)
        page.wait_for_load_state("networkidle")                            # let the dashboard's own requests finish before moving on
        state["signed_in"] = True
        check("the student signs in with the Student ID and reaches the dashboard", True)
        shot(page, "dashboard")

        # ------------------------------------------------------------------ the test is listed and opens
        card = None
        for _ in range(30):                                                # the window opens a few seconds after delivery
            page.goto(SUI + "/tests")
            page.wait_for_load_state("networkidle")
            card = page.locator("div, article, li").filter(has_text=P["exam"]).filter(has=page.get_by_role("button", name="Start Test")).last
            if card.count():
                break
            page.wait_for_timeout(2000)
        check("the delivered exam is listed with Start Test", card is not None and card.count() == 1)
        shot(page, "tests")
        with ctx.expect_page() as opened:
            card.get_by_role("button", name="Start Test").click()
        tab = opened.value
        tab.set_default_timeout(25000)
        tab.wait_for_url(re.compile(rf"/tests/{P['test_id']}/attempt"))
        tab.get_by_role("button", name="I understand, start in fullscreen").click()
        header = tab.locator(".test-attempt-question-header strong")
        expect(tab.locator(".test-attempt-question h2")).to_be_visible()
        total = int(re.search(r"of (\d+)", header.inner_text()).group(1))
        check(f"the attempt shows all {P['expect_total']} questions", total == P["expect_total"], total)

        # ------------------------------------------------------------------ every question: rendering, then the planned answer
        features = {"fraction": 0, "matrix": 0, "scripts": 0, "greek": 0, "bold_vector": 0, "upright_unit": 0}
        answered = 0
        for i in range(1, total + 1):
            expect(header).to_contain_text(f"Question {i} of")
            area = tab.locator(".test-attempt-question")
            area.locator(".katex").first.wait_for(timeout=3000) if area.locator(".katex").count() else None
            stem_el = tab.locator(".test-attempt-question h2")
            stem = stem_el.inner_text().strip()
            options = [o.inner_text().strip() for o in tab.locator(".test-attempt-option").all()]
            visible = stem + "\n" + "\n".join(options)
            q = {
                "number": i, "stem_text": stem, "options_text": options,
                "katex": area.locator(".katex").count() + tab.locator(".test-attempt-options .katex").count(),
                "fallbacks": tab.locator(".math-fallback").count(),
                "katex_errors": tab.locator(".katex-error").count(),
                "raw_latex_visible": RAW.findall(visible),
                "fraction": tab.locator(".katex .mfrac").count(), "matrix": tab.locator(".katex .mtable").count(),
                "scripts": tab.locator(".katex .msupsub").count(), "bold_vector": tab.locator(".katex .boldsymbol").count(),
                "upright_unit": tab.locator(".katex .mathrm").count(),
                "greek": sum(ch in tab.locator(".test-attempt-question .katex, .test-attempt-options .katex").all_inner_texts().__str__()
                             for ch in "παβθλμωΩ"),
            }
            features = {k: n + (1 if q[k] else 0) for k, n in features.items()}
            report["questions"].append(q)
            shot(tab, f"q{i:02d}")
            want = next((v for k, v in P["answers"].items() if k in stem), None)
            if want:
                tab.locator(".test-attempt-option").nth("ABCD".index(want)).click()
                expect(tab.locator(".test-attempt-option.selected")).to_have_count(1)
                answered += 1
            if i < total:
                tab.get_by_role("button", name="Save & Next").click()

        qs = report["questions"]
        check("every question shows typeset mathematics where the text has LaTeX (KaTeX output present)",
              sum(q["katex"] > 0 for q in qs) >= 12, [(q["number"], q["katex"]) for q in qs])
        check("no raw LaTeX source is visible in any question or option", not any(q["raw_latex_visible"] for q in qs),
              [(q["number"], q["raw_latex_visible"]) for q in qs if q["raw_latex_visible"]])
        check("no expression fell back to its stored source (no .math-fallback, no .katex-error)",
              not any(q["fallbacks"] or q["katex_errors"] for q in qs), [(q["number"], q["fallbacks"], q["katex_errors"]) for q in qs])
        check("fractions render (KaTeX .mfrac)", features["fraction"] >= 2, features)
        check("the matrix renders (KaTeX .mtable)", features["matrix"] >= 1, features)
        check("superscripts / subscripts render (KaTeX .msupsub)", features["scripts"] >= 6, features)
        check("Greek letters render (π, Ω)", features["greek"] >= 2, features)
        check("the bold vectors render in bold italic (KaTeX .boldsymbol)", features["bold_vector"] >= 1, features)
        check("units and element symbols render upright (KaTeX .mathrm)", features["upright_unit"] >= 5, features)
        check("8 questions answered as planned", answered == 8, answered)
        report["features"] = features

        # ------------------------------------------------------------------ submit, result
        tab.once("dialog", lambda d: d.accept())
        tab.get_by_role("button", name="Submit Test").click()
        tab.wait_for_url(re.compile(r"/result"), timeout=40000)
        tab.wait_for_load_state("networkidle")
        expect(tab.get_by_text(re.compile(r"\b27\b")).first).to_be_visible(timeout=25000)
        check("after submission the result page shows the score 27 (7 × 4 − 1)", True)
        shot(tab, "result")
        report["result_text"] = tab.locator("main").inner_text()[:2000] if tab.locator("main").count() else tab.inner_text("body")[:2000]

        report["csp_violations"] = csp_of(page) + csp_of(tab)
        check("no Content-Security-Policy violation on any page", not report["csp_violations"], report["csp_violations"])
        check("no JavaScript error on any page", not report["page_errors"], report["page_errors"])
        # the ONLY tolerated error: the auth service's 401 to the session check made while still signed out (the login page asking
        # "who am I?"). Chrome logs each as a console resource error, so the console must hold exactly that many 401 lines and nothing else
        allowed = [e for e in report["http_errors"] if e["status"] == 401 and e["before_sign_in"] and "/s/auth/" in e["url"]]
        bad_http = [e for e in report["http_errors"] if e not in allowed]
        console_401 = [e for e in report["console_errors"] if "status of 401" in e]
        unexpected = [e for e in report["console_errors"] if "status of 401" not in e]
        report["tolerated"] = {"http": allowed, "console": console_401}
        check("no HTTP error response other than the signed-out 401 from the auth service's session check", not bad_http, bad_http)
        check("no console error other than those same 401s (one console line per tolerated response)",
              not unexpected and len(console_401) <= len(allowed), {"unexpected": unexpected, "console_401": console_401, "allowed": allowed})
        browser.close()
except Exception as exc:  # noqa: BLE001 - any failure is a failed check with what was collected
    check("browser journey completed", False, f"{type(exc).__name__}: {exc}")
finish()
