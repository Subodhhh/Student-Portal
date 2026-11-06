/** Shared validation patterns. Framework-agnostic so they can be unit-tested in isolation. */

// ---------------------------------------------------------------------------
// Email
// ---------------------------------------------------------------------------
// Pragmatic check: something@something.tld, no whitespace.
// Not RFC 5322-complete on purpose — real deliverability is verified by the backend / email provider.
export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export const isValidEmail = (value: string): boolean => EMAIL_RE.test(value.trim());

// ---------------------------------------------------------------------------
// Phone
// ---------------------------------------------------------------------------
// Digits only, exactly 10 of them. No spaces, dashes, parentheses or country code.
export const PHONE_DIGITS = 10;
export const PHONE_RE = /^\d{10}$/;

/** True only if the value is exactly 10 digits (0-9). */
export const isValidPhone = (value: string): boolean => PHONE_RE.test(value.trim());

/**
 * Strip everything that is not a digit, so "98765-43210" / "98765 43210" / "(98765) 43210"
 * all normalise to "9876543210" before validation or submission.
 * Use this in onChange handlers if you want to be forgiving while typing.
 */
export const normalisePhone = (value: string): string => value.replace(/\D/g, "");

// ---------------------------------------------------------------------------
// Optional: human-readable messages (reuse in Field error props / Alert text)
// ---------------------------------------------------------------------------
export const phoneError = (value: string): string | undefined => {
  const digits = normalisePhone(value);
  if (!digits) return "Phone number is required";
  if (digits.length !== PHONE_DIGITS) return `Phone number must be ${PHONE_DIGITS} digits`;
  return undefined;
};