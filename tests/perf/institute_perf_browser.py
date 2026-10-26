"""Times every Institute Portal page in Chromium (needs playwright==1.48). usage: python institute_perf_browser.py <params.json>

For each staff account: sign in, then for every sidebar page, `runs` times each:
  navigate  click the sidebar link (in-app navigation, the SPA already loaded);
  reload    full page load of that URL (initial load: HTML, scripts, /me and the page's data).
A page is ready when no request has been in flight for 400 ms; its time is start -> end of the last response (the quiet period is not
counted). Writes one record per navigation: staff, kind, page, start/end (epoch seconds, to correlate with the server's records), ms, and
the API requests the browser made."""
import json
import sys
import time

from playwright.sync_api import sync_playwright

P = json.loads(open(sys.argv[1]).read())
UI, OUT = P["ui"], []


class Tracker:
    def __init__(self, page):
        self.inflight, self.last, self.api = 0, time.time(), []
        page.on("request", self._start)
        page.on("requestfinished", self._end)
        page.on("requestfailed", self._end)

    def _start(self, req):
        self.inflight += 1
        self.last = time.time()
        if "/api/" in req.url:
            self.api.append(req.method + " " + req.url.split(UI, 1)[-1].split("?", 1)[0])

    def _end(self, _req):
        self.inflight = max(0, self.inflight - 1)
        self.last = time.time()

    def measure(self, page, staff, kind, name, action):
        self.api = []
        start = self.last = time.time()
        action()
        deadline = start + 60
        while time.time() < deadline:
            page.wait_for_timeout(50)
            if self.inflight == 0 and time.time() - self.last >= 0.4:
                break
        OUT.append({"staff": staff, "kind": kind, "page": name, "start": start, "end": self.last, "ms": round((self.last - start) * 1000),
                    "api": list(self.api)})
        print(f"  {staff} {kind:8} {name:42} {OUT[-1]['ms']:6} ms  ({len(self.api)} API requests)", flush=True)


with sync_playwright() as pw:
    browser = pw.chromium.connect(P["ws"])
    for staff in P["staff"]:
        ctx = browser.new_context(viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(60000)
        tr = Tracker(page)
        page.goto(UI + "/login")
        page.get_by_label("Staff ID").fill(staff)
        page.get_by_label("Password", exact=True).fill(P["password"])
        otp = P["otp_file"]
        open(otp, "w").close()
        page.get_by_role("button", name="Sign in").click()
        page.get_by_label("Verification code").wait_for()
        for _ in range(100):
            code = open(otp).read().strip()
            if code:
                break
            time.sleep(0.1)
        page.get_by_label("Verification code").fill(code)
        tr.measure(page, staff, "sign-in", "Verify -> Dashboard", lambda: page.get_by_role("button", name="Verify").click())
        page.locator("nav.sidebar-nav a").first.wait_for()
        links = page.locator("nav.sidebar-nav a")
        pages = [(links.nth(i).inner_text().strip(), links.nth(i).get_attribute("href")) for i in range(links.count())]
        print(f"{staff}: {len(pages)} sidebar pages", flush=True)
        for _run in range(P["runs"]):
            for name, href in pages:
                tr.measure(page, staff, "navigate", name, lambda href=href: page.locator(f'nav.sidebar-nav a[href="{href}"]').first.click())
            for name, href in pages:
                tr.measure(page, staff, "reload", name, lambda href=href: page.goto(UI + href))
        ctx.close()
    browser.close()
open(P["out"], "w").write(json.dumps(OUT))
