// Ambient types for the suite.
interface Window { RastiChat?: { logout?: () => Promise<void> | void } & Record<string, unknown> }
