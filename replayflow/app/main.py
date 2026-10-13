"""Ponto de entrada. Etapa 1: CLI mínima de criação de sessão (a UI PyQt6 entra na etapa 3)."""

from __future__ import annotations

import argparse
from pathlib import Path

from replayflow.core import network_guard, session_store
from replayflow.models import AppConfig


def main(argv: list[str] | None = None) -> None:
    config = AppConfig()
    parser = argparse.ArgumentParser(prog="replayflow")
    sub = parser.add_subparsers(dest="cmd", required=True)
    new = sub.add_parser("new", help="cria uma sessão")
    new.add_argument("name")
    new.add_argument("--base-dir", type=Path, default=config.base_dir)
    new.add_argument("--video", type=Path)
    args = parser.parse_args(argv)

    network_guard.apply(config.private_mode)
    session = session_store.create_session(args.base_dir, args.name, config.pipeline)
    if args.video:
        session = session_store.import_video(session, args.video)
    print(session.root)


if __name__ == "__main__":
    main()
