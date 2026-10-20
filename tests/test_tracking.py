import pytest

from src.models import tracking


def test_runs_go_to_the_store_in_mlflow_dir(tmp_path, monkeypatch, fresh_settings):
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "")  # restored afterwards; mlflow sets it
    monkeypatch.setenv("MLFLOW_DIR", str(tmp_path / "mlruns"))
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "results.md").write_text("# ML results\n")
    with tracking.run("test") as tracker:
        tracker.params({"quick": True})
        tracker.metrics({"pr_auc": 0.5, "note": "skipped"}, prefix="lgbm.")
        tracker.artifacts(reports, "reports")
        run_id = mlflow.active_run().info.run_id
    run = mlflow.get_run(run_id)
    assert run.data.params == {"quick": "True"}
    assert run.data.metrics == {"lgbm.pr_auc": 0.5}
    assert (tmp_path / "mlruns" / "mlflow.db").exists()
    assert list((tmp_path / "mlruns" / "artifacts").rglob("reports/results.md"))


def test_tracking_is_a_no_op_when_disabled(tmp_path):
    with tracking.run("off", enabled=False) as tracker:
        tracker.params({"a": 1})
        tracker.metrics({"b": 2.0})
        tracker.artifacts(tmp_path, "reports")
