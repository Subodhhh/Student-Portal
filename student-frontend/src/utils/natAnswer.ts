/** A NAT answer as the server accepts it (digits, optional minus and decimal point, at most 6 decimal places and 12 significant
 * digits; whole numbers for an integer answer). null when it can be saved, else why not. The server checks it again. */
export function natAnswerProblem(text: string, integer: boolean): string | null {
  const m = /^-?(\d+)(?:\.(\d+))?$/.exec(text.trim());
  if (!m) return "Enter a number using digits, an optional minus sign and one decimal point (no commas, spaces, units or exponents).";
  const fraction = m[2] ?? "";
  if (fraction.length > 6) return "Use at most 6 decimal places.";
  const whole = m[1].replace(/^0+/, "");
  if ((whole ? whole + fraction : fraction.replace(/^0+/, "")).length > 12) return "Use at most 12 digits.";
  if (integer && /[1-9]/.test(fraction)) return "This answer must be a whole number.";
  return null;
}
