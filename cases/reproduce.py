"""Run one manually curated regression assertion. No network or server required."""
import sys
from importlib.metadata import version


def multidict_none():
    from werkzeug.datastructures import MultiDict
    value = MultiDict([("quantity", None)]).get("quantity", default=7, type=int)
    print("Observed:", repr(value), flush=True)
    assert value == 7


def double_slash():
    from werkzeug.routing import Map, Rule
    from werkzeug.exceptions import HTTPException
    urls = Map()
    urls.merge_slashes = False
    urls.add(Rule("/api/path", endpoint="example"))
    adapter = urls.bind("example.test")
    try:
        result = adapter.match("/api//path")
        code = 200
        print("Matched:", result, flush=True)
    except HTTPException as exc:
        code = exc.code
    print("Observed status:", code, flush=True)
    assert code == 404


def cache_header():
    from werkzeug.http import parse_cache_control_header
    value = parse_cache_control_header("max-age=invalid").max_age
    print("Observed:", repr(value), flush=True)
    assert value is None


def empty_default():
    import click
    from click.testing import CliRunner
    @click.command()
    @click.option("--name", default="", show_default=True)
    def command(name):
        pass
    result = CliRunner().invoke(command, ["--help"])
    print(result.output, flush=True)
    assert result.exit_code == 0
    assert '[default: ""]' in result.output


def reused_decorator():
    import click
    class CustomArgument(click.Argument):
        def __init__(self, *args, help=None, **kwargs):
            super().__init__(*args, **kwargs)
            self.help = help
    shared = click.argument("value", cls=CustomArgument, help="Example argument")
    @click.command()
    @shared
    def first(value):
        pass
    @click.command()
    @shared
    def second(value):
        pass
    print("Observed:", type(first.params[0]).__name__, type(second.params[0]).__name__, flush=True)
    assert isinstance(first.params[0], CustomArgument)
    assert isinstance(second.params[0], CustomArgument)


CASES = {
    "werkzeug-2842": ("Werkzeug", multidict_none),
    "werkzeug-2834": ("Werkzeug", double_slash),
    "werkzeug-2980": ("Werkzeug", cache_header),
    "click-2500": ("click", empty_default),
    "click-2294": ("click", reused_decorator),
}

if __name__ == "__main__":
    package, reproduce = CASES[sys.argv[1]]
    print("Package:", package, "Version:", version(package), flush=True)
    reproduce()
    print("PASS: corrected behavior observed", flush=True)
