const RESULT_SERVICE_URL = "http://localhost:8007";

export async function getResultDetails(attemptId: string) {
  const response = await fetch(
    `${RESULT_SERVICE_URL}/results/attempts/${attemptId}/details`,
    {
      method: "GET",
      credentials: "include",
    }
  );

  if (!response.ok) {
    throw new Error("Failed to fetch result details");
  }

  return response.json();
}