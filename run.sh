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