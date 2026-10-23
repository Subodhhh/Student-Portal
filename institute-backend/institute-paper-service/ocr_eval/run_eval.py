"""Run the photo-paper pipeline (TesseractBackend.read_lines -> photo_drafts.build_questions) on the evaluation dataset and report
accuracy and latency against ground truth. Run it inside the paper-service image: scripts/run-ocr-eval.sh.

Scoring (per category and overall). Text is compared EXACTLY after collapsing runs of whitespace (case, punctuation and symbols count):
  * detection precision / recall / F1 - question numbers found vs. the ground truth;
  * stem accuracy, option accuracy (each of A-D), answer accuracy - over the questions found;
  * complete-question accuracy - stem, all four options and the answer exact, over ALL ground-truth questions (missed = wrong);
  * flagged rate - questions marked needs_review (a person must check them before the paper can be accepted);
  * false acceptance - questions NOT flagged that are wrong in any field (or do not exist), over all questions not flagged:
    the errors that would reach the question bank if the reviewer only confirmed;
  * safe-outcome rate - ground-truth questions that are either exactly right or flagged for review;
  * latency - seconds per photo (pages and keys) and per draft (all its photos), median and 95th percentile.
A draft with a page refused as unreadable cannot be extracted at all (the API refuses it): its questions count as missed, never as
accepted. Cases in the "unreadable" category are expected to be refused or entirely flagged; a question read exactly right from a
degraded photo is accepted as correct, anything else accepted from it counts as a false acceptance.

usage: python -m ocr_eval.run_eval [--workers N] [--json out.json] [--scale K] [--only category,...]"""
import argparse
import json
import sys
import time
from multiprocessing import Pool

from ocr_eval.dataset import build, cases


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def _ocr(backend, data: bytes, purpose: str):
    from inst_paper.services import extraction
    unreadable = getattr(extraction, "OcrUnreadable", ())          # the pre-change code has no such exception
    t = time.perf_counter()
    try:
        try:
            lines, err = backend.read_lines(data, "image/jpeg", purpose), None
        except TypeError:                                   # the pre-change backend: read_lines(image, mime)
            lines, err = backend.read_lines(data, "image/jpeg"), None
    except unreadable as exc:
        lines, err = [], str(exc)
    except Exception as exc:  # noqa: BLE001 - like the upload route: a failed OCR (e.g. Tesseract timeout) refuses that photo
        lines, err = [], f"ERROR {type(exc).__name__}: {exc}"
    return lines, err, time.perf_counter() - t


def run_case(item: tuple[str, dict]) -> dict:
    from inst_paper.services import photo_drafts as pd
    from inst_paper.services.ocr_tesseract import TesseractBackend
    case_id, spec = item
    case = build(case_id, spec)
    backend = TesseractBackend()
    pages = [_ocr(backend, p, "page") for p in case.pages]
    keys = [_ocr(backend, k, "key") for k in case.keys]
    refused = any(err for _l, err, _t in pages)
    questions, issues = ([], ["refused"]) if refused else pd.build_questions(
        [lines for lines, _e, _t in pages], [ln for lines, _e, _t in keys for ln in lines] or None, 0.85, 200)
    return {"case": case_id, "category": case.category, "layout": case.layout, "unreadable": case.unreadable, "refused": refused,
            "page_errors": [e for _l, e, _t in pages + keys if e], "page_seconds": [t for *_x, t in pages], "key_seconds": [t for *_x, t in keys],
            "truth": {str(n): list(v) for n, v in case.truth.items()},
            "questions": [{k: q.get(k) for k in ("number", "question", "option_a", "option_b", "option_c", "option_d", "correct",
                                                 "needs_review", "issues", "confidence")} for q in questions], "issues": issues}


def score(results: list[dict]) -> dict:
    def pct(a, b):
        return round(100 * a / b, 1) if b else None

    def q(values, p):
        if not values:
            return None
        values = sorted(values)
        return round(values[min(len(values) - 1, int(p * len(values)))], 2)

    tp = fp = fn = stem = opt = ans = complete = flagged = unflagged = false_accept = safe = total_truth = found = 0
    drafts, photos = [], []
    unreadable_ok = unreadable_n = 0
    for r in results:
        drafts.append(sum(r["page_seconds"]) + sum(r["key_seconds"]))
        photos += r["page_seconds"] + r["key_seconds"]
        preds = {q_["number"]: q_ for q_ in r["questions"]}
        if r["unreadable"]:
            # Expected: refused, or every question flagged; a question that was nevertheless read EXACTLY right (a dim photo the
            # light correction rescued) is not an error. Anything else accepted unflagged is a false acceptance.
            unreadable_n += 1
            truth_u = {int(n): v for n, v in r["truth"].items()}

            def exact(p, truth_u=truth_u):
                t = truth_u.get(p["number"])
                return bool(t) and _norm(p["question"]) == _norm(t[0]) and [_norm(p[f"option_{x}"]) for x in "abcd"] == t[1] and p["correct"] == t[2]
            accepted_wrong = [p for p in preds.values() if not p["needs_review"] and not exact(p)]
            unreadable_ok += r["refused"] or not accepted_wrong
            false_accept += len(accepted_wrong)
            unflagged += sum(1 for p in preds.values() if not p["needs_review"])
            continue
        truth = {int(n): v for n, v in r["truth"].items()}
        total_truth += len(truth)
        for n, p in preds.items():
            found += 1
            right = False
            if n in truth:
                tp += 1
                t_stem, t_opts, t_ans = truth[n]
                s_ok = _norm(p["question"]) == _norm(t_stem)
                o_ok = [_norm(p[f"option_{L}"]) == _norm(t) for L, t in zip("abcd", t_opts, strict=True)]
                a_ok = (p["correct"] or "") == t_ans
                stem += s_ok
                opt += sum(o_ok)
                ans += a_ok
                right = s_ok and all(o_ok) and a_ok
                complete += right
            else:
                fp += 1
            if p["needs_review"]:
                flagged += 1
            else:
                unflagged += 1
                false_accept += not right
            safe += n in truth and (right or p["needs_review"])
        fn += sum(1 for n in truth if n not in preds)
    prec, rec = (tp / (tp + fp) if tp + fp else 0.0), (tp / (tp + fn) if tp + fn else 0.0)
    return {
        "cases": len(results), "ground_truth_questions": total_truth, "questions_found": found,
        "detection_precision": pct(tp, tp + fp), "detection_recall": pct(tp, tp + fn),
        "detection_f1": round(200 * prec * rec / (prec + rec), 1) if prec + rec else 0.0,
        "stem_accuracy": pct(stem, tp), "option_accuracy": pct(opt, 4 * tp), "answer_accuracy": pct(ans, tp),
        "complete_question_accuracy": pct(complete, total_truth), "flagged_rate": pct(flagged, found),
        "false_acceptance_rate": pct(false_accept, unflagged), "false_acceptances": false_accept, "unflagged_questions": unflagged,
        "safe_outcome_rate": pct(safe, total_truth),
        "unreadable_cases_refused_or_flagged": f"{unreadable_ok}/{unreadable_n}" if unreadable_n else None,
        "seconds_per_photo_p50": q(photos, 0.5), "seconds_per_photo_p95": q(photos, 0.95),
        "seconds_per_draft_p50": q(drafts, 0.5), "seconds_per_draft_p95": q(drafts, 0.95),
        "photos": len(photos), "ocr_errors": sum(1 for r in results for e in r["page_errors"] if e.startswith("ERROR ")),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--json")
    ap.add_argument("--scale", type=int, default=1)
    ap.add_argument("--only")
    a = ap.parse_args()
    specs = cases(a.scale)
    if a.only:
        specs = [s for s in specs if s[1]["category"] in a.only.split(",")]
    t = time.perf_counter()
    with Pool(a.workers) as pool:
        results = pool.map(run_case, specs, chunksize=1)
    by_cat: dict[str, list] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)
    report = {"overall": score([r for r in results if not r["unreadable"]]), "by_category": {c: score(rs) for c, rs in by_cat.items()},
              "wall_seconds": round(time.perf_counter() - t, 1), "workers": a.workers}
    cols = ["cases", "ground_truth_questions", "detection_precision", "detection_recall", "detection_f1", "stem_accuracy", "option_accuracy",
            "answer_accuracy", "complete_question_accuracy", "flagged_rate", "false_acceptance_rate", "safe_outcome_rate",
            "seconds_per_photo_p50", "seconds_per_photo_p95", "seconds_per_draft_p50", "seconds_per_draft_p95"]
    print("category," + ",".join(cols))
    for name, s in [*report["by_category"].items(), ("OVERALL (excl. unreadable)", report["overall"])]:
        print(name + "," + ",".join(str(s.get(c)) for c in cols))
    if "unreadable" in report["by_category"]:
        print("unreadable cases refused or fully flagged:", report["by_category"]["unreadable"]["unreadable_cases_refused_or_flagged"])
    print(f"wall time {report['wall_seconds']}s with {a.workers} workers")
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"report": report, "results": results}, f, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
