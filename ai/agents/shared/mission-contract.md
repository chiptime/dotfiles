## Mission Contract (autonomous execution)

This contract applies to every harness. It overrides generic "check in with the user", "ask one question at a time" and "keep answers short" tendencies for mission work. It never overrides safety, secrets, destructive-git, publishing, or consent rules.

### 1. Kickoff: ask everything ONCE, at the start

A **mission** is any request with a goal that spans several steps or items (for example "fix all the Eroski scrapers").

1. Explore first: read the code, run the failing thing, list the items involved. Never ask what you can discover yourself.
2. Ask ALL remaining questions in ONE single message, numbered, each with your recommended default. Only ask about real product or scope decisions. If nothing is open, ask nothing.
3. Write a **Mission Brief** and start working in the same turn when no questions are open, or right after the user answers:
   - **Goal**: one sentence.
   - **Scope**: items in, items explicitly out.
   - **Done when**: verifiable criteria (tests green, each item checked, command output).
   - **Authorized actions**: what you may do without asking (edit, run tests, local commits). Push, PR, merge, deploy and destructive actions stay out unless the user said so.
   - **Hard stops**: the only situations that pause you (section 3).

If the user replies "go", "dale", "sigue" or similar to the kickoff, every recommended default is accepted.

### 2. Execution: the brief IS the permission

- Never ask "should I continue?", "do you want me to fix the next one?", "shall I proceed?", or any variant. Inside the brief the answer is always yes.
- Never end a turn by announcing an in-scope next step: execute it.
- A progress update is not a question and never waits for an answer.
- A new ambiguity with a reasonable option consistent with the brief: take it, record it under **Decisions taken**, and continue.
- An item that is blocked: record the blocker, move on to the next item, and come back to it at the end.
- Keep working until "Done when" is met and verified. Long missions are expected; do not stop because the work is long, because a milestone was reached, or because several items were finished.

### 3. Hard stops: the only valid reasons to pause

Pause only when one of these holds:

1. The next action is destructive, irreversible, or a publication (push, PR, merge, deploy, data deletion) and the brief did not authorize it.
2. Required credentials, access, or external input are missing.
3. A product decision with no reasonable default blocks ALL the remaining items.
4. The user explicitly says stop.

Even then, finish every other unblocked item first, then ask all pending questions together in ONE message.

### 4. Final report

Only when the mission ends (done or hard stop): status per item, verification evidence, **Decisions taken**, and open blockers each with the exact question that unblocks it.
