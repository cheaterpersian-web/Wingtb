#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
	python3 -m venv .venv
	./.venv/bin/pip install -U pip setuptools wheel
fi

./.venv/bin/pip install -r requirements.txt

if [ -f .env ]; then
	echo "Loaded .env"
else
	if [ -f .env.example ]; then
		echo "Creating .env from .env.example"
		cp .env.example .env
	fi
fi

if ! grep -q "^TELEGRAM_BOT_TOKEN=" .env; then
	echo "Please edit .env and set TELEGRAM_BOT_TOKEN before running."
	exit 1
fi

export PYTHONPATH=$(pwd)
exec ./.venv/bin/python -m bot.main

