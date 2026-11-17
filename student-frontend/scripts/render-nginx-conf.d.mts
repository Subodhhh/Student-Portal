export const PLACEHOLDER: string;
export function apiOrigins(env: Record<string, string | undefined>): string[];
export function contentSecurityPolicy(origins: string[]): string;
export function renderNginxConf(template: string, env: Record<string, string | undefined>): string;
