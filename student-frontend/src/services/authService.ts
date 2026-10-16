import { AUTH_API_URL } from "./api";

export async function loginStudent(login: string, password: string) {
  const response = await fetch(`${AUTH_API_URL}/auth/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    credentials: "include",
    body: JSON.stringify({
      login,
      password,
    }),
  });

  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(data?.detail || "Login failed.");
  }

  return response.json();
}

export async function resetStudentPassword(
  token: string,
  newPassword: string,
) {
  const response = await fetch(`${AUTH_API_URL}/auth/reset-password`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      token,
      new_password: newPassword,
    }),
  });

  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(data?.detail || "Password reset failed.");
  }

  return response.json();
}

export async function forgotStudentPassword(email: string) {
  const response = await fetch(`${AUTH_API_URL}/auth/forgot-password`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      email,
    }),
  });

  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(data?.detail || "Password reset request failed.");
  }

  return response.json();
}