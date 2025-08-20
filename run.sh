#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN=${PYTHON_BIN:-python3}
USE_SYSTEM_PY=0

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
	echo "python3 not found. Please install Python 3 and retry."
	exit 1
fi

# Try to create venv; if not possible, fall back to system Python
if [ ! -d .venv ]; then
	set +e
	"$PYTHON_BIN" -m venv .venv
	venv_status=$?
	set -e
	if [ $venv_status -ne 0 ] || [ ! -x .venv/bin/python ]; then
		echo "Creating venv failed. Falling back to system Python (no apt permissions)."
		USE_SYSTEM_PY=1
	fi
fi

# Ensure pip exists inside venv if we will use it
if [ "$USE_SYSTEM_PY" -eq 0 ] && [ ! -x .venv/bin/pip ]; then
	if [ -x .venv/bin/python ]; then
		set +e
		.venv/bin/python -m ensurepip --upgrade || true
		set -e
		.venv/bin/python -m pip install -U pip setuptools wheel
	else
		echo "venv python not found after creation. Falling back to system Python."
		USE_SYSTEM_PY=1
	fi
fi

# Install requirements
if [ "$USE_SYSTEM_PY" -eq 0 ]; then
	.venv/bin/python -m pip install -r requirements.txt --upgrade
else
	"$PYTHON_BIN" -m pip install --user -r requirements.txt --upgrade || true
fi

# Ensure .env exists
if [ -f .env ]; then
	echo "Loaded .env"
else
	if [ -f .env.example ]; then
		echo "Creating .env from .env.example"
		cp .env.example .env
	fi
fi

# export variables from .env to environment
set -a
. ./.env
set +a

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
	echo "Please edit .env and set TELEGRAM_BOT_TOKEN before running."
	exit 1
fi

export PYTHONPATH=$(pwd)
echo "Starting Telegram bot..."
if [ "$USE_SYSTEM_PY" -eq 0 ]; then
	exec ./.venv/bin/python -m bot.main
else
	exec "$PYTHON_BIN" -m bot.main
fi

#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN=${PYTHON_BIN:-python3}
USE_SYSTEM_PY=0

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
	echo "python3 not found. Please install Python 3 and retry."
	exit 1
fi

# Try to create venv; if not possible, fall back to system Python
if [ ! -d .venv ]; then
	set +e
	"$PYTHON_BIN" -m venv .venv
	venv_status=$?
	set -e
	if [ $venv_status -ne 0 ] || [ ! -x .venv/bin/python ]; then
		echo "Creating venv failed. Falling back to system Python (no apt permissions)."
		USE_SYSTEM_PY=1
	fi
fi

# Ensure pip exists inside venv if we will use it
if [ "$USE_SYSTEM_PY" -eq 0 ] && [ ! -x .venv/bin/pip ]; then
	if [ -x .venv/bin/python ]; then
		set +e
		.venv/bin/python -m ensurepip --upgrade || true
		set -e
		.venv/bin/python -m pip install -U pip setuptools wheel
	else
		echo "venv python not found after creation. Falling back to system Python."
		USE_SYSTEM_PY=1
	fi
fi

# Install requirements
if [ "$USE_SYSTEM_PY" -eq 0 ]; then
	.venv/bin/python -m pip install -r requirements.txt --upgrade
else
	"$PYTHON_BIN" -m pip install --user -r requirements.txt --upgrade || true
fi

# Ensure .env exists
if [ -f .env ]; then
	echo "Loaded .env"
else
	if [ -f .env.example ]; then
		echo "Creating .env from .env.example"
		cp .env.example .env
	fi
fi

# export variables from .env to environment
set -a
. ./.env
set +a

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
	echo "Please edit .env and set TELEGRAM_BOT_TOKEN before running."
	exit 1
fi

export PYTHONPATH=$(pwd)
echo "Starting Telegram bot..."
if [ "$USE_SYSTEM_PY" -eq 0 ]; then
	exec ./.venv/bin/python -m bot.main
else
	exec "$PYTHON_BIN" -m bot.main
fi

