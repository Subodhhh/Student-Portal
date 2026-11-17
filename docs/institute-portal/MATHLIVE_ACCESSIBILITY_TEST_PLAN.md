# MathLive formula editor: manual accessibility test plan

**Status: NOT TESTED.** No screen-reader or real-device test has been performed yet. Every result in section 6 is NOT TESTED until a person
runs it on the named hardware and software. Stage 5 (MathLive integration) stays blocked until these checks pass and the results are
reviewed.

The editor under test is the isolated spike (never part of the project): MathLive 0.111.1 with the four agreed fixes, a custom
"Templates" on-screen keyboard, spoken key names, the menu hidden, and the Institute portal's production Content-Security-Policy.
It is in the session scratchpad (`…/scratchpad/mathlive-spike`). If the scratchpad has been cleared, ask for the spike to be rebuilt first.

## 1. Starting and stopping the test page

Everything runs on the development PC. The page needs no internet access and loads nothing from outside the PC.

**Start (in WSL):**

```bash
cd /tmp/claude-1000/-home-subodhmore-Institute-Student-Portal/f13c17fe-6cc1-4a72-a6f0-cb8b2b1cf0bb/scratchpad/mathlive-spike
npx vite build                 # only if dist/ is missing or the spike changed
python3 serve_lan.py           # this PC only            -> "Serving ... this computer only"
python3 serve_lan.py --lan     # ALSO the local network  (only for the phone tests)
```

**Windows (NVDA tests):** with either command, open `http://localhost:4173` in the Windows browser. WSL forwards localhost; no
network setup is needed. Use `serve_lan.py` without `--lan` for these tests.

**Phone on the same Wi-Fi (phone tests only):** start with `--lan`, then in an **administrator PowerShell** on Windows:

```powershell
# 1. WSL's current address (it changes after a restart, so it is looked up, never typed in)
$wsl = (wsl hostname -I).Trim().Split(" ")[0]
# 2. forward the PC's port 4173 to WSL
netsh interface portproxy add v4tov4 listenport=4173 listenaddress=0.0.0.0 connectport=4173 connectaddress=$wsl
# 3. allow it from the LOCAL SUBNET only, and only while the network is marked Private
New-NetFirewallRule -DisplayName "MathLive spike 4173" -Direction Inbound -LocalPort 4173 -Protocol TCP -Action Allow `
  -RemoteAddress LocalSubnet -Profile Private
# 4. the address to open on the phone: the PC's Wi-Fi/Ethernet IPv4 address
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -in "Dhcp","Manual" -and $_.IPAddress -notlike "169.254.*" } |
  Select-Object InterfaceAlias, IPAddress
```

Open `http://<IPAddress shown for Wi-Fi or Ethernet>:4173` on the phone. If the PC's network is marked *Public*, the rule
does not apply and the phone cannot connect: change the network to *Private* only if it is a trusted home or office network.

**Stop:**

1. In WSL press **Ctrl+C** in the server window ("Stopped." is printed). If the window was closed: `pkill -f serve_lan.py`.
2. If the phone set-up was used, in an administrator PowerShell:
   ```powershell
   netsh interface portproxy delete v4tov4 listenport=4173 listenaddress=0.0.0.0
   Remove-NetFirewallRule -DisplayName "MathLive spike 4173"
   netsh interface portproxy show v4tov4                   # must list nothing for 4173
   ```
3. Check: `http://localhost:4173` no longer opens.

**Security of the test server**

* Without `--lan` it listens on this PC only (127.0.0.1).
* With `--lan` it listens on the network, but refuses (403) any request that does not come from a private, loopback or link-local
  address. The Windows firewall rule only admits the local subnet on a Private network. Nothing is reachable from the internet.
* It serves only the built page (GET/HEAD). There are no directory listings, uploads or data, and no logs are written to disk.
* It sends the Institute portal's production Content-Security-Policy, plus `nosniff`, `no-referrer` and `no-store`.
* Stop it, and remove the port proxy and firewall rule, as soon as the phone tests are finished.

The page has 11 labelled fields (one per case), a **save status** line (a polite live region) with *Simulate a save* /
*Simulate a failed save*, and buttons *Before the fields* / *After the fields* for focus-order checks.

## 2. Record the environment

| Item | Value |
|---|---|
| Tester | |
| Date | |
| PC: Windows version | |
| Browser + version (Chrome/Edge/Firefox) | |
| NVDA version | |
| NVDA settings changed from default (if any) | |
| Phone: model | |
| Phone: OS + version | |
| Phone: browser + version | |
| Phone screen reader (TalkBack/VoiceOver) + version, if used | |

## 3. NVDA on Windows (desktop browser)

Use NVDA's browse mode to reach the page, then focus mode inside a field (NVDA switches automatically when a field takes focus;
NVDA+Space toggles). "Announces" = what NVDA speaks; write it down verbatim when it differs from what is expected.

| # | Steps | Expected |
|---|---|---|
| N1 | Tab from *Before the fields* to field 1 | Announced as an editable math/text field named "1 Fraction: one half" |
| N2 | In field 1 type `1/2` | The formula is spoken meaningfully ("one half" / "fraction 1 over 2"), not as raw characters or LaTeX |
| N3 | Field 1: Left/Right arrows through the fraction | Moving into numerator and denominator is announced; the caret position is clear |
| N4 | Field 1: change the denominator to 3 | Edit works; the new value is announced |
| N5 | Field 2: type `e^13x` | Announced as e raised to 13x (the power covers 13x) |
| N6 | Field 2: clear, type `e^13`, Right arrow, `x` | Announced as e to the 13, then x on the baseline |
| N7 | Field 3: type `x_1`; then Backspace into the subscript and edit it | Subscript announced and editable |
| N8 | Field 4: on-screen *Templates* → "Square root", type `RT/3M` | Root then fraction announced; whole fraction under the root |
| N9 | Field 5: *Templates* → "Definite integral"; type `0`, Tab, `pi`, Tab, `sinx`, Tab, `x` | Each box announced as it is reached; result spoken as the integral from 0 to pi of sin x dx |
| N10 | Field 6: *Templates* → "Limit"; fill `x`, Tab, `0`, Tab, `sinx/x` | Limit and its condition announced |
| N11 | Field 7: *Templates* → "Sum"; fill `r=1`, Tab, `n`, Tab, `r^2` | Sum with both bounds announced |
| N12 | Field 8: *Templates* → "Matrix"; fill 1, -2, 3, 4 with Tab | Each cell announced with row/column; the matrix is navigable by arrows |
| N13 | Field 9: *Templates* → "Chemical symbol"; type `Fe`, "Power", `3+` | Announced recognisably as Fe with a 3+ superscript (note exactly what is spoken) |
| N14 | Field 10: "Chemical symbol" `SO`, "Index" `4`, Right, "Power" `2-` | SO, subscript 4, superscript 2− announced |
| N15 | Field 11: type `9.8`, "Unit metre", "Unit second", "Power" `-2` | Units announced (m, s) and the power −2 |
| N16 | Open the on-screen keyboard (keyboard icon / focus it) and move through the keys | Every *Templates* key has a spoken name ("Fraction", "Power", "Definite integral" …), not symbols such as "a/b" or "(▦)" |
| N17 | Insert a template with the on-screen keyboard | Focus stays in (or returns to) the field, at the first empty box |
| N18 | Tab and Shift+Tab across all fields, *Before*/*After* buttons | With no empty boxes Tab leaves the field (no trap); order is Before → 1 … 11 → save buttons → After, and the reverse |
| N19 | In a template with empty boxes press Tab | Tab moves to the next empty box; after the last box Tab leaves the field |
| N20 | Escape inside a field; Escape with the on-screen keyboard open | Predictable: the keyboard closes / nothing destructive happens; note the exact behaviour |
| N21 | Field 1: insert "Fraction" from the keyboard and fill only the top | "Fill in every empty box before saving." is announced (polite), once |
| N22 | *Simulate a save*, then *Simulate a failed save* | "Saving…", then "All changes saved at …" / "Not saved - retrying …" announced once each, not repeated |
## 4. Real phone (not emulation)

| # | Steps | Expected |
|---|---|---|
| M1 | Open the page in portrait at normal size | No sideways page scroll; fields and labels fully visible |
| M2 | Tap field 1 | The MathLive on-screen keyboard opens; the phone's own keyboard does not cover it |
| M3 | Switch the keyboard to *Templates*; tap "Fraction", `1`, next box, `2` | Fraction entered; every key reachable without zooming |
| M4 | Tap into the denominator, delete, retype | Editing and deletion work by touch |
| M5 | Enter the integral, matrix and chemistry cases (5, 8, 9, 10) by touch | All enterable; boxes reachable |
| M6 | Close the on-screen keyboard; scroll; reopen it on another field | Opens and closes reliably; nothing clipped |
| M7 | Key size | Keys large enough to hit reliably (spike measured 39×42 px CSS on an emulated Pixel 7) |
| M8 | Simulate a save and a failed save | Status line visible above the keyboard (scroll if needed) and readable |
| M9 | Rotate to landscape and back | Still usable; no overflow |
| M10 | Tap the *Before the fields* button (a normal control) and then a field | The phone's native keyboard never blocks the page or traps focus |
| M11 | (If available) TalkBack / VoiceOver: repeat N1, N2, N16 by swipe navigation | Field name, formula and key names spoken |
## 5. Defects

Give each failure an id (D1, D2 …) and use it in the results table. For each one, record:

* the test case;
* the exact steps from a fresh page load;
* what happened, and what was expected;
* evidence:
  * **NVDA:** the spoken text. Use NVDA+F1 for the log, or the Speech viewer (NVDA menu → Tools → Speech viewer) and copy its text.
  * **Screen:** a screenshot (Windows+Shift+S; on a phone, the usual screenshot buttons), or a short screen recording.
* whether it happens every time, sometimes, or once;
* severity: **Blocker** (cannot complete the task), **Major** (can, with difficulty), **Minor** (cosmetic or wording).

| # | Case | Description | Evidence | Reproducible? | Severity |
|---|---|---|---|---|---|
| | | | | | |

## 6. Results (fill in while testing)

Write what actually happened. Leave **NOT TESTED** for anything not performed, and do not mark a check PASS from memory or by
analogy with another device.

| Test ID | Actual result | PASS / FAIL / NOT TESTED | Defect reference | Tester notes |
|---|---|---|---|---|
| N1 | | NOT TESTED | | |
| N2 | | NOT TESTED | | |
| N3 | | NOT TESTED | | |
| N4 | | NOT TESTED | | |
| N5 | | NOT TESTED | | |
| N6 | | NOT TESTED | | |
| N7 | | NOT TESTED | | |
| N8 | | NOT TESTED | | |
| N9 | | NOT TESTED | | |
| N10 | | NOT TESTED | | |
| N11 | | NOT TESTED | | |
| N12 | | NOT TESTED | | |
| N13 | | NOT TESTED | | |
| N14 | | NOT TESTED | | |
| N15 | | NOT TESTED | | |
| N16 | | NOT TESTED | | |
| N17 | | NOT TESTED | | |
| N18 | | NOT TESTED | | |
| N19 | | NOT TESTED | | |
| N20 | | NOT TESTED | | |
| N21 | | NOT TESTED | | |
| N22 | | NOT TESTED | | |
| M1 | | NOT TESTED | | |
| M2 | | NOT TESTED | | |
| M3 | | NOT TESTED | | |
| M4 | | NOT TESTED | | |
| M5 | | NOT TESTED | | |
| M6 | | NOT TESTED | | |
| M7 | | NOT TESTED | | |
| M8 | | NOT TESTED | | |
| M9 | | NOT TESTED | | |
| M10 | | NOT TESTED | | |
| M11 | | NOT TESTED | | |

Sign-off: tested by ____________ on ____________ (date). Reviewed by ____________ on ____________.

## 7. Known from the automated spike (headless Chromium; not a substitute for this test)

* axe-core (WCAG 2.2 AA) found one issue: colour contrast of the selected keyboard tab (fixable with CSS in the integration).
* MathLive gives a keycap's aria-label its visual label; the spike sets spoken names with a small script. N16 verifies this.
* Tab leaving a finished field depends on a keydown handler added by the spike (fix 4). N18/N19 verify it with a real screen reader,
  whose own key handling may differ.
* Speech text comes from MathLive's own speech rules (`getValue("spoken-text")`); NVDA may read the field differently. Record what
  is actually spoken.
