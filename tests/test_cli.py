from test_pipeline import project

from ainews.cli import main


def test_cli_config_and_schema_commands_need_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    root = project(tmp_path)
    assert main(["--root", str(root), "check"]) == 0
    assert main(["--root", str(root), "schema"]) == 0
    assert (root/"index.html").exists() and (root/"config/report.schema.json").exists()


def test_cli_missing_key_returns_nonzero(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    root = project(tmp_path)
    assert main(["--root", str(root), "run"]) == 1
    assert not list((root/"reports").glob("*.html"))
