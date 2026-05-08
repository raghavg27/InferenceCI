from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from inferenceci import __version__
from inferenceci.config_loader import ConfigError, load_config
from inferenceci.pricing import load_pricing_table
from inferenceci.runner import RunnerError, run_all

_console_err = Console(stderr=True)
_console = Console()


def _print_error(msg: str) -> None:
    _console_err.print(f"[bold red]error:[/bold red] {msg}")


def _exit(code: int) -> click._exit:  # type: ignore
    sys.exit(code)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="costdiff")
def main() -> None:
    """costdiff — replay LLM scenarios in CI, diff cost on PRs."""


# ----- init ----------------------------------------------------------------

_TEMPLATE_DIR = Path(__file__).parent / "data"


@main.command()
@click.option("--force", is_flag=True, help="Overwrite existing files.")
@click.option(
    "--dir",
    "target_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Directory to scaffold into.",
)
def init(force: bool, target_dir: Path) -> None:
    """Scaffold costdiff.yaml + scenarios/ in the current directory."""
    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    files = {
        target_dir / "costdiff.yaml": _TEMPLATE_DIR / "costdiff.template.yaml",
        target_dir / "scenarios" / "example_openai.py": _TEMPLATE_DIR / "scenario.template.py",
    }
    refused: list[Path] = []
    for dst in files:
        if dst.exists() and not force:
            refused.append(dst)
    if refused:
        for r in refused:
            _print_error(f"refusing to overwrite {r} (use --force)")
        sys.exit(2)

    for dst, src in files.items():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        _console.print(f"  [green]+[/green] {dst.relative_to(target_dir)}")
    _console.print(
        "\n[bold]next:[/bold] edit costdiff.yaml, set OPENAI_API_KEY, "
        "then run [cyan]costdiff run[/cyan]."
    )


# ----- run -----------------------------------------------------------------

@main.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("costdiff.yaml"),
    show_default=True,
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("report.json"),
    show_default=True,
)
@click.option(
    "--scenario",
    "scenario_name",
    type=str,
    default=None,
    help="Run only this scenario.",
)
def run(config_path: Path, output_path: Path, scenario_name: str | None) -> None:
    """Execute scenarios per config and write a report JSON."""
    try:
        config = load_config(config_path)
    except ConfigError as e:
        _print_error(str(e))
        sys.exit(2)

    try:
        report = run_all(config, config_path, only_scenario=scenario_name)
    except RunnerError as e:
        _print_error(str(e))
        sys.exit(2)

    output_path.write_text(report.model_dump_json(indent=2))
    _print_run_summary(report)
    _console.print(f"\n[green]wrote[/green] {output_path}")
    if any(s.errors for s in report.scenarios):
        sys.exit(1)


def _print_run_summary(report) -> None:
    table = Table(title="Run summary", show_lines=False)
    table.add_column("scenario", style="cyan")
    table.add_column("runs", justify="right")
    table.add_column("errors", justify="right")
    table.add_column("calls (med)", justify="right")
    table.add_column("input tok (med)", justify="right")
    table.add_column("output tok (med)", justify="right")
    table.add_column("cost USD (med)", justify="right")
    table.add_column("latency ms (med)", justify="right")
    for s in report.scenarios:
        m = s.metrics
        table.add_row(
            s.name,
            str(s.runs),
            str(s.errors) if s.errors == 0 else f"[red]{s.errors}[/red]",
            f"{m.calls.median:.0f}",
            f"{m.input_tokens.median:.0f}",
            f"{m.output_tokens.median:.0f}",
            f"{m.cost_usd.median:.4f}",
            f"{m.latency_ms.median:.0f}",
        )
    t = report.totals
    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        "",
        "",
        f"{t.calls.median:.0f}",
        f"{t.input_tokens.median:.0f}",
        f"{t.output_tokens.median:.0f}",
        f"[bold]{t.cost_usd.median:.4f}[/bold]",
        f"{t.latency_ms.median:.0f}",
    )
    _console.print(table)
    for w in report.warnings:
        _console.print(f"[yellow]warning:[/yellow] {w}")


# ----- pricing -------------------------------------------------------------

@main.group()
def pricing() -> None:
    """Pricing utilities."""


@pricing.command("list")
@click.option(
    "--pricing-file",
    "pricing_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Override the bundled pricing table.",
)
def pricing_list(pricing_path: Path | None) -> None:
    """Print the loaded pricing table."""
    try:
        table, used = load_pricing_table(pricing_path)
    except ConfigError as e:
        _print_error(str(e))
        sys.exit(2)

    _console.print(f"[dim]source:[/dim] {used}  [dim]last_updated:[/dim] {table.last_updated}")
    for prov, models in sorted(table.providers.items()):
        t = Table(title=prov, show_lines=False)
        t.add_column("model", style="cyan")
        t.add_column("input/1M", justify="right")
        t.add_column("output/1M", justify="right")
        t.add_column("cached_input/1M", justify="right")
        t.add_column("cache_write/1M", justify="right")
        t.add_column("cache_read/1M", justify="right")
        for name, p in sorted(models.items()):
            t.add_row(
                name,
                f"${p.input_per_1m:.2f}",
                f"${p.output_per_1m:.2f}",
                f"${p.cached_input_per_1m:.2f}" if p.cached_input_per_1m is not None else "-",
                f"${p.cache_write_per_1m:.2f}" if p.cache_write_per_1m is not None else "-",
                f"${p.cache_read_per_1m:.2f}" if p.cache_read_per_1m is not None else "-",
            )
        _console.print(t)


# ----- version (also exposed via --version) --------------------------------

@main.command()
def version() -> None:
    """Print the costdiff version."""
    click.echo(__version__)


# ----- compare (placeholder filled in step 8) ------------------------------

@main.command()
@click.argument(
    "baseline_json", type=click.Path(dir_okay=False, exists=True, path_type=Path)
)
@click.argument(
    "head_json", type=click.Path(dir_okay=False, exists=True, path_type=Path)
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("costdiff.yaml"),
    show_default=True,
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json", "markdown"]),
    default="text",
    show_default=True,
)
def compare(baseline_json: Path, head_json: Path, config_path: Path, fmt: str) -> None:
    """Diff two reports. Exit 0 within thresholds, 1 exceeded, 2 input error."""
    from inferenceci.diff import (
        DiffInputError,
        load_report,
        render_markdown,
        render_text,
        run_diff,
    )

    try:
        config = load_config(config_path)
    except ConfigError as e:
        _print_error(str(e))
        sys.exit(2)

    try:
        baseline = load_report(baseline_json)
        head = load_report(head_json)
    except DiffInputError as e:
        _print_error(str(e))
        sys.exit(2)

    diff = run_diff(baseline, head, config.thresholds)

    if fmt == "json":
        click.echo(json.dumps(diff.to_dict(), indent=2))
    elif fmt == "markdown":
        click.echo(render_markdown(diff))
    else:
        render_text(diff, _console)

    sys.exit(1 if diff.failed else 0)


if __name__ == "__main__":
    main()
