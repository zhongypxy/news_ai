from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_schedules_secrets_timeouts_and_success_only_commit():
    workflow = yaml.load((ROOT/".github/workflows/daily.yml").read_text("utf-8"), Loader=yaml.BaseLoader)
    assert workflow["on"]["schedule"] == [
        {"cron": "0 9 * * *", "timezone": "Asia/Singapore"},
        {"cron": "17 10 * * *", "timezone": "Asia/Singapore"}]
    assert workflow["jobs"]["daily"]["timeout-minutes"] == "40"
    steps = workflow["jobs"]["daily"]["steps"]
    generate = next(s for s in steps if s.get("name") == "Generate verified daily report")
    assert generate["env"]["OPENAI_API_KEY"] == "${{ secrets.OPENAI_API_KEY }}"
    commit = next(s for s in steps if s.get("name") == "Commit successful reports")
    assert "if" not in commit and "git push" in commit["run"]
    artifact = steps[-1]
    assert artifact["if"] == "always()" and "logs/" in artifact["with"]["path"]
    assert "cancel-in-progress" in workflow["concurrency"]


def test_windows_sync_uses_fast_forward_and_checks_local_changes():
    script = (ROOT/"scripts/sync-windows.ps1").read_text("utf-8")
    assert "--ff-only" in script and "status --porcelain" in script
    assert "reset --hard" not in script and "clean -f" not in script
    install = (ROOT/"scripts/install-sync-task.ps1").read_text("utf-8")
    assert "-At '10:00'" in install and "-At '11:00'" in install
    assert "-WindowStyle Hidden" in install and "-AtLogOn" in install
