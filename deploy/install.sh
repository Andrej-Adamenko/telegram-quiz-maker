#!/bin/bash
# Debian, Python 3.12+, systemd. No packages or credentials are installed here.
set -Eeuo pipefail
umask 022

APP=/opt/telegram-quiz-bot
RELEASES=$APP/releases
CONFIG=/etc/telegram-quiz-bot
STATE=/var/lib/telegram-quiz-bot
UNIT=/etc/systemd/system/telegram-quiz-bot.service
SERVICE=telegram-quiz-bot.service
stage=

die() { printf '%s\n' "$*" >&2; exit 1; }
usage() {
    printf '%s\n' \
      'Usage: install.sh /absolute/source/directory RELEASE_ID' \
      '       install.sh --rollback [RELEASE_ID]'
    exit 2
}
valid_id() { [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ && "$1" != *..* ]]; }
cleanup() {
    # Only a path created by mktemp below is eligible for removal.
    if [[ -n "$stage" && "$stage" == "$RELEASES"/.staging-* && -d "$stage" ]]; then
        rm -rf -- "$stage"
    fi
}
trap cleanup EXIT
[[ $EUID -eq 0 ]] || die 'Run the installer as root.'
[[ $# -ge 1 && $# -le 2 ]] || usage
for command in python3 systemctl runuser useradd getent flock install realpath; do
    command -v "$command" >/dev/null || die "Missing prerequisite: $command"
done
/usr/bin/python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' \
    || die 'Python 3.12 or newer is required.'
[[ -d /run/systemd/system ]] || die 'systemd must manage this container.'
install -d -o root -g root -m 0755 "$APP" "$RELEASES"
# Serialize install and rollback so current/previous cannot race.
exec 9>"$APP/.deploy.lock"
flock -n 9 || die 'Another installation or rollback is running.'

if ! getent passwd quizbot >/dev/null; then
    useradd --system --user-group --home-dir "$STATE" --no-create-home \
      --shell /usr/sbin/nologin quizbot
fi
account=$(getent passwd quizbot)
IFS=: read -r _ _ account_uid account_gid _ account_home account_shell <<<"$account"
[[ "$account_uid" != 0 && "$account_home" == "$STATE" && "$account_shell" == /usr/sbin/nologin ]] \
    || die 'Existing quizbot account has unexpected settings; no release was activated.'
[[ "$(getent group quizbot | cut -d: -f3)" == "$account_gid" ]] \
    || die 'quizbot must use its own quizbot primary group.'
install -d -o root -g quizbot -m 0750 "$CONFIG"
install -d -o quizbot -g quizbot -m 0700 "$STATE"
if [[ -f "$CONFIG/bot.env" ]]; then
    chown root:root "$CONFIG/bot.env"
    chmod 0600 "$CONFIG/bot.env"
fi
if [[ -f "$CONFIG/token.txt" ]]; then
    chown root:quizbot "$CONFIG/token.txt"
    chmod 0640 "$CONFIG/token.txt"
fi

configuration_ready() {
    # Parse as data, never source the environment file as root shell commands.
    /usr/bin/python3 - "$CONFIG" <<'PY'
import pathlib, re, sys
directory = pathlib.Path(sys.argv[1])
try:
    values = {}
    for line in (directory / 'bot.env').read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, value = line.split('=', 1)
        if key in values:
            raise ValueError('duplicate setting')
        values[key] = value
    if (set(values) != {'BOT_TOKEN_FILE', 'OWNER_USER_ID', 'STATE_DIR'}
            or values['BOT_TOKEN_FILE'] != str(directory / 'token.txt')
            or values['STATE_DIR'] != '/var/lib/telegram-quiz-bot'
            or not re.fullmatch(r'[1-9][0-9]*', values['OWNER_USER_ID'])):
        raise ValueError('invalid configuration')
    token = (directory / 'token.txt').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
        raise ValueError('invalid token format')
except (OSError, UnicodeError, ValueError):
    sys.exit(1)
PY
}

was_active=false
if systemctl is-active --quiet "$SERVICE"; then was_active=true; fi
ready=false
if configuration_ready; then ready=true; fi
if "$was_active" && ! "$ready"; then
    die 'Configuration is incomplete or invalid; the running release was left unchanged.'
fi

previous_target=
if [[ -L "$APP/current" ]]; then
    previous_target=$(realpath -e "$APP/current")
    [[ "$previous_target" == "$RELEASES"/* && "$(dirname "$previous_target")" == "$RELEASES" ]] \
        || die 'current points outside the managed releases directory.'
elif [[ -e "$APP/current" ]]; then
    die 'current exists but is not a symbolic link.'
fi

check_release() {
    local target=$1
    [[ -f "$target/bot.py" && -f "$target/example-quiz.json" && -d "$target/tests" \
       && -f "$target/deploy/telegram-quiz-bot.service" ]] \
        || die 'Release is missing required files.'
    # Validation has no token, no bot configuration, and no root privileges.
    (
        cd "$target"
        env -i PATH=/usr/bin:/bin LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
          /usr/sbin/runuser -u quizbot -- /usr/bin/python3 -m unittest discover -s tests -v
        env -i PATH=/usr/bin:/bin LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
          /usr/sbin/runuser -u quizbot -- /usr/bin/python3 bot.py --check example-quiz.json
    )
}

if [[ "$1" == --rollback ]]; then
    if [[ $# -eq 2 ]]; then
        valid_id "$2" || die 'Invalid release ID.'
        target="$RELEASES/$2"
    else
        [[ -L "$APP/previous" ]] || die 'No previous release is recorded.'
        target=$(realpath -e "$APP/previous")
    fi
    [[ -d "$target" && ! -L "$target" && "$(dirname "$target")" == "$RELEASES" ]] \
        || die 'Rollback target is not a managed release directory.'
    check_release "$target"
else
    [[ $# -eq 2 && "$1" == /* ]] || usage
    source=$(realpath -e "$1")
    [[ -d "$source" ]] || die 'Source directory does not exist.'
    valid_id "$2" || die 'Invalid release ID.'
    target="$RELEASES/$2"
    [[ ! -e "$target" ]] || die 'Release ID already exists; choose a new ID.'
    stage=$(mktemp -d "$RELEASES/.staging-XXXXXXXX")
    chmod 0755 "$stage"
    install -d -m 0755 "$stage/tests" "$stage/deploy"
    # Explicit allowlist: credentials, databases and local environment files stay out.
    for filename in bot.py telegraph_pages.py example-quiz.json quiz.schema.json PROMPT.md FORMAT.md README.md; do
        install -o root -g root -m 0644 "$source/$filename" "$stage/$filename"
    done
    shopt -s nullglob
    tests=("$source"/tests/test_*.py)
    [[ ${#tests[@]} -gt 0 ]] || die 'No offline tests were supplied.'
    install -o root -g root -m 0644 "${tests[@]}" "$stage/tests/"
    for filename in install.sh telegram-quiz-bot.service bot.env.example; do
        install -o root -g root -m 0644 "$source/deploy/$filename" "$stage/deploy/$filename"
    done
    for filename in configure.py setup-token.py setup-telegraph.py deploy-server.ps1; do
        if [[ -f "$source/deploy/$filename" ]]; then
            install -o root -g root -m 0644 "$source/deploy/$filename" "$stage/deploy/$filename"
        fi
    done
    if [[ -f "$source/DEPLOYMENT.md" ]]; then
        install -o root -g root -m 0644 "$source/DEPLOYMENT.md" "$stage/DEPLOYMENT.md"
    fi
    chmod 0755 "$stage/deploy/install.sh"
    check_release "$stage"
    mv -- "$stage" "$target"
    stage=
    install -o root -g root -m 0600 "$target/deploy/bot.env.example" "$CONFIG/bot.env.example"
fi

[[ "$target" != "$previous_target" ]] || die 'That release is already current.'

switch_current() {
    local next=$1
    ln -s -- "$next" "$APP/.current-next-$$"
    mv -Tf -- "$APP/.current-next-$$" "$APP/current"
    install -o root -g root -m 0644 "$next/deploy/telegram-quiz-bot.service" "$UNIT"
    systemctl daemon-reload
}

# Code rollback never rewinds the persistent SQLite state (avoids duplicate polls).
switch_current "$target"
if "$ready"; then
    systemctl reset-failed "$SERVICE" || true
    if systemctl enable "$SERVICE" && systemctl restart "$SERVICE"; then
        sleep 2
    fi
    if ! systemctl is-active --quiet "$SERVICE"; then
        systemctl stop "$SERVICE" || true
        if [[ -n "$previous_target" ]]; then
            printf '%s\n' 'Service did not stay active; restoring the preceding release.' >&2
            switch_current "$previous_target"
            if "$was_active"; then systemctl restart "$SERVICE"; fi
        fi
        die 'Activation failed. Inspect journalctl -u telegram-quiz-bot.service.'
    fi
else
    printf '%s\n' 'Release installed; bot not started because bot.env/token.txt are not ready.'
fi
if [[ -n "$previous_target" ]]; then
    ln -s -- "$previous_target" "$APP/.previous-next-$$"
    mv -Tf -- "$APP/.previous-next-$$" "$APP/previous"
fi
printf 'Current release: %s\n' "$(basename "$target")"
if "$ready"; then
    printf '%s\n' 'Service is active. Check its journal and a real Telegram request to verify end-to-end operation.'
fi
