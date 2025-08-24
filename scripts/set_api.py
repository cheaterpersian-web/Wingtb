from __future__ import annotations

import asyncio
import sys

from app.core.storage.db import SQLiteRepo


async def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python -m scripts.set_api ACCESS_ID SECRET_KEY")
        sys.exit(1)
    access, secret = sys.argv[1], sys.argv[2]
    repo = SQLiteRepo()
    repo.connect()
    await repo.update_settings({"coinex_api_key": access.strip(), "coinex_api_secret": secret.strip()})
    print("Saved API to SQLite")


if __name__ == "__main__":
    asyncio.run(main())