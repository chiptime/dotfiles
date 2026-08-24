/**
 * Store-aware canonical persistence with an exactly-once receipt.
 * The CLI/backend owns ALL filesystem writes; the router only mem_saves
 * when receipt.engramRequired. One owner per store, written once.
 */
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname } from 'node:path';
import { createHash } from 'node:crypto';
import type { Receipt, Store } from './outcomes';

export interface PersistOptions {
	store: Store;
	change: string;
	artifactText: string;
	openspecRoot: string;
	workdir: string;
}

export interface PersistOutcome {
	receipt: Receipt;
	receiptPath: string;
	artifactDest?: string;
	sha256: string;
}

/** Receipts are keyed per run workdir; a present receipt means already persisted. */
export function persistExploration(opts: PersistOptions): PersistOutcome {
	const receiptPath = `${opts.workdir}/receipt.json`;
	const sha256 = createHash('sha256').update(opts.artifactText).digest('hex');
	if (existsSync(receiptPath)) return JSON.parse(readFileSync(receiptPath, 'utf8')) as PersistOutcome;
	let wroteOpenspec = false;
	let artifactDest: string | undefined;
	if (opts.store === 'openspec' || opts.store === 'hybrid') {
		artifactDest = `${opts.openspecRoot}/changes/${opts.change}/exploration.md`;
		mkdirSync(dirname(artifactDest), { recursive: true });
		writeFileSync(artifactDest, opts.artifactText);
		wroteOpenspec = true;
	}
	const receipt: Receipt = {
		store: opts.store,
		wroteOpenspec,
		engramRequired: opts.store === 'engram' || opts.store === 'hybrid',
	};
	mkdirSync(opts.workdir, { recursive: true });
	writeFileSync(receiptPath, JSON.stringify({ receipt, receiptPath, artifactDest, sha256 }, null, '\t'));
	return { receipt, receiptPath, artifactDest, sha256 };
}
