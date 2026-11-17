"""Browser journeys of platform_improvements_ui.sh: real Institute portal in Chromium, nothing mocked. Prints PASS/FAIL per check."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

S = json.loads(Path(os.environ["SEED"]).read_text())
SHOTS = Path(os.environ["SHOTS"])
SHOTS.mkdir(parents=True, exist_ok=True)
PLATFORM, API = "http://localhost", "http://localhost:8010"
APEX_ADMIN, BRANCH_ADMIN = "STF900002", "STF900003"
failures: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {str(detail)[:300]}"), flush=True)
    if not ok:
        failures.append(name)


def otp():
    logs = subprocess.run(["docker", "logs", os.environ["IDENTITY"]], capture_output=True, text=True, check=True).stderr
    return re.findall(r"verification code is (\d{6})", logs)[-1]


def sign_in(page, base, code):
    page.goto(f"{base}/login")
    page.get_by_label("Staff ID").fill(code)
    page.get_by_label("Password").fill(S["password"])
    page.get_by_role("button", name="Sign in").click()
    page.get_by_label("Verification code").wait_for()     # the code exists only once the password step has answered
    page.get_by_label("Verification code").fill(otp())
    page.get_by_role("button", name="Verify").click()
    page.get_by_role("button", name=re.compile("Sign out|Log out")).wait_for(timeout=15000)


def status(page, path):
    # the browser context's own cookies, without CORS: a gateway "No such route" 404 carries no CORS headers, so fetch() could not read it
    return page.request.get(API + path).status


def table_text(page):
    return page.locator("table").first.inner_text()


with sync_playwright() as pw:
    browser = pw.chromium.connect("ws://127.0.0.1:13013/")

    print("Provider Admin")
    ctx = browser.new_context()
    page = ctx.new_page()
    sign_in(page, PLATFORM, APEX_ADMIN)
    check("a Provider Admin signs in at the platform address", page.url.startswith(PLATFORM), page.url)
    check("another provider's data is refused", status(page, f"/api/v1/branches/{S['central']}/batches") == 403)

    print("All Branches, downloads")
    page.goto(f"{PLATFORM}/papers?tab=papers")
    expect(page.get_by_role("link", name="Approved Main")).to_be_visible()
    page.get_by_label("Papers from").select_option("PROVIDER") if page.get_by_label("Papers from").input_value() != "PROVIDER" else None
    text = table_text(page)
    check("one branch shows that branch's papers", "Published North" not in text, text)
    page.get_by_label("Branch").select_option(label="All Branches")
    expect(page.get_by_role("link", name="Published North")).to_be_visible()
    text = table_text(page)
    check("All Branches shows every branch of the provider, once each", text.count("Approved Main") == 1 and "Core Paper" not in text, text)
    check("All Branches names each paper's branch", "Apex North" in text, text)
    page.get_by_label("Has a version that is").select_option("PUBLISHED")
    expect(page.get_by_role("link", name="Approved Main")).to_be_hidden()
    expect(page.get_by_role("link", name="Published North")).to_be_visible()
    check("All Branches works with the other filters", True)
    page.screenshot(path=str(SHOTS / "all-branches.png"), full_page=True)
    check("no ZIP download is offered", page.get_by_role("button", name=re.compile("ZIP")).count() == 0)

    draft, approved = S["versions"]["Draft Main"], S["versions"]["Approved Main"]
    page.goto(f"{PLATFORM}/papers/{draft}")
    page.get_by_text("Question of Draft Main").wait_for()
    check("a draft offers no download", page.get_by_role("button", name=re.compile("^Download")).count() == 0)
    for doc in ("pdf", "answer-key"):
        check(f"a direct API call cannot fetch a draft's {doc}", status(page, f"/api/v1/paper-versions/{draft}/{doc}") == 403)
    check("the ZIP routes are gone", status(page, f"/api/v1/papers/pdf?provider_id={S['apex']}") == 404)
    page.goto(f"{PLATFORM}/papers/{approved}")
    with page.expect_download() as got:
        page.get_by_role("button", name="Download Question Paper").click()
    check("an approved paper downloads as one PDF", got.value.suggested_filename.endswith(".pdf"), got.value.suggested_filename)
    with page.expect_download() as got:
        page.get_by_role("button", name="Download Answer Key").click()
    check("its answer key downloads as one PDF", "answer-key" in got.value.suggested_filename, got.value.suggested_filename)

    print("Batches")
    page.goto(f"{PLATFORM}/batches")
    page.get_by_role("button", name="New batch").click()
    page.get_by_label("Name").fill("Morning")
    page.get_by_role("button", name="Create").click()
    expect(page.get_by_role("cell", name="Morning", exact=True)).to_be_visible()
    check("a batch is created", True)
    for name in ("View", "Edit", "Subjects", "Class defaults", "Add students", "Delete"):
        check(f"the {name} action is there", page.get_by_role("button", name=name, exact=True).count() >= 1)
    page.get_by_role("button", name="Edit", exact=True).click()
    page.get_by_label("Name").fill("Morning A")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("cell", name="Morning A", exact=True)).to_be_visible()
    check("a batch is edited", True)
    page.get_by_role("button", name="Add students", exact=True).click()
    page.get_by_role("checkbox", name="Select STU000001").check()
    page.get_by_role("checkbox", name="Select STU000002").check()
    page.get_by_role("button", name="Add 2 students").click()
    expect(page.get_by_text("Added 2.")).to_be_visible()
    check("the added students leave the eligible list", page.get_by_role("checkbox", name="Select STU000001").count() == 0)
    page.screenshot(path=str(SHOTS / "add-students.png"), full_page=True)
    page.get_by_role("button", name="Close", exact=True).last.click()
    expect(page.get_by_role("row", name=re.compile("Morning A"))).to_contain_text("2")
    check("the student count updates", True)
    bid = page.evaluate(f"fetch('{API}/api/v1/branches/{S['main']}/batches', {{credentials: 'include'}}).then(r => r.json()).then(j => j.items[0].batch_id)")
    page.get_by_role("button", name="View", exact=True).click()
    expect(page.get_by_role("cell", name="STU000002")).to_be_visible()
    check("the membership list shows them", True)
    page.get_by_role("button", name="Close", exact=True).last.click()
    page.get_by_role("button", name="Delete", exact=True).click()
    page.get_by_role("button", name="Delete batch").click()
    expect(page.get_by_role("cell", name="Morning A", exact=True)).to_be_hidden()
    check("Delete takes the batch out of the normal list", True)
    page.get_by_label("Show").select_option("INACTIVE")
    expect(page.get_by_role("cell", name="Morning A", exact=True)).to_be_visible()
    check("it is kept in Inactive batches, with its students", "2" in page.get_by_role("row", name=re.compile("Morning A")).inner_text())
    page.screenshot(path=str(SHOTS / "inactive-batches.png"), full_page=True)
    page.get_by_role("button", name="Reactivate").click()
    expect(page.get_by_role("cell", name="Morning A", exact=True)).to_be_hidden()
    page.get_by_label("Show").select_option("ACTIVE")
    expect(page.get_by_role("cell", name="Morning A", exact=True)).to_be_visible()
    check("Reactivate brings it back", True)
    ctx.close()

    print("Branch Admin (platform address)")
    ctx = browser.new_context()
    page = ctx.new_page()
    sign_in(page, PLATFORM, BRANCH_ADMIN)
    page.goto(f"{PLATFORM}/papers?tab=papers")
    expect(page.get_by_role("link", name="Approved Main")).to_be_visible()
    check("a Branch Admin cannot read a sibling branch", status(page, f"/api/v1/papers?branch_id={S['north']}") == 403)
    rows = page.evaluate(f"fetch('{API}/api/v1/papers?provider_id={S['apex']}', {{credentials: 'include'}}).then(r => r.json()).then(j => j.items.map(x => x.title))")
    check("All Branches for a Branch Admin means their own branch", sorted(rows) == ["Approved Main", "Draft Main"], rows)
    check("a Branch Admin cannot touch another provider's batches", status(page, f"/api/v1/branches/{S['central']}/batches") == 403)
    ctx.close()

    browser.close()

print(f"\n{'ALL PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
