# Telegram Quiz Maker

Run your own Telegram bot that turns a JSON file into a native quiz. Prepare the content yourself or with a language model, send the file to your bot, and receive a quiz with a correct answer and an explanation under the lightbulb. Optionally publish a longer explanation on Telegraph and place its link anywhere supported in the quiz.

The bot uses Python 3.12+ and the standard library only. It does not call a model API. Its interface, operational messages, and documentation are English; authored content supports any Unicode language and is not translated. The included Ukrainian educational quiz demonstrates multilingual content, not a required language or topic. These repository language choices do not restrict the languages accepted by the bot.

## How it works

1. Prepare a UTF-8 `.json` file using [FORMAT.md](FORMAT.md). Give that document to a model when generating a quiz. [PROMPT.md](PROMPT.md) is an optional educational template about adult–child interactions.
2. Send the file in your bot's private chat. Only the configured owner may create quizzes.
3. Forward the returned quiz to a channel or Saved Messages. Participants choose an answer and open 💡 to see the explanation.

The bot creates an anonymous quiz with one correct answer, no option shuffling, and revoting disabled. It preserves the author's content and adds no fixed instructions or labels. Sending another JSON file creates another quiz; existing quizzes are not edited.

## Quick start

From the repository directory, run the offline checks with Python 3.12 or newer:

```sh
python bot.py --check example-quiz.json
python -m unittest discover -s tests
```

No token or network access is needed for these checks. `--check` validates structure and local limits; Telegram validates MarkdownV2 and rendered Description/Explanation limits when a quiz is sent.

To run a bot:

1. Create a dedicated bot with [BotFather](https://t.me/BotFather) and save its token in a private file, such as `data/token.txt`.
2. Set `BOT_TOKEN_FILE`, `OWNER_USER_ID`, and `STATE_DIR` as shown below. The owner ID is a positive numeric Telegram user ID, not a username or phone number.
3. Run `python bot.py`, open your bot in Telegram, and send `/start` followed by a JSON file.

Example environment setup for a POSIX shell; replace the numeric owner ID with your own:

```sh
export BOT_TOKEN_FILE="$PWD/data/token.txt"
export OWNER_USER_ID=123456789
export STATE_DIR="$PWD/data"
python bot.py
```

In PowerShell, use `$env:BOT_TOKEN_FILE`, `$env:OWNER_USER_ID`, and `$env:STATE_DIR` instead. [.env.example](.env.example) documents these settings; the program does not load `.env` files automatically.

A minimal quiz without a Telegraph account is:

```json
{
  "version": 1,
  "question": "Which option is a fruit?",
  "options": ["Apple", "Stone"],
  "correct_option": 1,
  "explanation": "An apple is a fruit\\."
}
```

For continuous operation, token provisioning, updates, and rollback on Debian/systemd, follow [DEPLOYMENT.md](DEPLOYMENT.md). That guide includes a masked token-entry helper and a separate Telegraph account setup step. The full [example-quiz.json](example-quiz.json) uses Telegraph, so configure that account before sending it.

## Quiz format

[FORMAT.md](FORMAT.md) is the complete contract; [quiz.schema.json](quiz.schema.json) describes the JSON structure. The contract follows native API capabilities and limits rather than restrictions of Telegram's interactive editor. One object defines one quiz with 1–12 options. `correct_option` is **one-based**. The author chooses how to use Question and Description. The educational example puts a lamp instruction in Question and the situation in Description; this is only a template preference. Its learning goal is to practice one concrete adult–child situation, compare possible adult responses, and reinforce the correct response while allowing further explanation. The bot itself imposes no educational method.

Description and Explanation always use Telegram MarkdownV2. Question and Options are plain text. Escape the punctuation in your MarkdownV2 source; the bot escapes substituted URLs. There is no HTML or JSON formatting-mode selector, custom MarkdownV2 parser, or automatic plain-text fallback. Telegram validates the syntax and rendered limits.

Add `telegraph: {"title": "...", "text": "..."}` to publish one article automatically. Place `{{telegraph.url}}` in Question, Description, Explanation, or an option. Description and Explanation also support a named link such as `[Read more]({{telegraph.url}})`. Every occurrence uses the same article URL. `telegraph.text` uses a supported subset of ordinary Markdown, distinct from MarkdownV2.

Telegraph was selected for longer explanations because readers can follow a link and select or copy the text; an image would not meet that requirement. Pages are public to anyone with their URL. The account token must be provisioned before using this feature; the running bot does not create accounts. The default token filename is `telegraph-token.txt` beside `BOT_TOKEN_FILE`, or in `STATE_DIR` when `BOT_TOKEN` is used. `TELEGRAPH_TOKEN_FILE` overrides this path for direct execution.

An optional `explanation_document` attaches text directly to the explanation. Supply a safe `.md` or `.txt` filename and the complete text, up to 128 KiB in UTF-8. Articles and attachments can be combined, used separately, or omitted. Attachment viewing and article presentation depend on the Telegram client.

## Configuration

| Variable | Meaning |
| --- | --- |
| `BOT_TOKEN_FILE` | File containing the Telegram bot token. |
| `BOT_TOKEN` | Alternative to `BOT_TOKEN_FILE`; do not set both. |
| `OWNER_USER_ID` | Positive numeric Telegram user ID allowed to submit quiz files. |
| `STATE_DIR` | Persistent SQLite directory; defaults to `data`. |
| `TELEGRAPH_TOKEN_FILE` | Optional override for the Telegraph account token file. |
| `LOG_FILE` | Optional rotating log file; otherwise logs go to the console. |

The Linux installer uses the narrower configuration in [deploy/bot.env.example](deploy/bot.env.example). Keep credentials, populated environment files, private connection details, logs, and runtime state out of Git and release archives. Public deployment examples contain generic values. Repository access does not grant access to a running server.

`/start` and `/help` show usage instructions. `/whoami` returns the sender's own numeric Telegram ID. Only the configured owner's private `.json` uploads are processed, with a maximum file size of 256 KiB. Quiz input cannot override access control or redirect output to another destination.

## State and failure handling

Keep `STATE_DIR/state.sqlite3` across restarts and updates; deleting it is not a way to recover from a failed request. It records Telegram update processing and caches published Telegraph pages. Repeated delivery of an already claimed update does not create another quiz. If a sending request has an unknown outcome, the bot does not blindly resend it; check the result before submitting the file again. This favors avoiding duplicate publication over guaranteed delivery.

If Telegram rejects formatting after a Telegraph page was published, correcting and resubmitting the same page material reuses the cached URL. An uncertain publication is blocked from automatic repetition until its outcome is checked.

Run one process per bot token. Long polling requires outbound HTTPS access to Telegram and, when used, Telegraph. No inbound port or custom domain is needed. If the bot already has a webhook, startup stops without removing it.

## Development

Accepted product decisions and their rationale belong in this README. [AGENTS.md](AGENTS.md) contains instructions for models working on the repository. The input contract stays in [FORMAT.md](FORMAT.md) and [quiz.schema.json](quiz.schema.json); installation and deployment procedures stay in [DEPLOYMENT.md](DEPLOYMENT.md). Open defects, unresolved questions, and development plans are tracked in [GitHub Issues](https://github.com/Andrej-Adamenko/telegram-quiz-maker/issues). Use these existing locations rather than creating separate context, backlog, or TODO files. Keep them current as decisions are made or work changes state, without copying conversation transcripts.

In addition to the quick-start checks, setup-helper changes can be checked offline with:

```sh
python deploy/setup-token.py --check
```

[GitHub Actions](.github/workflows/tests.yml) runs offline checks; it does not deploy to production. Sending a quiz or publishing an article has external side effects and is not part of these checks. The working bot is deployed separately on Debian/systemd, with connection details kept outside the public repository.

The recorded baseline on 2026-09-22 was commit `a85267b`, with 65 passing tests and successful GitHub Actions checks. This is a historical checkpoint, not a substitute for checking a new change; GitHub Issues is the current record of unresolved work.

## Project files

- [AGENTS.md](AGENTS.md): agent instructions for continuing work in a new session.
- [bot.py](bot.py): Telegram requests, JSON validation, and durable update handling.
- [telegraph_pages.py](telegraph_pages.py): article rendering, publication, and caching.
- [FORMAT.md](FORMAT.md): general authoring contract for people and models.
- [PROMPT.md](PROMPT.md): optional educational quiz prompt.
- [example-quiz.json](example-quiz.json): a Ukrainian educational example.
- [tests/](tests/): offline standard-library tests.
- [.github/workflows/tests.yml](.github/workflows/tests.yml): offline CI checks.
- [deploy/](deploy/): Debian/systemd installation and remote setup helpers.
