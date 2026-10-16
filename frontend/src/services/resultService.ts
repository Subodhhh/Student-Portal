import { RESULT_API_URL as RESULT_SERVICE_URL } from "./api";

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