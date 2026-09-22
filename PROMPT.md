# Educational quiz generation prompt

Create one educational quiz for adults about interacting with children. Topic: [topic]. Child's age: [age, or choose an appropriate age]. Quiz language: [language; if omitted, infer it from the author's context]. Context and source material: [optional materials].

Write all quiz content, the Telegraph title and text, and link labels in the selected quiz language. These instructions being English must not cause the content to be translated into English. Keep JSON field names and the literal `{{telegraph.url}}` marker unchanged.

The goal is for an adult to remember an appropriate response by choosing an action in a concrete situation. Describe one recognizable situation and several plausible adult responses, preferably four. Exactly one response should be clearly correct under the stated circumstances. Incorrect options should reflect realistic misconceptions, not caricatures. Keep options comparable in length and style, without letter or number prefixes. Do not reveal the answer in Question or Description.

For this educational template, put the situation and the question to the adult in `description` (Description). Put a selected-language instruction in `question` (Question), equivalent to: “Choose an answer, then tap 💡 to open the explanation.” This layout is a preference of this template, not a general bot requirement. Other quizzes may use these fields for arbitrary content. Do not specify where the lamp icon appears, since its position may differ between Telegram clients.

After answering, the participant should see a short explanation with a link to the full discussion. Give a memorable rule in the short explanation. In the full discussion, address each option in its original order, explain the choice, offer one usable adult response, and include a brief recall question with its answer further down. Avoid shaming or judging the adult's or child's character. Do not promise that one phrase guarantees a particular reaction from a child.

The bot automatically publishes the full discussion on Telegraph and replaces `{{telegraph.url}}` with its real URL. The page will be accessible to anyone with that URL. Prepare text suitable for public publication that remains selectable and copyable. Do not create the page manually or invent its URL. The JSON author controls the link label, surrounding text, and placement; the bot appends no fixed wording.

If channel material is provided, choose a relevant issue that you did not find covered in the material you reviewed. Do not claim it is absent from the entire channel when only some posts are available. Do not invent sources; include links only to verified material.

Return one valid JSON object that can be saved as UTF-8 `quiz.json` and sent to the bot. Return only JSON, without a code fence or commentary. Follow FORMAT.md, quiz.schema.json, and these educational template requirements:

- `version`: integer `1`.
- `question`: 1–300 characters. Use the lamp instruction described above in the selected quiz language. Do not put the scenario here.
- `options`: preferably four plausible responses. The general format permits 1–12 distinct nonempty strings, each up to 100 characters after marker substitution.
- `correct_option`: the **one-based** integer index of the correct response, no greater than the number of options.
- `description`: the concrete situation and the question to the adult in **MarkdownV2**, up to 1024 characters after substitution and Telegram parsing. This template requires a nonempty Description; the generic bot permits omission or other content. Do not put the lamp instruction here or reveal the correct answer.
- `explanation`: a short memorable rule and a named link, such as `[Full explanation]({{telegraph.url}})`, in **MarkdownV2**. Translate the label into the selected quiz language. This template requires the link here so it is available with the explanation. The limit is 200 characters and at most two LF line breaks **after substitution and Telegram parsing**; only the visible link label counts, not its URL or markup. Do not promise an attached file.
- `telegraph`: an object with exactly `title` and `text`, required by this template. `title` is a nonempty public page title of up to 256 characters. `text` is the complete nonempty discussion in supported ordinary Markdown. The bot limits the whole JSON file to 256 KiB. Converted Telegraph nodes must not exceed the API limit of 64 KiB of serialized UTF-8 content. Include the actual text in the JSON string, correctly escaping line breaks and quotation marks; do not substitute a URL, file path, image, or base64.

The generic bot permits a plain `{{telegraph.url}}` marker in `question`, `description`, `explanation`, or any `options` item; it becomes the full URL. Named links such as `[Link label]({{telegraph.url}})` are allowed only in Description and Explanation. A `telegraph` object requires at least one marker in these poll text fields; every occurrence refers to the same page. The marker cannot be used without `telegraph`. Unknown and unresolved markers are rejected. Telegram limits apply to the final displayed text; the complete JSON file must fit within 256 KiB in UTF-8.

Description and Explanation always use MarkdownV2, without a mode selector or plain-text fallback. Escape reserved punctuation as specified in FORMAT.md; for example, a visible period in these fields is written as `\\.` in JSON. Leave `{{telegraph.url}}` literal: the bot escapes the substituted URL. Question and Options are plain text and do not require those punctuation escapes. `telegraph.text` uses supported ordinary Markdown, not MarkdownV2. Do not add parse_mode fields.

The bot also supports `explanation_document` with `filename` (`.md` or `.txt`) and `text` for a text attachment, and it can be combined with `telegraph`. This educational template needs only `telegraph`, so omit `explanation_document`.

Do not add other fields. The bot creates an anonymous quiz with one correct answer, no option shuffling, and revoting disabled. Keep option labels and ordering in the full discussion consistent with `options`, and ensure the explanation agrees with `correct_option`.
