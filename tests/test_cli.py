import pytest

from simgeope.cli import main


@pytest.mark.parametrize(
    "arguments", [["--help"], ["world", "--help"], ["world", "build", "--help"]]
)
def test_cli_help_without_build_imports(arguments, capsys):
    with pytest.raises(SystemExit) as exit_result:
        main(arguments)
    assert exit_result.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_cli_requires_subcommand():
    with pytest.raises(SystemExit) as exit_result:
        main([])
    assert exit_result.value.code == 2
