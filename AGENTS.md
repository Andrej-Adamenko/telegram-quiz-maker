# Agent instructions

## Start here

Read `README.md` when starting a new task: it is the source of project purpose, accepted decisions, and implementation context. Read `FORMAT.md` before changing quiz input and `DEPLOYMENT.md` before deployment work. Review relevant GitHub Issues for unresolved questions, bugs, and planned work. Do not assume that a new chat has the previous conversation.

Check the current Git state before editing. Treat `main` on the configured GitHub origin as the shared source of truth. Do not reintroduce obsolete history from an older checkout.

## Product constraints

- Keep the bot generic: it turns authored JSON into a native Telegram quiz, optionally publishing a Telegraph article. It does not generate or translate content and does not call a model API.
- Interface, errors, documentation, code comments, and generic test wording are English. The shipped educational example is Ukrainian. Do not add Russian-language text to repository files. This repository-language choice must not restrict the languages accepted by the bot.
- Preserve authored Unicode content and the author's choice of how to use each field. The example's lamp instruction in Question and scenario in Description are example choices, not bot rules.
- Description and Explanation use Telegram's native MarkdownV2. Question and Options remain plain text. Telegraph article text uses the supported ordinary Markdown subset. Keep these contracts distinct; do not add an HTML selector or a custom parser for Telegram poll fields.
- Preserve `{{telegraph.url}}` substitution and user-defined link labels. Do not hardcode article labels or educational text into bot behavior.
- Preserve owner-only creation, persistent update deduplication, article caching, and conservative handling of uncertain network outcomes. Avoid duplicate quizzes or articles and preserve the SQLite state during updates.

## Validation

Python 3.12+ and the standard library are sufficient. After behavior changes, run:

```sh
python -m unittest discover -s tests
python bot.py --check example-quiz.json
```

For setup-helper changes, also run `python deploy/setup-token.py --check`. These checks are offline. Do not publish Telegraph pages or send quizzes merely to test documentation changes. Run checks appropriate to the change; documentation-only edits do not require new tests.

## Deployment and privacy

Read `.local/DEPLOYMENT.md` if present when working on the owner's deployment. It is a local-only operational note, not part of the public repository; it is absent from a fresh clone or worktree. Verify the live release before changing it. GitHub publication and server deployment are separate actions.

Never commit local operational notes, real owner identifiers, deployment endpoints, tokens, SSH keys, populated environment files, runtime databases, or logs. Publish an explicit file list. Do not print or copy token contents into chat. Keep one process per Telegram token and preserve state and credentials across releases.

## Preserve context at every response

At every response, check whether the conversation or work produced new context that must survive a new chat. Save important decisions, constraints, discoveries, unresolved questions, and changes in task status without waiting for a reminder. Keep the appropriate records current during the work and before the final response. If nothing meaningful changed, do not create artificial edits or duplicate records.

Use these established locations:

- `README.md`: project purpose, accepted product and architectural decisions, their relevant rationale, and current implementation context.
- `AGENTS.md`: instructions for the model only. Do not turn it into a project history, transcript, task backlog, or duplicate of the README.
- GitHub Issues in this repository: unresolved problems, open design questions, proposed improvements, and development plans. Check for an existing issue before creating one; update the same issue as work progresses. Distinguish a proposal from an approved decision and describe the expected result. Close issues only when resolved or explicitly declined. Do not invent work merely to populate the tracker.
- Existing specialized documentation: keep the exact input contract in `FORMAT.md`/`quiz.schema.json` and deployment procedures in `DEPLOYMENT.md`. Link these sources rather than copying the same details into multiple files.
- The ignored local deployment note: private connection and operational details that must never enter the public repository or public issues.

When an issue produces an accepted lasting decision, summarize that decision in the README and link the issue as useful. Git commits and pull requests record implementation changes. Do not create a separate context, memory, backlog, TODO, or decision-file scheme by default. If a new category genuinely needs another home, explain the tradeoff and recommend an established convention to the user before introducing it.

Persist content in English and preserve the repository's language policy. Keep records concise, factual, and consistent; do not copy chat transcripts or present old snapshots as live checks. If storage or publication fails, say what was saved locally and what remains unsynchronized. Do not claim that context was saved when it was only discussed.
