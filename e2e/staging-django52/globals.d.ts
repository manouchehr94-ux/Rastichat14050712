// Minimal ambient types so the suite typechecks without adding @types/node to the e2e package.
declare const process: { env: Record<string, string | undefined> };
interface Window { RastiChat?: { logout?: () => Promise<void> | void } & Record<string, unknown> }
