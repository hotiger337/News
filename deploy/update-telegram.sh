#!/usr/bin/env bash
# Run from the Azure git checkout as the deployment user.
set -euo pipefail
cd "$(dirname "$0")/.."
app=/opt/newsbot
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
"$app/.venv/bin/python" -m unittest discover -s tests -q
# Preserve local settings via a three-way merge with the previously shipped config.
git show 2c34ded:sources.toml > "$tmpdir/base"
if cmp -s sources.toml "$app/sources.toml"; then
    cp sources.toml "$tmpdir/merged"
elif ! git merge-file -p "$app/sources.toml" "$tmpdir/base" sources.toml > "$tmpdir/merged"; then
    echo '서버 출처 설정과 새 기본값이 충돌합니다. 운영 파일을 보존했습니다.' >&2
    exit 1
fi
"$app/.venv/bin/python" -c 'import sys,tomllib; tomllib.load(open(sys.argv[1],"rb"))' "$tmpdir/merged"
sudo install -m 644 newsbot/*.py "$app/newsbot/"
sudo install -m 644 "$tmpdir/merged" "$app/sources.toml"
sudo install -d -m 755 /etc/systemd/system/newsbot.service.d
printf '[Service]\nExecStart=\nExecStart=/opt/newsbot/.venv/bin/python -m newsbot run-telegram\n' > "$tmpdir/telegram.conf"
sudo install -m 644 "$tmpdir/telegram.conf" /etc/systemd/system/newsbot.service.d/telegram.conf
sudo systemctl daemon-reload
sudo systemctl restart newsbot
systemctl is-active newsbot
