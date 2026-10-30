import { useCallback, useEffect, useState } from "react";

import { ATTEMPT_API_URL } from "../services/api";

/*
 * A Global Test with paper choice: every subject with the papers this student may sit. The attempt service decides everything (eligibility,
 * which choices are required, completeness, the lock once started); this component only shows its answer and sends the student's
 * choice back. Choices are stored by the service, so they survive refreshes and other devices.
 */

export type ChoicePaper = {
  paper_id: string;
  label: string;
  provider_name?: string | null;
  question_count: number;
  total_marks: number;
};

export type SubjectChoice = {
  position: number;
  subject: string;
  status: string;                 // PAPER (one paper), SELECTABLE (choose one), or why there is no paper
  message: string | null;
  papers: ChoicePaper[];
  selected_paper_id: string | null;
};

export type PaperChoices = {
  test_id: string;
  locked: boolean;                // the test has started: these are the attempt's papers
  complete: boolean;              // every required choice is made
  attempt_id: string | null;
  subjects: SubjectChoice[];
};

function paperText(paper: ChoicePaper) {
  return [paper.label, paper.provider_name, `${paper.question_count} question(s)`, `${paper.total_marks} marks`]
    .filter(Boolean)
    .join(" · ");
}

function errorText(status: number, body: { detail?: { code?: string } | string } | null) {
  const code = typeof body?.detail === "object" ? body.detail.code : undefined;
  if (code === "SELECTION_LOCKED") return "Your papers are fixed because the test has started.";
  if (code === "PAPER_NOT_ELIGIBLE") return "This paper is not available to you.";
  if (status === 503) return "Paper selection is temporarily unavailable. Please try again.";
  return "Your choice could not be saved. Please try again.";
}

async function fetchChoices(testId: string): Promise<PaperChoices> {
  const response = await fetch(`${ATTEMPT_API_URL}/attempts/tests/${testId}/papers`, { credentials: "include" });
  if (!response.ok) throw new Error(`Failed to load papers: ${response.status}`);
  return response.json();
}

function PaperSelection({ testId, onChange }: { testId: string; onChange?: (choices: PaperChoices | null) => void }) {
  const [choices, setChoices] = useState<PaperChoices | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState<string | null>(null);

  const show = useCallback((next: PaperChoices | null) => {
    setChoices(next);
    onChange?.(next);
  }, [onChange]);

  const load = useCallback(() => fetchChoices(testId).then(show).catch((loadError) => {
    console.error("Failed to load paper choices:", loadError);
    setError("Unable to load your papers.");
    show(null);
  }), [testId, show]);

  useEffect(() => {
    load();
  }, [load]);

  async function choose(subject: string, paperId: string) {
    setSaving(subject);
    setError("");
    try {
      const response = await fetch(`${ATTEMPT_API_URL}/attempts/tests/${testId}/selection`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ subject, paper_id: paperId }),
      });
      if (!response.ok) {
        setError(errorText(response.status, await response.json().catch(() => null)));
        if (response.status === 409) await load();          // started elsewhere: show the locked papers
        return;
      }
      show(await response.json());
    } catch (saveError) {
      console.error("Failed to save paper choice:", saveError);
      setError("Your choice could not be saved. Please try again.");
    } finally {
      setSaving(null);
    }
  }

  if (!choices) {
    return (
      <div className="test-details-section paper-selection">
        <h3>Subjects and Papers</h3>
        <p>{error || "Loading your papers..."}</p>
      </div>
    );
  }

  return (
    <div className="test-details-section paper-selection">
      <h3>Subjects and Papers</h3>

      {choices.locked ? (
        <p className="paper-selection-note" role="status">The test has started. These are your papers.</p>
      ) : (
        !choices.complete && (
          <p className="paper-selection-note" role="status">Choose one paper for each subject marked below before you start.</p>
        )
      )}

      {error && <p className="paper-selection-error" role="alert">{error}</p>}

      <ul>
        {[...choices.subjects].sort((a, b) => a.position - b.position).map((s) => {
          const selected = s.papers.find((p) => p.paper_id === s.selected_paper_id);
          const choosing = s.status === "SELECTABLE" && !choices.locked;
          return (
            <li key={s.subject} className={s.papers.length ? "available" : "unavailable"}>
              <strong>{s.subject}</strong>

              {choosing && (
                <fieldset disabled={saving !== null}>
                  <legend>Choose a {s.subject} paper</legend>
                  {s.papers.map((p) => (
                    <label key={p.paper_id}>
                      <input
                        type="radio"
                        name={`paper-${s.subject}`}
                        value={p.paper_id}
                        checked={s.selected_paper_id === p.paper_id}
                        onChange={() => choose(s.subject, p.paper_id)}
                      />
                      {paperText(p)}
                    </label>
                  ))}
                  {!selected && <span className="paper-selection-required">Choose one paper</span>}
                </fieldset>
              )}

              {!choosing && selected && <span>{paperText(selected)}</span>}

              {!choosing && !selected && <span>{s.message}</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default PaperSelection;
