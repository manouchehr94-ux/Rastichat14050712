# Design Tokens

Canonical values extracted from the HTML prototype (`index (3)(1).html`). This repository has no npm workspace linking the widget and the two Next.js dashboards into one build (each has its own `package.json`/`node_modules`), so there is no single importable token package. These values are instead **mirrored** in each surface and must be kept in sync by hand when changed:

- `apps/operator-dashboard/src/app/globals.css` — Tailwind v4 `@theme` custom properties (`--color-terracotta`, `--color-gold`, etc.), generating utility classes like `bg-terracotta`.
- `packages/widget/src/main.ts` — inline `<style>` template literal inside `initUI()`.

`apps/platform-dashboard` intentionally keeps its own indigo scheme: it has no rich-chat/3-pane surface in scope for prototype alignment (see `docs/audit/PROTOTYPE_ALIGNMENT_CURRENT_STATE.md`).

## Color

| Token | Hex | Usage |
|---|---|---|
| `cream` | `#FAF3EA` | App background |
| `cream-soft` | `#F1E4D3` | Secondary background |
| `terracotta` | `#BC5A38` | Primary brand color, outgoing bubbles, primary buttons |
| `terracotta-2` | `#A1492A` | Primary gradient endpoint, darker accents |
| `terracotta-soft` | `#F3DCC9` | Selected/hover surfaces |
| `terracotta-tint` | `#FBEEE4` | Faint tinted backgrounds |
| `gold` | `#C2954A` | Accent, ratings, secondary gradient start |
| `gold-soft` | `#EBD9B6` | Gold-tinted chip/badge backgrounds |
| `success` | `#5E8A56` | Online status, read receipts, success states |
| `success-soft` | `#E4EEDF` | Success-tinted backgrounds |
| `danger` | `#C0504A` | Destructive actions |
| `line` | `#ECDCC8` | Borders |

Outgoing message bubbles use a gradient, not a flat fill: `linear-gradient(135deg, #C2603B, #9F4427)`.

## Typography

Persian text uses **Vazirmatn** (weights 400–800), loaded via Google Fonts in both the widget (dynamic `<link>` injected at init) and the operator dashboard (`next/font/google`). Falls back to `Tahoma, Arial, sans-serif` if the font fails to load — this fallback must never block rendering.

## Shape & elevation

- Cards/panels: `border-radius: 28px` (desktop), full-bleed with no radius on mobile.
- Message bubbles: `border-radius: 20px`, with the "tail" corner reduced to `7px`.
- Chips/badges: fully rounded (`border-radius: 999px`).
- Shadows are soft and warm-tinted, not neutral gray: e.g. `0 24px 60px -24px rgba(96,58,32,.45)`.

## Known gap

Full pixel-for-pixel parity with the prototype's shadow/spacing system across every component was not re-verified component-by-component in this pass; colors, typography, and the two structural gaps called out in the capability matrix (mobile customer-info panel, tag chips) were the priority. Treat this file as the source of truth going forward and expand it as more surfaces are aligned.
