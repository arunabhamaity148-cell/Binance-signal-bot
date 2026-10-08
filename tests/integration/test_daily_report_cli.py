from scripts.daily_report import main


def test_daily_report_cli_dry_run_prints_but_does_not_send(tmp_path, capsys):
    result = main(["--dry-run", "--db", str(tmp_path / "cli.db")])
    output = capsys.readouterr()
    assert result == 0
    assert "📊 DAILY REPORT —" in output.out
    assert "No outcomes recorded yet." in output.out
    assert "DRY RUN — Telegram delivery was not attempted." in output.out
    assert output.err == ""
