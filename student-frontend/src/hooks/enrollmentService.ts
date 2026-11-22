import { ENROLLMENT_API_URL } from "./api";

export async function getMyEnrollments() {
  const response = await fetch(`${ENROLLMENT_API_URL}/enrollments`, {
    credentials: "include",
    cache: "no-store",

  });

  if (!response.ok) {
    throw new Error("Failed to load institutes");
  }

  return response.json();
}