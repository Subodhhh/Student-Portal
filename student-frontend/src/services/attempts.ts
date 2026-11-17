/** GET /attempts/test/{test_id} answers 200 {"has_attempt": false} when the student has not started the test (older attempt services
 *  answered 404, which callers still accept). A real attempt is the unchanged attempt object; any other status is an error. */
export function isNoAttempt(body: unknown): boolean {
  return typeof body === "object" && body !== null && (body as { has_attempt?: unknown }).has_attempt === false;
}
