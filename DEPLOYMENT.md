# Debian deployment

Deploy a dedicated instance on a Debian server or container managed by systemd. Requirements are Python 3.12+, CA certificates, outbound HTTPS connectivity, and root access for installation. The application uses the Python standard library. The installer does not install operating-system packages or obtain credentials.

The bot uses long polling; it needs no inbound port or custom domain. Use one running process per Telegram bot token. The interface is English, while authored content may use any Unicode language without translation.

## Install from Windows PowerShell

The remote deployment helper requires PowerShell, OpenSSH `ssh.exe` and `scp.exe`, and `tar.exe`. Supply a private SSH key and a known-hosts file containing a verified server host key. Unknown or changed host keys are not automatically accepted.

From the repository directory, replace the example host and paths:

```powershell
.\deploy\deploy-server.ps1 `
  -Server root@server.example `
  -IdentityFile C:\keys\quiz_deploy_ed25519 `
  -KnownHostsFile C:\keys\quiz_known_hosts `
  -Port 22
```

The SSH user must have the root access expected by the installer. `-Port` defaults to 22. An optional `-ReleaseId` supplies an explicit release name; otherwise the helper generates one. The helper packages an explicit source-file list, uploads it, invokes the installer, and removes its temporary upload directory after success. Credentials and SQLite are not part of the release archive.

## Manual server installation

Transfer a clean source checkout to an absolute staging directory on the server. Include the application, tests, example, schema, documentation, and `deploy/`; exclude tokens, populated environment files, private keys, and databases.

As root, run:

```sh
bash /absolute/staging/path/deploy/install.sh /absolute/staging/path release-001
```

Use a new release ID for each update. IDs may contain ASCII letters, digits, periods, hyphens, and underscores, up to 80 characters; `..` is forbidden.

The installer creates the `quizbot` system user with no login shell. Root-owned code is stored under `/opt/telegram-quiz-bot/releases/ID`, with `current` selecting the active release. Before switching releases, the installer runs offline tests and example validation as `quizbot`, without credentials or network access. It installs `telegram-quiz-bot.service` for systemd.

Initial installation creates only `/etc/telegram-quiz-bot/bot.env.example`. Without valid `bot.env` and `token.txt`, it does not start the bot. Once configuration exists, later installations enable and restart the service.

## Configure the Telegram token and owner

Create a dedicated bot using [BotFather](https://t.me/BotFather). You will need its token and the owner's positive numeric Telegram user ID.

On Windows, Python with tkinter is required for the masked token-entry helper:

```powershell
python .\deploy\setup-token.py `
  --server root@server.example `
  --identity-file C:\keys\quiz_deploy_ed25519 `
  --known-hosts-file C:\keys\quiz_known_hosts `
  --owner-user-id 123456789 `
  --port 22
```

Replace all example values. The helper accepts the token in a masked window, sends it through SSH standard input, and stores it on the server. It checks the Telegram identity and refuses a bot with an active webhook. Existing credentials are not overwritten. Successful setup enables the service. `python deploy/setup-token.py --check` checks the helper offline without opening a window or requiring connection arguments.

Alternatively, after installation, use the same server configuration routine from an interactive root terminal:

```sh
cd /opt/telegram-quiz-bot/current
python3 -c '
import getpass, json, subprocess
owner = int(input("Owner Telegram user ID: "))
token = getpass.getpass("BotFather token: ")
subprocess.run(["python3", "deploy/configure.py"],
    input=json.dumps({"token": token, "owner_user_id": owner}),
    text=True, check=True)
'
```

The token is passed to the configuration routine through standard input, not as a command argument. Both setup paths create the following three unquoted environment settings:

```ini
BOT_TOKEN_FILE=/etc/telegram-quiz-bot/token.txt
OWNER_USER_ID=YOUR_POSITIVE_NUMERIC_TELEGRAM_ID
STATE_DIR=/var/lib/telegram-quiz-bot
```

The installer accepts exactly these settings in `bot.env`. Do not put token values in source files, shell history, command arguments, or release archives.

| Path | Owner | Mode |
| --- | --- | --- |
| `/etc/telegram-quiz-bot` | `root:quizbot` | `0750` |
| `/etc/telegram-quiz-bot/bot.env` | `root:root` | `0600` |
| `/etc/telegram-quiz-bot/token.txt` | `root:quizbot` | `0640` |
| `/etc/telegram-quiz-bot/telegraph-token.txt` | `root:quizbot` | `0640` |

## Optional Telegraph account

To use the JSON `telegraph` field, provision an account once. As root on the server:

```sh
cd /opt/telegram-quiz-bot/current
python3 deploy/setup-telegraph.py
```

The helper creates a Telegraph account and stores its token at `/etc/telegram-quiz-bot/telegraph-token.txt`. It does not print the token or overwrite an existing one. If setup reports an unfinished attempt, inspect that outcome before retrying. The running bot can read this file but does not create accounts itself.

Telegraph articles are public to anyone with the link. The bot publishes them from JSON and substitutes their URLs automatically. Do not add an extra Telegraph setting to this installer's `bot.env`: its default token path is already correct. Direct `bot.py` execution, outside this service configuration, supports a `TELEGRAPH_TOKEN_FILE` override.

## Start and verify

```sh
systemctl enable --now telegram-quiz-bot.service
systemctl status telegram-quiz-bot.service --no-pager
systemd-analyze verify /etc/systemd/system/telegram-quiz-bot.service
journalctl -u telegram-quiz-bot.service -n 40 --no-pager
```

An active service confirms a running process. Also verify the Telegram workflow: send `/start`, upload a valid JSON file, check its options and correct answer, then open 💡 after voting. If using `telegraph`, check the article, URL substitution, and selectable text. If using an attachment, check its contents in your target Telegram client.

[FORMAT.md](FORMAT.md) describes authoring and escaping. Description and Explanation always use MarkdownV2; Question and Options are plain text. The bot does not assign fixed purposes to these fields or add its own content. Operational replies should be English while quiz content retains its authored language.

The offline `--check` command does not reproduce Telegram's MarkdownV2 parsing or rendered text limits. Telegram can reject malformed formatting after an article was published; the cached article is retained for corrected submissions.

## Updates, logs, and persistent state

Deploy an update using the same PowerShell command or manual installer with a new release ID. Keep `/var/lib/telegram-quiz-bot/state.sqlite3` across updates: it contains update deduplication state and cached Telegraph URLs. Do not run a second manual process alongside the service, and do not delete the database to work around publication errors.

If a send or publication has an uncertain outcome, inspect whether it succeeded before resubmitting. The bot avoids automatic duplicate-producing retries.

Logs go to journald. Retention and rotation follow the server's journald configuration; the installer does not change the global policy. `LOG_FILE` is not used by this service.

```sh
systemctl is-enabled telegram-quiz-bot.service
systemctl is-active telegram-quiz-bot.service
journalctl -u telegram-quiz-bot.service --since '10 minutes ago' --no-pager
```

## Rollback

From the server, restore the previous retained release or name a specific one:

```sh
bash /opt/telegram-quiz-bot/current/deploy/install.sh --rollback
bash /opt/telegram-quiz-bot/current/deploy/install.sh --rollback release-001
```

From PowerShell:

```powershell
.\deploy\deploy-server.ps1 `
  -Server root@server.example `
  -IdentityFile C:\keys\quiz_deploy_ed25519 `
  -KnownHostsFile C:\keys\quiz_known_hosts `
  -Rollback -ReleaseId release-001
```

Rollback validates the target release, switches its code and service unit into place, and restarts it when configuration is ready. If a newly activated process exits within two seconds, the installer attempts to restore the preceding release and service state. Later failures require log inspection and explicit rollback.

Rollback does not rewind SQLite or credentials. Restoring old Telegram processing state can process messages again; review compatibility and backup requirements before any future database migration. Old releases are not removed automatically. After successful manual installation, its staging directory can be removed; the active copy resides under `releases/`.
