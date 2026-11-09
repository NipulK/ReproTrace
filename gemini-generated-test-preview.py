# AI-generated draft. Not executed or verified. Review before running.
import click
from click.testing import CliRunner

def test_empty_string_default_show_default():
    @click.command()
    @click.option("--test_value", type=str, default="", show_default=True)
    def test_function(test_value: str) -> None:
        pass

    runner = CliRunner()
    result = runner.invoke(test_function, ["--help"])
    assert result.exit_code == 0
    assert 'default: ""' in result.output