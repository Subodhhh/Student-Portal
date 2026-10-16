#!/usr/bin/env python3
"""Real-browser walk-through of both portals (Playwright, Chromium served by a Playwright run-server).

For every role it logs in through the UI (MFA codes are read from the identity service's dev console via `docker logs`), opens every
navigation entry and records: console errors, API responses >= 500 or unexpected 4xx, error banners and blank pages. Screenshots go to
the output directory. Usage:
    ui_walkthrough.py WS_ENDPOINT QA_ACCOUNTS_JSON ADMIN_CODE ADMIN_PASSWORD OUT_DIR
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

WS, ACCOUNTS, ADMIN_CODE, ADMIN_PW, OUT = sys.argv[1:6]
acc = json.loads(Path(ACCOUNTS).read_text())
out = Path(OUT)
out.mkdir(parents=True, exist_ok=True)
INST, STU = "http://localhost:8081", "http://localhost:8080"
problems: list[str] = []
visited = 0


def otp_for(code_hint: str | None = None) -> str:
    deadline = time.time() + 20
    while time.time() < deadline:
        logs = subprocess.run(["docker", "logs", "--since", "1m", "institute-identity-service"], capture_output=True, text=True)
        codes = re.findall(r"code is (\d{6})", logs.stdout + logs.stderr)
        if codes:
            return codes[-1]
        time.sleep(1)
    raise RuntimeError("no OTP")


def watch(page, label: str, allow_4xx: set[int] = frozenset()):
    def on_console(msg):
        # The apps probe their session endpoint before login; that 401 is expected and not a defect.
        if msg.type == "error" and "favicon" not in msg.text and "status of 401" not in msg.text:
            problems.append(f"[{label}] console: {msg.text[:200]}")

    def on_response(resp):
        url = resp.url
        if not re.search(r":80(0\d|10)/", url):
            return
        if resp.status >= 500 or (400 <= resp.status < 500 and resp.status not in allow_4xx and resp.status != 401):
            problems.append(f"[{label}] {resp.request.method} {url} -> {resp.status}")

    page.on("console", on_console)
    page.on("response", on_response)


def check_page(page, label: str, name: str):
    global visited
    page.wait_for_load_state("networkidle", timeout=20000)
    time.sleep(0.4)
    visited += 1
    body = page.inner_text("body")
    if len(body.strip()) < 20:
        problems.append(f"[{label}] {name}: page looks blank")
    for sel in (".alert.error", "[role=alert]"):
        for el in page.query_selector_all(sel):
            txt = el.inner_text().strip()
            if txt:
                problems.append(f"[{label}] {name}: error banner: {txt[:160]}")
    if "Something went wrong" in body or "Unable to load" in body or "Failed to" in body:
        problems.append(f"[{label}] {name}: error text on page")
    page.screenshot(path=str(out / f"{label}-{re.sub(r'[^a-z0-9]+', '-', name.lower())}.png"), full_page=True)


def institute(browser, label: str, code: str, password: str, expect_mfa: bool, forbidden: list[str], required: list[str]):
    ctx = browser.new_context(viewport={"width": 1366, "height": 900})
    page = ctx.new_page()
    watch(page, label)
    page.goto(INST + "/login")
    page.get_by_label("Staff ID").fill(code)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    if expect_mfa:
        page.get_by_label("Verification code").wait_for(timeout=15000)
        time.sleep(1)
        page.get_by_label("Verification code").fill(otp_for())
        page.get_by_role("button", name="Verify").click()
    page.wait_for_selector("nav.sidebar-nav a", timeout=20000)
    links = [(a.inner_text().split(" (")[0].strip(), a.get_attribute("href")) for a in page.query_selector_all("nav.sidebar-nav a")]
    names = [n for n, _ in links]
    for f in forbidden:
        if any(f.lower() == n.lower() for n in names):
            problems.append(f"[{label}] nav shows '{f}' which this role must not have")
    for r in required:
        if not any(r.lower() == n.lower() for n in names):
            problems.append(f"[{label}] nav is missing '{r}'")
    for name, href in links + [("My Account", "/account")]:
        page.goto(INST + href)
        check_page(page, label, name)
    # Scope selector must exist (branch-scoped pages depend on it) for every role with providers.
    if not page.query_selector("#scope-provider"):
        problems.append(f"[{label}] no provider/branch selector in the top bar")
    ctx.close()
    return names


def student(browser):
    label = "student"
    ctx = browser.new_context(viewport={"width": 1366, "height": 900})
    page = ctx.new_page()
    watch(page, label, allow_4xx={404})   # 404 = "no attempt yet" lookups are part of the normal flow
    page.goto(STU + "/login")
    page.get_by_placeholder("Enter your Student ID").fill(acc["student_id"])
    page.get_by_placeholder("Enter your password").fill(acc["student_password"])
    page.locator("button[type=submit]").click()
    page.wait_for_url(re.compile(r".*/dashboard"), timeout=20000)
    for name, path in [("Dashboard", "/dashboard"), ("My Institutes", "/my-institutes"), ("Tests", "/tests"),
                       ("Scheduled tests", "/scheduled-tests"), ("Results", "/results"), ("Ranking", "/ranking"),
                       ("Notifications", "/notifications"), ("Profile", "/profile"),
                       ("Test details", f"/tests/{acc['test_id']}"), ("Performance", f"/tests/{acc['test_id']}/performance")]:
        page.goto(STU + path)
        check_page(page, label, name)
        if name == "Tests":
            opts = [o.inner_text() for o in page.query_selector_all("select option")]
            if not any("QA Provider A" in o for o in opts) or not any("QA Provider B" in o for o in opts):
                problems.append(f"[student] Tests provider filter does not list both enrolled providers: {opts[:6]}")
        if name == "My Institutes":
            txt = page.inner_text("body")
            if f"QA Provider A {acc['run']}" not in txt or f"QA Provider B {acc['run']}" not in txt:
                problems.append("[student] My Institutes does not show both providers of the QA student")
        if name == "Performance":
            txt = page.inner_text("body")
            if "NEET Full Mock Test" in txt or "ABC Coaching" in txt:
                problems.append("[student] performance page still shows hard-coded sample data")
            if "3" not in txt or "12" not in txt:
                problems.append("[student] performance page does not show the evaluated score 3/12")
    ctx.close()


with sync_playwright() as pw:
    browser = pw.chromium.connect(WS)
    sa = institute(browser, "superadmin", ADMIN_CODE, ADMIN_PW, True, forbidden=[],
                   required=["Providers & Branches", "Students & Enrollments", "Question Bank & Papers", "Exam Plans", "Composite Tests",
                             "Exams & Results", "Service Health", "Audit Log"])
    pa = institute(browser, "provider-admin", acc["pa_code"], acc["pa_password"], True, forbidden=["Service Health"],
                   required=["Students & enrollments", "Question bank & papers", "Exam plans", "Composite tests", "Exams & results"])
    te = institute(browser, "teacher", acc["teacher_code"], acc["teacher_password"], False,
                   forbidden=["Students & enrollments", "Exam plans", "Composite tests", "Staff", "Billing", "Audit log"],
                   required=["Question bank & papers", "Exams & results"])
    student(browser)
    browser.close()

print(f"visited {visited} pages")
print("navigation:", json.dumps({"superadmin": sa, "provider-admin": pa, "teacher": te}))
if problems:
    print(f"{len(problems)} problem(s):")
    for p in dict.fromkeys(problems):
        print(" -", p)
    sys.exit(1)
print("no problems found")
