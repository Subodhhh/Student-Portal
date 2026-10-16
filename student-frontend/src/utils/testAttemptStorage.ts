export type TestAttemptStatus =
  | "IN_PROGRESS"
  | "SUBMITTED"
  | "AUTO_SUBMITTED";

export type StoredTestAttempt = {
  testId: string;
  startedAt: number;
  expiresAt: number;
  status: TestAttemptStatus;
};

const STORAGE_KEY = "student_test_attempts";

function getAttempts(): StoredTestAttempt[] {
  const stored = localStorage.getItem(STORAGE_KEY);

  if (!stored) {
    return [];
  }

  try {
    return JSON.parse(stored) as StoredTestAttempt[];
  } catch {
    return [];
  }
}

function saveAttempts(
  attempts: StoredTestAttempt[]
) {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify(attempts)
  );
}

export function getTestAttempt(
  testId: string
): StoredTestAttempt | null {
  const attempts = getAttempts();

  return (
    attempts.find(
      (attempt) => attempt.testId === testId
    ) ?? null
  );
}

export function startTestAttempt(
  testId: string,
  durationMinutes: number
): StoredTestAttempt {
  const existingAttempt =
    getTestAttempt(testId);

  if (existingAttempt) {
    return existingAttempt;
  }

  const startedAt = Date.now();

  const attempt: StoredTestAttempt = {
    testId,
    startedAt,
    expiresAt:
      startedAt +
      durationMinutes * 60 * 1000,
    status: "IN_PROGRESS",
  };

  const attempts = getAttempts();

  saveAttempts([
    ...attempts,
    attempt,
  ]);

  return attempt;
}

export function updateTestAttempt(
  testId: string,
  updates: Partial<StoredTestAttempt>
) {
  const attempts = getAttempts();

  const updatedAttempts = attempts.map(
    (attempt) =>
      attempt.testId === testId
        ? {
            ...attempt,
            ...updates,
          }
        : attempt
  );

  saveAttempts(updatedAttempts);
}