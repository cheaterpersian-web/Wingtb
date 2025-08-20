#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN=${PYTHON_BIN:-python3}

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
	echo "python3 not found. Please install Python 3 and retry."
	exit 1
fi

if [ ! -d .venv ]; then
	set +e
	"$PYTHON_BIN" -m venv .venv
	venv_status=$?
	set -e
	if [ $venv_status -ne 0 ] || [ ! -x .venv/bin/python ]; then
		echo "Creating venv failed. Trying to install python3-venv (apt)."
		if command -v apt-get >/dev/null 2>&1; then
			apt-get update -y
			apt-get install -y python3-venv
			"$PYTHON_BIN" -m venv .venv
		else
			echo "Could not create venv automatically. Please install the Python venv package for your OS and rerun."
			exit 1
		fi
	fi
fi

# Ensure pip exists inside venv
if [ ! -x .venv/bin/pip ]; then
	if [ -x .venv/bin/python ]; then
		set +e
		.venv/bin/python -m ensurepip --upgrade
		set -e
		.venv/bin/python -m pip install -U pip setuptools wheel
	else
		echo "venv python not found after creation. Please remove .venv and rerun."
		exit 1
	fi
fi

.venv/bin/python -m pip install -r requirements.txt --upgrade

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

