import argparse
import csv
import re
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text


_SERVER_KEY_PATTERN = re.compile(r"[^a-z0-9]+")


def normalize_mt5_server_key(value: str) -> str:
    return _SERVER_KEY_PATTERN.sub("", value.strip().lower())


def load_server_names(csv_path: Path) -> list[str]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if "ServerName" not in (reader.fieldnames or []):
            raise ValueError("CSV must contain a ServerName column")

        seen: set[str] = set()
        names: list[str] = []
        for row in reader:
            name = (row.get("ServerName") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names


def seed(database_url: str, csv_path: Path) -> int:
    server_names = load_server_names(csv_path)
    engine = create_engine(database_url, pool_pre_ping=True)

    statement = text(
        """
        INSERT INTO mt5_server_catalog (
            id,
            canonical_server_name,
            normalized_server_key,
            source,
            active,
            created_at,
            updated_at
        )
        VALUES (
            :id,
            :canonical_server_name,
            :normalized_server_key,
            'csv',
            true,
            now(),
            now()
        )
        ON CONFLICT (canonical_server_name) DO UPDATE
        SET
            normalized_server_key = EXCLUDED.normalized_server_key,
            source = EXCLUDED.source,
            active = true,
            updated_at = now()
        """
    )

    rows = [
        {
            "id": str(uuid.uuid4()),
            "canonical_server_name": name,
            "normalized_server_key": normalize_mt5_server_key(name),
        }
        for name in server_names
    ]

    with engine.begin() as connection:
        connection.execute(statement, rows)

    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed MT5 server catalog from a CSV export.")
    parser.add_argument("--database-url")
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        server_names = load_server_names(args.csv)
        print(f"Loaded {len(server_names)} MT5 server catalog row(s).")
        return

    if not args.database_url:
        parser.error("--database-url is required unless --dry-run is set")

    inserted_or_updated = seed(args.database_url, args.csv)
    print(f"Seeded {inserted_or_updated} MT5 server catalog row(s).")


if __name__ == "__main__":
    main()
