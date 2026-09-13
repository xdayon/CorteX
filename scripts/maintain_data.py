#!/usr/bin/env python3
"""Preview, back up, restore, and prune CorteX local data safely."""
from __future__ import annotations

import argparse
import json
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from cortex.config import load_config


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def backup(output: Path, *, include_cache: bool) -> None:
    config = load_config()
    data_dir = config.paths.data_dir.resolve()
    database = config.paths.database.resolve()
    output = output.resolve()
    if _inside(output, data_dir):
        raise SystemExit("O backup deve ficar fora de data/ para não incluir a si próprio")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cortex-backup-") as temporary:
        staging = Path(temporary)
        sqlite_copy = staging / "cortex.sqlite3"
        with sqlite3.connect(database) as source, sqlite3.connect(sqlite_copy) as target:
            source.backup(target)
        manifest = {
            "schema_version": 1,
            "created_at_unix": time.time(),
            "source_data_dir": str(data_dir),
            "includes_cache": include_cache,
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        temporary_archive = output.with_suffix(output.suffix + ".tmp")
        with tarfile.open(temporary_archive, "w:gz") as archive:
            archive.add(sqlite_copy, arcname="cortex.sqlite3")
            archive.add(staging / "manifest.json", arcname="manifest.json")
            for path in sorted(data_dir.rglob("*")):
                if not path.is_file() or path.resolve() == database:
                    continue
                if not include_cache and _inside(path, config.paths.cache_dir):
                    continue
                archive.add(path, arcname=str(path.relative_to(data_dir)))
        temporary_archive.replace(output)
    print(f"Backup criado: {output}")


def restore(archive_path: Path, target: Path, *, apply: bool) -> None:
    archive_path = archive_path.resolve()
    target = target.resolve()
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        unsafe = [member.name for member in members if member.islnk() or member.issym()
                  or Path(member.name).is_absolute() or ".." in Path(member.name).parts]
        if unsafe:
            raise SystemExit(f"Backup contém caminhos inseguros: {unsafe[:3]}")
        print(f"Restauração: {len(members)} entradas em {target}")
        if not apply:
            print("Prévia somente; execute novamente com --apply para restaurar")
            return
        if target.exists() and any(target.iterdir()):
            raise SystemExit("O diretório de destino deve estar vazio")
        target.mkdir(parents=True, exist_ok=True)
        archive.extractall(target, filter="data")


def prune_cache(max_age_days: int, *, apply: bool) -> None:
    if max_age_days < 1:
        raise SystemExit("--max-age-days deve ser maior que zero")
    cache_dir = load_config().paths.cache_dir.resolve()
    cutoff = time.time() - max_age_days * 86400
    candidates = [path for path in cache_dir.rglob("*") if path.is_file()
                  and path.stat().st_mtime < cutoff and _inside(path, cache_dir)]
    total = sum(path.stat().st_size for path in candidates)
    print(f"Cache elegível: {len(candidates)} arquivos, {total / 1024 / 1024:.1f} MiB")
    for path in candidates[:20]:
        print(path)
    if len(candidates) > 20:
        print(f"... e mais {len(candidates) - 20}")
    if not apply:
        print("Prévia somente; execute novamente com --apply para remover")
        return
    for path in candidates:
        path.unlink()
    for directory in sorted(cache_dir.rglob("*"), reverse=True):
        if directory.is_dir():
            try:
                directory.rmdir()
            except OSError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup_parser = commands.add_parser("backup")
    backup_parser.add_argument("--output", type=Path, required=True)
    backup_parser.add_argument("--include-cache", action="store_true")
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("--archive", type=Path, required=True)
    restore_parser.add_argument("--target", type=Path, required=True)
    restore_parser.add_argument("--apply", action="store_true")
    prune_parser = commands.add_parser("prune-cache")
    prune_parser.add_argument("--max-age-days", type=int, default=30)
    prune_parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "backup":
        backup(args.output, include_cache=args.include_cache)
    elif args.command == "restore":
        restore(args.archive, args.target, apply=args.apply)
    else:
        prune_cache(args.max_age_days, apply=args.apply)


if __name__ == "__main__":
    main()
