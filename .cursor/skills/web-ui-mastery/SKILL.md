---
name: web-ui-mastery
description: Use when building or redesigning any website, landing page, web app, dashboard, admin panel or CMS UI. Produces distinctive, production-grade interfaces with a deliberate design plan, strong typography, accessible color, purposeful motion and a self-critique pass. Orchestrates frontend-design, ui-ux-pro-max, design-taste, shadcn, web-design-guidelines and webapp-testing when they are installed.
---

# Web UI Mastery

You are the design lead and the front-end engineer. The goal is an interface that looks like a specific team designed it for a specific audience, and not like a template. Do not start coding until the plan in Phase 1 is written and reviewed.

## Companion skills (use if installed, otherwise follow this file alone)

| Need | Skill |
|---|---|
| Aesthetic direction, anti-generic design | `frontend-design` |
| UI/UX decisions, component and style choices | `ui-ux-pro-max` |
| Polish, motion, "this feels like AI slop" fixes | `design-taste-frontend` / `design-taste` |
| Production React components | `shadcn` |
| Accessibility and UX audit | `web-design-guidelines` |
| Browser verification | `webapp-testing` (Playwright) |

Install examples:
```
npx skills add https://github.com/anthropics/skills --skill frontend-design
npx skills add nextlevelbuilder/ui-ux-pro-max-skill
npx skills add h3nryprod01/design-taste
```

## Phase 0: Ground the brief

Before designing, write down in 4 lines:
1. **Subject:** what the product or site really is.
2. **Audience:** who uses it, on what device, in what mood (a finance analyst at a desk and a parent on a phone need different designs).
3. **Primary job:** the one action the page exists to drive (sign up, publish content, find data, buy).
4. **Content reality:** real copy, real data shapes, real images available. If none is given, invent realistic content for this subject. Never use "Lorem ipsum", "Feature One", or "Acme Inc".

If the subject is unclear, propose one and state your assumption. Ask at most one clarifying question.

## Phase 1: Design plan (write it before any code)

Produce a compact token system:

- **Color:** 4 to 6 named hex values (base, surface, text, muted, one primary accent, one semantic set for success/warning/danger). Check WCAG AA contrast (4.5:1 body text, 3:1 large text and UI parts).
- **Type:** one or two families with clearly different roles. Set a scale (for example 12, 14, 16, 20, 28, 40, 64), weights, line-height (1.5 to 1.7 body, 1.05 to 1.2 display) and a max line length under 75 characters.
- **Layout:** one sentence of concept plus an ASCII wireframe of the first screen. State alignment (left, centered, asymmetric) and the grid (12-col, 8px spacing base).
- **Shape and depth:** a deliberate radius scale (for example 4/8/16, with hierarchy, not one radius everywhere) and an elevation approach (borders, tonal layers, or shadows, pick one lead).
- **Principles:** 3 bullets on what makes this design unmistakably this product.

### Review pass
Compare the plan with what you would produce for any similar prompt. If a part matches the generic default, change it and say what you changed and why.

Common defaults to avoid unless the brief asks for them:
- Cream background with a serif display and terracotta accent.
- Near-black background with one neon accent.
- Identical rounded cards with the same soft grey shadow, plus gradient washes as decoration.
- A tracked-out ALL-CAPS eyebrow above every heading.
- Numbered markers (01 / 02 / 03) on content that is not a sequence.
- One word in a headline highlighted in a different color or italic.
- Arrow glyphs appended to every link or button.
- Purple-to-blue gradients, glassmorphism everywhere, emoji as icons.

## Phase 2: Build

### Stack defaults
- Next.js (App Router) or Vite + React, TypeScript, Tailwind CSS, shadcn/ui, Lucide icons, Framer Motion (or CSS) for motion. Use plain HTML/CSS for simple static pages.
- Define the tokens from Phase 1 once, as CSS variables and in the Tailwind theme. Components consume tokens only, with no hard-coded hex values scattered in JSX.
- Support light and dark mode when it suits the product. Dashboards usually need both.

### Page structure (marketing or AI-generated sites)
1. **Hero:** open with the most characteristic thing in the subject's world (a live demo, product shot, striking headline, real data). The default "big stat plus gradient" is only acceptable if it is truly the best option.
2. **Proof:** real numbers, logos, quotes, or a working example, near the top.
3. **Core value:** 2 to 4 sections that each answer one user question. Vary the layout between sections (split, bento, timeline, comparison). Do not repeat the same card grid.
4. **Objection handling:** pricing, FAQ, security, integrations as relevant.
5. **Final CTA and footer:** one clear action, complete navigation, legal links.

### Dashboard and CMS structure
- **Shell:** collapsible sidebar plus top bar (search, command palette `⌘K`, notifications, profile). Breadcrumbs on deep pages.
- **Overview page:** the 3 to 5 numbers or tasks that matter most come first. Charts must have titles, units, and a takeaway, not just decoration.
- **Content management views:** data table with sticky header, column sort, filters, saved views, bulk actions, row actions menu, pagination or virtual scroll.
- **Editors:** two-column (content left, settings and preview right), autosave state, draft/published status, version history, clear primary action ("Publish").
- **States for every screen:** loading (skeletons, not spinners), empty (explains and offers the next action), error (says what went wrong and how to fix it), success (confirms with the same verb as the button), and permission-denied.
- **Density:** offer comfortable and compact modes for tables. Use tabular numerals for figures.

### Typography rules
- Pick typefaces for this brief. Avoid defaulting to Inter, Roboto, or Space Grotesk without a reason. Load fonts with `font-display: swap` and subset them.
- Use type as a design element in the hero and section titles. Keep body text quiet and highly readable.
- Sentence case for UI text. Use ALL CAPS only for short, real abbreviations.

### Color and surface rules
- One accent color carries action. Use semantic colors only for status.
- Build depth with tonal steps between background, surface, and raised surface before reaching for shadows.
- Never rely on color alone to convey meaning. Pair it with an icon or label.

### Motion rules
- Spend motion in one place: one orchestrated page-load or hero moment.
- Respond to user actions (open, expand, drag, confirm) with 150 to 250 ms transitions using ease-out. Animate `transform` and `opacity` only.
- Do not add fade-and-slide-up to every section or hover lift to every card.
- Respect `prefers-reduced-motion`.

### Copywriting rules
- Write from the user's perspective. Name things by what users understand, not by how the system works.
- Buttons say exactly what happens: "Save changes", "Publish page". The toast then says "Page published".
- Errors never apologize and never stay vague: say what happened and the fix.
- Cut filler. Each text element does one job.

### Imagery and icons
- Use real or well-composed imagery that matches the subject. Use SVG illustrations, product screenshots, or CSS-built visuals when photos are unavailable. Never leave broken image links.
- One icon set, one stroke weight, consistent sizing.

### Quality floor (always, without announcing it)
- Responsive from 360px up: fluid type with `clamp()`, no horizontal scroll, touch targets at least 44px.
- Semantic HTML (`header`, `nav`, `main`, `section`, `footer`), one `h1`, logical heading order, labeled form controls, alt text.
- Visible keyboard focus on every interactive element, logical tab order, no focus traps outside modals.
- Core Web Vitals: LCP under 2.5s, CLS under 0.1. Lazy-load below-the-fold images, set explicit image dimensions, avoid layout shift from fonts.
- SEO basics: title, meta description, Open Graph tags, canonical URL.
- CSS hygiene: avoid conflicting selectors, avoid `!important`, keep specificity flat, and check section spacing is not doubled or cancelled.

## Phase 3: Critique and verify

1. Run the app and open it at 360px, 768px, and 1440px. Use `webapp-testing` or Playwright screenshots if available.
2. Score the result 1 to 5 on each: hierarchy, typography, color and contrast, spacing rhythm, consistency, accessibility, performance, originality.
3. Fix every score under 4. Then remove one decorative element (the "Chanel rule").
4. Run the accessibility check (axe or Lighthouse) and fix errors.
5. Report: the design plan in brief, what was built, what was changed after critique, and known gaps.

## Output contract

- Deliver working code, not descriptions.
- Keep a `DESIGN.md` at the project root with the tokens, type scale, component rules and voice notes, so future changes stay consistent.
- Make small, reviewable commits: tokens and layout first, then components, then pages, then polish.
