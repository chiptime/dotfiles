/**
 * Playwright adapter over the Teams web search page (TeamsSearchAdapter seam).
 *
 * Browser mechanics only: it launches the shared persistent profile, runs the
 * documented discovery search (`que` + Date=Today filter) and returns RAW
 * timestamp strings — locale/relative parsing belongs to src/core.ts, never
 * here. Unattended by design: a login wall surfaces as a search-box timeout,
 * the poller exits 2, and cron notifies. Selectors are locale-tolerant
 * (Spanish/English observed) and are proven by the manual E2E gate, not by
 * bun tests. Read-only: the only interactions are navigate, search, filter.
 */
import { chromium } from "playwright-core";
import type { BrowserContext, Page } from "playwright-core";
import type { TeamsSearchAdapter } from "./core.ts";

const TEAMS_URL = "https://teams.microsoft.com";
const SEARCH_TERM = "que";

/** New-client search box with aria/placeholder fallbacks for older renders. */
const SEARCH_BOX =
	'input#search-box-input, input[aria-label*="earch" i], input[placeholder*="earch" i]';
/** Search results render as treegrid rows; one row per matching message. */
const RESULTS_ROW = '[role="treegrid"] [role="row"]';

export interface TeamsPageOptions {
	/** Chromium binary the poller resolved from the Playwright cache. */
	executablePath: string;
	/** Shared persistent profile — same dir as the playwright_teams MCP. */
	userDataDir: string;
	/** Per-action timeout in ms (default 30s). */
	timeoutMs?: number;
	/** Headless by default: cron runs unattended. */
	headless?: boolean;
}

export class TeamsSearchPage implements TeamsSearchAdapter {
	private context: BrowserContext | null = null;
	private readonly options: Required<TeamsPageOptions>;

	constructor(options: TeamsPageOptions) {
		this.options = { timeoutMs: 30_000, headless: true, ...options };
	}

	async search(): Promise<{ rawTimestamp: string }[]> {
		const page = await this.openFilteredResults();
		try {
			// One search-result row per message; the author+time gridcell carries
			// the timestamp text (no <time> elements in the search results DOM).
			const rows = page.locator(RESULTS_ROW);
			const count = await rows.count();
			const out: { rawTimestamp: string }[] = [];
			for (let i = 0; i < count; i++) {
				const header = await rows
					.nth(i)
					.locator('[role="gridcell"]')
					.first()
					.textContent()
					.catch(() => null);
				const match = (header ?? "").match(/(\d{1,2}:\d{2}\s*(?:AM|PM)?)/i);
				if (match) out.push({ rawTimestamp: match[1] });
			}
			return out;
		} finally {
			await page.close();
		}
	}

	/** Idempotent: releasing the context frees the shared profile lock. */
	async close(): Promise<void> {
		const context = this.context;
		this.context = null;
		await context?.close();
	}

	/** Navigate, run the discovery search, apply Date=Today. */
	private async openFilteredResults(): Promise<Page> {
		if (this.context === null) {
			// Locked profile (MCP holding it) makes this launch throw — by design.
			this.context = await chromium.launchPersistentContext(this.options.userDataDir, {
				executablePath: this.options.executablePath,
				headless: this.options.headless,
				timeout: this.options.timeoutMs,
			});
		}
		const page = await this.context.newPage();
		page.setDefaultTimeout(this.options.timeoutMs);
		await page.goto(TEAMS_URL, { waitUntil: "domcontentloaded" });

		const searchBox = page.locator(SEARCH_BOX).first();
		// Login gate: an unauthenticated session never renders the search box.
		await searchBox.waitFor({ state: "visible" });
		await searchBox.fill(SEARCH_TERM);
		// Teams re-renders the input on typing; a page-level Enter survives detachment.
		await page.keyboard.press("Enter");

		const dateFilter = page.getByRole("button", { name: /^(date|fecha)/i });
		await dateFilter.click();
		const today = page
			.getByRole("option", { name: /^(today|hoy)$/i })
			.or(page.getByRole("menuitem", { name: /^(today|hoy)$/i }));
		await today.first().click();

		// Results pane must render before timestamps are read; an empty-but-
		// rendered list is a legitimate quiet day, a missing pane is a DOM change.
		// New-client search renders results in a treegrid that starts hidden
		// until results populate — "attached" is the reliable readiness signal,
		// and textContent() reads hidden rows fine.
		await page.locator('[role="list"], [role="treegrid"], [data-testid*="result" i]').first().waitFor({
			state: "attached",
		});
		return page;
	}
}
