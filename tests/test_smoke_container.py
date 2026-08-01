# Basic container sanity tests

from tests._docker import run_container
from scrubexif.__about__ import __license__, __version__


def test_tmp_writable_in_container():
    cp = run_container(entrypoint="bash", args=["-c", "echo ok > /tmp/x && cat /tmp/x"])
    assert "ok" in cp.stdout.lower()


def test_exiftool_available():
    cp = run_container(entrypoint="exiftool", args=["-ver"])
    assert cp.returncode == 0


def test_jpegtran_available():
    cp = run_container(entrypoint="jpegtran", args=["-version"])
    assert cp.returncode == 0


def test_scrubexif_invokable():
    """The packaged CLI prints the exact version and license metadata."""
    cp = run_container(args=["--version"])
    assert cp.returncode == 0
    assert cp.stdout == f"scrubexif {__version__}\n{__license__}\n"
    assert cp.stderr == ""


def test_stability_env_override_prints():
    cp = run_container(envs={"SCRUBEXIF_STABLE_SECONDS": "0"}, args=["--version"])
    joined = (cp.stdout + cp.stderr).lower()
    assert "scrubexif" in joined
