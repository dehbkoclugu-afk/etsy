from __future__ import annotations

from pinforge.cli import parser


def test_auto_run_cli_is_one_command_and_can_be_headless() -> None:
    args = parser().parse_args(
        [
            "auto-run",
            "--board",
            "New products",
            "--headless",
        ]
    )

    assert args.command == "auto-run"
    assert args.board == "New products"
    assert args.limit == 1
    assert args.headless is True
