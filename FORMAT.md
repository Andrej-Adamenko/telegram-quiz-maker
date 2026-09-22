# Telegram quiz JSON format

Create one valid JSON object using this contract. Save it as UTF-8 `quiz.json` and send it to the bot. Return only JSON, without a code fence or commentary. The complete file must not exceed 256 KiB; this is the bot's limit, not a Telegram API limit.

Quiz content supports Unicode in any language. Follow the author's language choice; if none is specified, infer it from their context. The bot's interface and documentation being English does not require English quiz content. The bot does not translate submitted content or append English strings to quiz fields. The author controls the topic and purpose of every field; no field is reserved for a scenario, instruction, or particular teaching method.

| Field | Requirement |
| --- | --- |
| `version` | Required: integer `1`. |
| `question` | Required: nonempty plain text, up to 300 characters after substitution. |
| `description` | Optional MarkdownV2 description, up to 1024 characters after Telegram parses the markup. |
| `options` | Required: 1–12 distinct nonempty plain-text strings, each up to 100 characters after substitution. Options differing only in case or surrounding whitespace count as duplicates. |
| `correct_option` | Required: the **one-based** integer index of the correct option, from 1 to the number of options. |
| `explanation` | Optional MarkdownV2 explanation: up to 200 characters and at most two LF line breaks after Telegram parses the markup. |
| `telegraph` | Optional object with exactly `title` and `text`, described below. |
| `explanation_document` | Optional object with exactly `filename` and `text`, described below. |

Do not add unknown fields or duplicate keys. Strings must contain valid Unicode without disallowed control characters. Required nonempty strings cannot consist only of whitespace. `NaN` and `Infinity` are not allowed. The bot creates an anonymous quiz with one correct answer, no option shuffling, and revoting disabled. These settings are not JSON fields.

## MarkdownV2 in Description and Explanation

Both fields are **always** sent to Telegram as MarkdownV2. HTML, legacy Markdown, and a plain-text fallback are not supported. Do not add `parse_mode`, `description_parse_mode`, or `explanation_parse_mode` fields.

In ordinary text outside special MarkdownV2 contexts, escape these characters with a backslash:

```text
_ * [ ] ( ) ~ ` > # + - = | { } . !
```

Do not escape syntax you intentionally use for formatting or links as ordinary punctuation. Code and link destinations have different escaping rules. JSON requires the backslash itself to be doubled: a visible period is written as `\\.`, and an exclamation mark as `\\!`. Leave the literal marker `{{telegraph.url}}` unescaped; the bot escapes the URL substituted for it.

For example, an English JSON field can be written as:

```json
"explanation": "Ask before sharing\\. [Full explanation]({{telegraph.url}})"
```

This fragment requires a `telegraph` object in the complete quiz. Its English label is only an example; use the author's chosen language for actual content.

`question` and `options` use plain text, not MarkdownV2. Do not add backslashes before their ordinary punctuation. Named links are not supported in those fields.

The 1024/200-character limits and two-LF limit apply to the **final displayed text**, not the source markup. Each `description` or `explanation` source string may contain up to 262144 characters within the overall JSON-file size limit. The bot performs structural validation; Telegram validates MarkdownV2, final text length, and line breaks. Malformed markup does not trigger a fallback to plain text.

## Public Telegraph page

`telegraph` contains:

- `title`: a nonempty page title of 1–256 characters.
- `text`: the complete nonempty page content in supported ordinary Markdown, not MarkdownV2. Source size is bounded by the overall 256 KiB JSON-file limit. Converted Telegraph nodes must fit within the API limit of 64 KiB of serialized UTF-8 content; the bot checks this size.

The bot automatically publishes one page accessible to anyone with its URL. You do not need to create the page or obtain its URL manually. Put the actual text in `text`, not a file path, URL, image, or base64. For predictable formatting, use simple headings, paragraphs, lists, bold text, and ordinary Markdown links.

When `telegraph` is provided, at least one poll text field must contain the literal marker `{{telegraph.url}}`:

- In `question`, `description`, `explanation`, or any `options` item, a plain `{{telegraph.url}}` is replaced by the page's full URL. The bot automatically escapes that URL for MarkdownV2 in Description and Explanation.
- Only `description` and `explanation` support `[Link label]({{telegraph.url}})`. The user sees the clickable label; only that label counts toward displayed text length, excluding the URL and markup. Format the label using MarkdownV2; the bot escapes the substituted URL automatically.
- You may repeat the marker: all occurrences refer to the same page. The author chooses surrounding text and link placement; the bot appends no fixed phrase.
- Do not use the marker without a `telegraph` object. Unknown or unresolved markers are rejected.

When using a full URL, leave enough room for its length in the final text. Telegram may detect a MarkdownV2 error after the page has already been published. Resubmitting the same page material reuses the existing page.

## Text attachment

`explanation_document` contains:

- `filename`: up to 80 characters, ending in `.md` or `.txt`. The first character must be an ASCII letter or digit; remaining characters may be ASCII letters, digits, periods, hyphens, or underscores. Paths and `..` are forbidden. Example: `quiz-explanation.md`.
- `text`: the complete nonempty file content, at most 131072 bytes in UTF-8. This is an additional attachment limit imposed by the bot. Include the actual text as a JSON string, not a file path, URL, or base64.

The bot attaches this text as a file to the quiz explanation. `telegraph` and `explanation_document` may be used together or separately; both are optional. Keep the explanation, correct answer index, and option order consistent.

## API references

Telegram's [sendPoll documentation](https://core.telegram.org/bots/api#sendpoll) defines poll fields and rendered text limits. Its [MarkdownV2 documentation](https://core.telegram.org/bots/api#markdownv2-style) defines formatting and escaping. The [Telegraph API](https://telegra.ph/api) defines account creation, page content, and publication limits. File-size caps, owner-only access, and the JSON structure described here are this project's additional constraints.
