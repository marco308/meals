"""The ios-* recipes have to fail when Xcode does.

They pipe xcodebuild into grep and tail, and without pipefail a pipeline's
status is its last command's: `make ios-build` exited 0 on a failed build, and
`make ios-testflight` read "** ARCHIVE FAILED **" as a match and went on to
export and upload whatever archive the previous run had left behind.

There is no iOS job in CI, so these run the real recipes against stand-ins for
Xcode's tools, with whatever make this machine has. On a Mac that is GNU make
3.81, which ignores `.SHELLFLAGS` without a word, and a Mac is the only place
the ios-* targets ever run.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="needs make")

# Every call is appended to $CALLS, so a test can see how far a recipe got.
# xcodebuild fails like the real one, banner and all, unless told otherwise.
STAND_INS = {
    "xcodegen": 'echo "xcodegen $*" >> "$CALLS"\n',
    "xcodebuild": (
        'echo "xcodebuild $*" >> "$CALLS"\n'
        'case " $* " in\n'
        '  *" -exportArchive "*) action=EXPORT ;;\n'
        '  *" archive "*) action=ARCHIVE ;;\n'
        '  *" test "*) action=TEST ;;\n'
        "  *) action=BUILD ;;\n"
        "esac\n"
        'if [ "${XCODEBUILD_EXIT:-65}" = 0 ]; then echo "** $action SUCCEEDED **"; exit 0; fi\n'
        'echo "error: this is a stand-in for Xcode, and it failed"\n'
        'echo "** $action FAILED **"\n'
        'exit "${XCODEBUILD_EXIT:-65}"\n'
    ),
    "xcrun": 'echo "xcrun $*" >> "$CALLS"\n',
}


@pytest.fixture
def ios_dir(tmp_path):
    """Stands in for ios/Meals, so nothing is written into the checkout."""
    path = tmp_path / "ios"
    path.mkdir()
    return path


@pytest.fixture
def xcode(tmp_path, ios_dir):
    """Run a make target from the repo root with Xcode's tools stood in for.
    Returns the result and every stand-in call, in order."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STAND_INS.items():
        tool = bin_dir / name
        tool.write_text("#!/bin/sh\n" + body)
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    calls = tmp_path / "calls.log"

    def run(target: str, *, xcodebuild_exit: int = 65) -> tuple[subprocess.CompletedProcess, str]:
        # Stripped so that running under `make test` doesn't make this a
        # sub-make of it, jobserver and all.
        env = {k: v for k, v in os.environ.items() if k not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL"}}
        env.update(PATH=f"{bin_dir}{os.pathsep}{env['PATH']}", CALLS=str(calls), XCODEBUILD_EXIT=str(xcodebuild_exit))
        result = subprocess.run(
            [
                "make",
                "--no-print-directory",
                target,
                f"IOS_DIR={ios_dir}",
                # Command-line values beat an ios/.env, so a real one is never used.
                "MEALS_DEVELOPMENT_TEAM=TEAM000000",
                "ASC_KEY_ID=KEY0000000",
                "ASC_ISSUER=00000000-0000-0000-0000-000000000000",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result, calls.read_text() if calls.exists() else ""

    return run


@pytest.mark.parametrize("target", ["ios-build", "ios-test"])
def test_a_failed_xcodebuild_fails_the_target(xcode, target):
    result, calls = xcode(target)
    assert "xcodebuild" in calls, f"the recipe never reached xcodebuild:\n{result.stdout}{result.stderr}"
    assert "FAILED" in result.stdout
    assert result.returncode != 0, f"`make {target}` exited 0 on a failed xcodebuild"


def test_a_failed_archive_is_never_exported_or_uploaded(xcode, ios_dir):
    stale = ios_dir / "build"
    (stale / "Meals.xcarchive").mkdir(parents=True)
    (stale / "export").mkdir()
    (stale / "export" / "Meals.ipa").write_text("last week's build")

    result, calls = xcode("ios-testflight")

    assert "xcodebuild archive" in calls, f"the recipe never reached the archive:\n{result.stdout}{result.stderr}"
    assert "** ARCHIVE FAILED **" in result.stdout
    assert result.returncode != 0
    assert "-exportArchive" not in calls
    assert "xcrun" not in calls, "a failed archive went on to upload a build"
    # Nothing left over for a failure to upload, whatever stops it.
    assert not (stale / "Meals.xcarchive").exists()
    assert not (stale / "export").exists()


def test_the_stand_ins_do_reach_the_upload_when_xcode_succeeds(xcode):
    """The control: without it, the tests above would pass just as well if the
    recipe fell over before xcodebuild for some reason of its own."""
    result, calls = xcode("ios-testflight", xcodebuild_exit=0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "xcrun altool --upload-app" in calls
