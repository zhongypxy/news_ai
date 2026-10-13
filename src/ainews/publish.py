import copy
import hashlib
import json
import os
from pathlib import Path

from .models import Report
from .quality import QualityError


def digest(content):
    return hashlib.sha256(content).hexdigest()


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


class RepositoryLock:
    """OS locks are released after process death, avoiding stale PID lock files."""
    def __init__(self, root):
        self.path = Path(root) / "state/.lock"
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                if not self.handle.read(1):
                    self.handle.write(b"0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise QualityError("Another report writer is already running") from None
        return self

    def __exit__(self, *args):
        if os.name == "nt":
            import msvcrt
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


class Publisher:
    def __init__(self, root, renderer):
        self.root, self.renderer = Path(root), renderer
        self.state_path = self.root / "state/events.json"
        self.pending_path = self.root / "state/.pending.json"

    def load_state(self):
        if not self.state_path.exists():
            return {"version": 1, "events": {}, "reports": {}}
        try:
            state = json.loads(self.state_path.read_text("utf-8"))
            if state.get("version") != 1 or not isinstance(state["events"], dict) or not isinstance(state["reports"], dict):
                raise ValueError()
            return state
        except (ValueError, KeyError, TypeError):
            raise QualityError("Event state is invalid; restore from Git history") from None

    def completed(self, day):
        state = self.load_state()
        record = state["reports"].get(day)
        if not record:
            if (self.root / f"reports/{day}.html").exists() or (self.root / f"data/{day}.json").exists():
                raise QualityError("Unregistered historical files; recover pending transaction first")
            return False
        for relative, expected in record["sha256"].items():
            path = self._path(relative)
            if not path.exists() or digest(path.read_bytes()) != expected:
                raise QualityError("Report archive integrity check failed; no overwrite allowed")
        try:
            Report.model_validate_json((self.root / f"data/{day}.json").read_bytes())
        except ValueError:
            raise QualityError("Archived JSON failed schema validation") from None
        return True

    def _path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise QualityError("Invalid publication path")
        if relative not in {"state/events.json", "index.html"} and not (
            relative.startswith("reports/") and relative.endswith(".html") or
            relative.startswith("data/") and relative.endswith(".json")):
            raise QualityError("Unexpected publication path")
        return path

    def publish(self, report, state, checkpoint=lambda stage: None):
        day = report.report_date.isoformat()
        if self.completed(day):
            raise QualityError("Historical report already published; overwrite forbidden")
        Report.model_validate_json(report.model_dump_json())
        updated = copy.deepcopy(state)
        daily = {f"reports/{day}.html": self.renderer.report(report),
                 f"data/{day}.json": report.model_dump_json(indent=2)}
        updated["reports"][day] = {"window_start": report.window_start.isoformat(), "window_end": report.window_end.isoformat(),
                                   "count": len(report.all_items), "summary": report.executive_summary,
                                   "sha256": {k: digest(v.encode("utf-8")) for k, v in daily.items()}}
        files = daily | {"state/events.json": json.dumps(updated, ensure_ascii=False, indent=2),
                         "index.html": self.renderer.index(updated["reports"])}
        atomic_write(self.pending_path, json.dumps({"day": day, "files": files}, ensure_ascii=False).encode("utf-8"))
        self._finish(files, checkpoint)

    def _finish(self, files, checkpoint=lambda stage: None):
        for relative, content in files.items():
            path, payload = self._path(relative), content.encode("utf-8")
            if relative.startswith(("reports/", "data/")):
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    if path.read_bytes() != payload:
                        raise QualityError("Existing history conflicts with recovery transaction")
                else:
                    # Exclusive creation prevents accidental overwrite, including concurrent writes.
                    with path.open("xb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
            else:
                atomic_write(path, payload)
            if relative.startswith("data/"):
                checkpoint("daily_written")
        self.pending_path.unlink()

    def recover(self):
        if self.pending_path.exists():
            try:
                transaction = json.loads(self.pending_path.read_text("utf-8"))
                files = transaction["files"]
                day = transaction["day"]
                required = {f"reports/{day}.html", f"data/{day}.json", "state/events.json", "index.html"}
                if set(files) != required:
                    raise ValueError()
                report = Report.model_validate_json(files[f"data/{day}.json"])
                if report.report_date.isoformat() != day:
                    raise ValueError()
                state = json.loads(files["state/events.json"])
                for relative in [f"reports/{day}.html", f"data/{day}.json"]:
                    if digest(files[relative].encode("utf-8")) != state["reports"][day]["sha256"][relative]:
                        raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise QualityError("Pending publication is damaged; restore manually") from None
            self._finish(files)
