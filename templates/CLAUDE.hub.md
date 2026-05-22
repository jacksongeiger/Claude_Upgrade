# Merkavian Hub — Project CLAUDE.md

## What This Project Is

Personal AI Hub on ARM server `192.18.128.170`, deployed at `hub.192-18-128-170.nip.io` on port `3002` via PM2 and Caddy basic_auth. Built as Coinbase APM internship prep using the real open source Coinbase Design System (CDS). Central interface for all projects with closeable sidebar panels.

## Stack

- Next.js 14 App Router
- TypeScript
- `@coinbase/cds-web` + `framer-motion@^10` (CDS requires framer-motion as peer dep)
- `@coinbase/cds-icons`
- **NO Tailwind** — CDS has its own styling system via StyleProps and CSS Variables
- Node managed via Homebrew (no nvm)

## CDS Setup

> ⚠️ **These reflect real findings from building merkavian-hub V1 — CDS component names and exports do not always match the documentation.** When in doubt, read the `.d.ts` files under `node_modules/@coinbase/cds-web/dts/` directly. The exports list is in `package.json` under `exports`.

Install:

```bash
npm install @coinbase/cds-web framer-motion@^10 @coinbase/cds-icons
```

Entry point must import in this order:

```ts
import '@coinbase/cds-icons/fonts/web/icon-font.css';
import '@coinbase/cds-web/defaultFontStyles';
import '@coinbase/cds-web/globalStyles';
```

Root wrapper: `MediaQueryProvider` wrapping `ThemeProvider`. The `ThemeProvider` takes a **`theme` prop with `defaultTheme` as the value**, plus `activeColorScheme` as a separate prop. **Never use light mode.**

```ts
import { MediaQueryProvider, ThemeProvider } from '@coinbase/cds-web/system';
import { defaultTheme } from '@coinbase/cds-web/themes/defaultTheme';

<MediaQueryProvider defaultValues={{ colorScheme: 'dark' }}>
  <ThemeProvider theme={defaultTheme} activeColorScheme="dark">
    {children}
  </ThemeProvider>
</MediaQueryProvider>
```

Key dark mode CSS Variables (the **actual** prefix is `--cds-color-*`, *not* `--darkColor-*` as some docs/examples suggest):

| Token | Approx. value | Purpose |
|---|---|---|
| `--cds-color-backgroundBase` | `rgb(10,11,13)` | Page background |
| `--cds-color-backgroundSurface` | `rgb(20,21,25)` | Elevation 1 (cards) |
| `--cds-color-backgroundElevated` | `rgb(30,32,37)` | Elevation 2 (hover) |
| `--cds-color-foregroundPrimary` | `rgb(255,255,255)` | Primary text / values |
| `--cds-color-foregroundMuted` | `rgb(138,145,158)` | Muted/secondary text |
| `--cds-color-foregroundPositive` | `rgb(39,173,117)` | Positive green (online, gains) |
| `--cds-color-foregroundNegative` | `rgb(240,97,109)` | Negative red (errors, losses) |
| `--cds-color-foregroundWarning` | `rgb(248,150,86)` | Warning orange (stale, retries) |
| `--cds-color-bgLine` | `rgba(138,145,158,0.2)` | Hairline borders |

(Exact values come from `defaultTheme` at runtime; values above are the observed RGB. Always reference by variable name, never by hex.)

Spacing (8px base scale):

```
--space-1=8px
--space-2=16px
--space-3=24px
--space-4=32px
--space-6=48px
```

Border radius:

```
--borderRadius-200=8px
--borderRadius-300=12px
--borderRadius-400=16px
```

## Component Rules

Always check CDS before writing any custom component.

Available CDS components: `Button`, `IconButton`, `Sidebar`, `SidebarItem`, `NavigationBar`, `Text` (and the specific variants `TextTitle1`/`TextTitle2`/`TextTitle3`/`TextBody`/`TextCaption`/`TextLabel1`/`TextLabel2`/`TextDisplay1`/`TextDisplay2`/`TextDisplay3`/`TextHeadline`/`TextLegal`), `Box`, `HStack`, `VStack`, `Grid`, `ContentCard` (+ `ContentCardHeader`, `ContentCardBody`, `ContentCardFooter`), `Toast`, `Modal`, `Spinner`, `ProgressBar`, `Table`, `Tabs`, `SegmentedTabs`, `Tooltip`, `Banner`, `Dropdown`, `Switch`, `TextInput`, `SearchInput`, `Icon`, `Avatar`, `Tag`, `Divider`, `Collapsible`.

> ⚠️ **`TopNavBar` does not exist** — use **`NavigationBar`** from `@coinbase/cds-web/navigation`. It takes `start`, `end`, `bottom`, and `children` (page title) slots.

> ⚠️ **`DataCard` lives at `@coinbase/cds-web/alpha/data-card` and is unstable.** Prefer building metric cards on top of `ContentCard` (a stable, polymorphic VStack with default card styling) plus the `Text*` typography components. Do not depend on `alpha/` imports for anything load-bearing.

Import pattern:

```ts
import { Button } from '@coinbase/cds-web/buttons';
import { NavigationBar, Sidebar, SidebarItem } from '@coinbase/cds-web/navigation';
import { Spinner } from '@coinbase/cds-web/loaders';
import { Banner } from '@coinbase/cds-web/banner';
import { ContentCard } from '@coinbase/cds-web/cards';
import { TextTitle1, TextBody, TextCaption } from '@coinbase/cds-web/typography';
import { Box, HStack, VStack, Grid } from '@coinbase/cds-web/layout';
```

Rules:

- Use CDS `Text*` components for all typography — never raw HTML `h1`/`p`/`span` with manual font styles
- Use `Box`, `HStack`, `VStack`, `Grid` for all layout — these use the CDS spacing scale
- Use Framer Motion for all animations — never CSS transitions for anything animated
- Never hardcode hex colors — always use the `--cds-color-*` CSS variables or CDS StyleProps
- Check `21st.dev` before building any complex component from scratch
- For numeric/metric displays (PnL, trade counts, win rates): use `TextTitle1` or `TextDisplay3` directly (not a `font` prop on a generic `Text`)

## Project Structure

```
src/
  app/
    layout.tsx                 # CDS side-effect imports + Providers + HubShell
    providers.tsx              # "use client" — MediaQueryProvider + ThemeProvider
    page.tsx                   # Hub overview / home
    api/
      cryptobot/route.ts       # server-side proxy to 127.0.0.1:5050
      polybot/route.ts         # server-side proxy to 127.0.0.1:5001
  components/
    layout/
      HubShell.tsx             # flex layout combining sidebar + topnav + main
      HubSidebar.tsx           # CDS Sidebar + SidebarItem, Framer Motion width transition
      HubTopNav.tsx            # CDS NavigationBar
    services/
      CryptoTrackerPanel.tsx   # iframe embed
      RapidDrafterPanel.tsx    # iframe embed
      CryptoBotCard.tsx        # status card, calls /api/cryptobot
      PolybotCard.tsx          # status card, calls /api/polybot
      ServiceStatusCard.tsx    # shared base for bot status cards
      IframePanel.tsx          # shared iframe wrapper with timeout fallback
      PolchainPanel.tsx        # V2, placeholder only
      MerkavianPanel.tsx       # V2, placeholder only
    ui/
      StatusDot.tsx            # online/offline/warning indicator with pulse animation
      MetricCard.tsx           # reusable metric display built on ContentCard
  lib/
    proxyService.ts            # timeout-bounded fetch helper for API routes
```

## Services Map

| Service | Type | ARM Port | V1? | How Hub connects |
|---|---|---|---|---|
| crypto-tracker | Next.js | 3000 | Yes | iframe via HTTPS |
| rapid-drafter | Next.js | 3001 | Yes | iframe via HTTPS (auth handling required) |
| cryptobot | Flask JSON API | 5050 | Yes | Hub API route server-side proxy |
| polybot | FastAPI | 5001 | Yes | Hub API route server-side proxy |
| polchain | Not on ARM | — | V2 | Deploy to ARM first |
| merkavian-dashboard | Mac only :5055 | — | V2 | Migrate to ARM first |

## Iframe Embedding

- **crypto-tracker** embeds via `https://192-18-128-170.nip.io` — public, no auth
- **rapid-drafter** embeds via `https://rapid-drafter.192-18-128-170.nip.io` — has Caddy `basic_auth`. Use basic auth URL format `https://user:pass@rapid-drafter.192-18-128-170.nip.io`, or create a separate unauthenticated internal Caddy route. Document chosen approach in `CHANGELOG.md`.
- No `X-Frame-Options` headers on any service — iframes work today
- If adding CSP later: set `frame-ancestors 'self' https://*.192-18-128-170.nip.io` — **never `'none'`** or you will break Hub embedding

## Caddy Config to Add

Add this block to `/etc/caddy/Caddyfile` on ARM:

```caddy
hub.192-18-128-170.nip.io {
    encode gzip
    basic_auth {
        jackson <bcrypt-hash>
    }
    reverse_proxy 127.0.0.1:3002
}
```

Generate hash:

```bash
caddy hash-password --plaintext yourpassword
```

Reload after changes:

```bash
sudo systemctl reload caddy
```

## Running Locally (on ARM)

```bash
# SSH into ARM
ssh arm

# Dev server
npm run dev -- --port 3002

# Test from Mac (tunnel)
ssh -L 3002:localhost:3002 arm
# then open http://localhost:3002

# Production
npm run build && pm2 restart hub

# Logs
pm2 logs hub

# Caddy reload
sudo systemctl reload caddy
```

## Port Reservation

Port **3002** is reserved for Hub. Do not use any other port without checking first.

Existing ports in use on ARM:

| Port | Service |
|---|---|
| 3000 | crypto-tracker |
| 3001 | rapid-drafter |
| 5001 | polybot (now 127.0.0.1 only) |
| 5050 | cryptobot (now 127.0.0.1 only) |
| 11434 | ollama |
| 5432 | postgres |

## Auth and Security Rules

- cryptobot (`:5050`) and polybot (`:5001`) have **NO auth** — safe only because they are now bound to `127.0.0.1` on ARM
- Hub API routes must **NEVER** expose these services publicly — always proxy server-side only, never route raw ARM ports through Caddy
- Hub itself must always be behind Caddy `basic_auth` — never deploy without auth
- Never commit credentials — store in `.env`, reference via `process.env`
- `.env` must be in `.gitignore` before any other work begins

### Reaching cryptobot / polybot from the Mac (post-127.0.0.1 binding)

Because both bots are now bound to `127.0.0.1`, **`merkavian-dashboard` on Mac:5055 will show them as offline** until you do one of the following:

1. **SSH tunnel from the Mac** (quickest unblock):
   ```bash
   ssh -L 5050:127.0.0.1:5050 -L 5001:127.0.0.1:5001 arm -N
   ```
   Leave that running; the Mac dashboard will resolve `127.0.0.1:5050`/`5001` to the tunneled ports.
2. **Migrate merkavian-dashboard to ARM** (the V2 plan below) — then it co-locates with the bots and doesn't need cross-network access at all.
3. **Add an authenticated Caddy route** like `bots.192-18-128-170.nip.io` with its own `basic_auth` → `127.0.0.1:5050`/`5001`. Heavyweight; only do this if option 1 or 2 isn't viable.

## Error States

Every service card and iframe panel must handle these states:

- **Loading:** CDS `Spinner` centered in the card
- **Service down / API error:** CDS `Banner` with `variant="warning"` and "Service offline" message — never blank card or crash
- **Partial data:** show last known values with stale indicator (`foregroundMuted` text + timestamp)
- **Network timeout:** treat as service down after 5 seconds
- **Iframe load failure:** show fallback div with service name, status dot, and link to open in new tab

## Animations (Framer Motion)

- Sidebar open/close: width transition `cubic-bezier(0.4,0,0.2,1)` 220ms
- Panel/page transitions: opacity + `translateY(4px)` to `translateY(0)`, 200ms ease
- Status dots: pulse animation for online services
- Card reveals on mount: staggered fade-in with 50ms delay between cards
- Use Framer Motion for all animations — never raw CSS transitions for layout or visibility changes

## Performance

- Bot metrics poll every **30 seconds** — no WebSocket for V1
- Iframe panels load **lazily** — do not load until user clicks the tab
- Target Lighthouse score **90+** on performance before V1 is considered done

## Known Quirks

- `polymarket` and `ollama-keepalive` PM2 services are in crash-restart loops — do not include in Hub V1, do not attempt to restart without investigation
- Port mapping confirmed via Caddyfile: `:3000` = crypto-tracker, `:3001` = rapid-drafter (earlier investigation had these reversed)
- ARM is `aarch64` — if any npm package fails with a native module error, find an arm64-compatible version before trying workarounds
- merkavian-dashboard runs only on Mac at `:5055` — **NOT** on ARM, cannot be iframed from Hub in V1
- Spinner is marked `@deprecated` in CDS v9 (replacement is indeterminate `ProgressCircle` — slated for removal in v10)

## V1 Scope (build this, nothing else)

1. Hub shell: Sidebar + TopNav + main content area with Framer Motion transitions
2. Overview page: status cards for cryptobot + polybot with live polling
3. Crypto Tracker tab: full iframe embed
4. Rapid Drafter tab: full iframe embed
5. Caddy config update + PM2 deployment on ARM

## V2 (do not build until V1 is live and tested)

- Migrate merkavian-dashboard to ARM and embed or absorb
- Deploy polchain frontend to ARM and embed
- Add Ollama chat interface tab
- Add unified log viewer across all services
