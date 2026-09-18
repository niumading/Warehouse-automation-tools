"""Create a PostgreSQL custom-format backup without printing connection secrets."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings
from sqlalchemy.engine import make_url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", help="Existing backup directory within the project")
    parser.add_argument("--pg-dump", default="pg_dump", help="Path to an existing pg_dump executable")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    destination = Path(args.destination).resolve()
    if not destination.is_relative_to(project / "backups") or not destination.is_dir():
        raise SystemExit("Destination must be an existing project backups directory")
    output = destination / "database.dump"
    if output.exists():
        raise SystemExit("Refusing to overwrite an existing database backup")
    settings = get_settings()
    url = make_url(settings.database_admin_url or settings.database_url)
    env = os.environ.copy()
    env["PGPASSWORD"] = url.password or ""
    subprocess.run([
        args.pg_dump,
        "-h", url.host or "localhost", "-p", str(url.port or 5432),
        "-U", url.username or "postgres", "-d", url.database,
        "-Fc", "-f", str(output),
    ], env=env, check=True)
    print(f"Database backup created: {output.name} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
