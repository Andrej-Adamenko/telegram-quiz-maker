# Agent instructions

## Start here

Read `PROJECT_CONTEXT.md` when starting a new task in this repository. It records the product decisions that are not apparent from code alone. Read `README.md` for usage, `FORMAT.md` before changing quiz input, and `DEPLOYMENT.md` before deployment work. Do not assume that a new chat has the previous conversation.

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

## Keep continuity current

Update `PROJECT_CONTEXT.md` when accepted product decisions or implementation state change. Record deployment-specific changes only in the ignored local note. Keep handoff notes concise and factual; do not copy conversation transcripts or treat an old status snapshot as a live check.
