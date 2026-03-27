"""Tests for state_persistence module."""

import json

import pytest

from state_persistence import load_state, save_state


@pytest.fixture
def state_file(tmp_path):
    """Return a path to a temporary state file."""
    return str(tmp_path / ".rtk_state.json")


@pytest.fixture
def sample_snapshot():
    """Return a sample state snapshot similar to GnssState.get_state_snapshot()."""
    return {
        "position": {"lat": 48.1234, "lon": 11.5678, "alt": 520.0},
        "fix_type": 4,
        "rtk_status": "RTK Fix",
        "num_satellites_used": 18,
        "hdop": 0.8,
        "firmware_version": "1.2.3",
        "module_name": "LC29H",
        "ntrip_total_bytes": 123456,
    }


class TestSaveState:
    def test_writes_valid_json(self, state_file, sample_snapshot):
        result = save_state(sample_snapshot, filename=state_file)
        assert result is True
        with open(state_file) as f:
            data = json.load(f)
        assert "saved_at" in data
        assert data["position"]["lat"] == 48.1234

    def test_saves_all_expected_fields(self, state_file, sample_snapshot):
        save_state(sample_snapshot, filename=state_file)
        with open(state_file) as f:
            data = json.load(f)
        expected_keys = {
            "saved_at",
            "position",
            "fix_type",
            "rtk_status",
            "num_satellites_used",
            "hdop",
            "firmware_version",
            "module_name",
            "ntrip_total_bytes",
        }
        assert set(data.keys()) == expected_keys

    def test_handles_empty_snapshot(self, state_file):
        result = save_state({}, filename=state_file)
        assert result is True
        with open(state_file) as f:
            data = json.load(f)
        assert data["position"] is None
        assert data["fix_type"] == 0

    def test_returns_false_on_write_error(self):
        result = save_state({}, filename="/nonexistent/dir/state.json")
        assert result is False


class TestLoadState:
    def test_returns_none_for_missing_file(self, tmp_path):
        result = load_state(filename=str(tmp_path / "does_not_exist.json"))
        assert result is None

    def test_returns_none_for_corrupt_json(self, state_file):
        with open(state_file, "w") as f:
            f.write("{invalid json!!!")
        result = load_state(filename=state_file)
        assert result is None

    def test_loads_valid_json(self, state_file, sample_snapshot):
        save_state(sample_snapshot, filename=state_file)
        data = load_state(filename=state_file)
        assert data is not None
        assert data["rtk_status"] == "RTK Fix"
        assert data["num_satellites_used"] == 18


class TestRoundTrip:
    def test_save_then_load_preserves_position(self, state_file, sample_snapshot):
        save_state(sample_snapshot, filename=state_file)
        loaded = load_state(filename=state_file)
        assert loaded is not None
        assert loaded["position"] == sample_snapshot["position"]
        assert loaded["fix_type"] == sample_snapshot["fix_type"]
        assert loaded["hdop"] == sample_snapshot["hdop"]
        assert loaded["ntrip_total_bytes"] == sample_snapshot["ntrip_total_bytes"]

    def test_save_then_load_preserves_all_fields(self, state_file, sample_snapshot):
        save_state(sample_snapshot, filename=state_file)
        loaded = load_state(filename=state_file)
        assert loaded is not None
        for key in ("fix_type", "rtk_status", "num_satellites_used", "hdop",
                     "firmware_version", "module_name", "ntrip_total_bytes"):
            assert loaded[key] == sample_snapshot[key], f"Mismatch for {key}"
