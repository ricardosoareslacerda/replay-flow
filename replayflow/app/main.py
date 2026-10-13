"""Ponto de entrada. CLI enquanto a UI PyQt6 (etapa 3) não chega."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from replayflow.core import network_guard, session_store
from replayflow.core.errors import ReplayFlowError
from replayflow.core.pipeline import Pipeline
from replayflow.models import AppConfig
from replayflow.services import transcriber


def main(argv: list[str] | None = None) -> None:
    config = AppConfig()
    parser = argparse.ArgumentParser(prog="replayflow")
    sub = parser.add_subparsers(dest="cmd", required=True)
    new = sub.add_parser("new", help="cria uma sessão")
    new.add_argument("name")
    new.add_argument("--base-dir", type=Path, default=config.base_dir)
    new.add_argument("--video", type=Path)
    proc = sub.add_parser("process", help="processa (ou retoma) uma sessão")
    proc.add_argument("session_dir", type=Path)
    dl = sub.add_parser("download-model", help="baixa um modelo whisper (única ação com rede)")
    dl.add_argument("model", choices=["tiny", "base", "small", "medium", "large-v3"])
    args = parser.parse_args(argv)

    network_guard.apply(config.private_mode and args.cmd != "download-model")
    try:
        if args.cmd == "new":
            session = session_store.create_session(args.base_dir, args.name, config.pipeline)
            if args.video:
                session = session_store.import_video(session, args.video)
            print(session.root)
        elif args.cmd == "process":
            Pipeline(session_store.load(args.session_dir), config.private_mode,
                     progress=lambda stage, status: print(f"[{stage}] {status or 'iniciando'}")).run()
        else:
            transcriber.download_model(args.model)
            print(f"Modelo {args.model} disponível no cache local.")
    except ReplayFlowError as e:
        sys.exit(f"Erro: {e}")


if __name__ == "__main__":
    main()
