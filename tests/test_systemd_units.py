"""
Lint the systemd units without systemd.

2026-08 review T1.5: `StartLimitIntervalSec=` sat in [Service], where systemd
logs "Unknown key name ... ignoring" and silently falls back to a 10s default
that RestartSec=3 can never trip. The crash-loop guard the comment promised
did not exist, and an unstartable server restarted forever. Nothing in CI
could see it because CI has no systemd. This test checks every directive in
every unit against the section it is valid in, so a misplaced key fails the
build instead of being ignored on the Pi.
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

UNIT_DIR = Path(__file__).resolve().parent.parent / "scripts" / "systemd"
UNITS = sorted(UNIT_DIR.glob("*.service"))

# Directives by section (systemd.unit(5), systemd.service(5), systemd.exec(5),
# systemd.kill(5), systemd.resource-control(5)). Not exhaustive - add to it
# when a legitimate new directive is used. The point is that a key in the
# WRONG section is caught.
KNOWN = {
    "Unit": {
        "Description", "Documentation", "Wants", "Requires", "Requisite", "BindsTo",
        "PartOf", "Conflicts", "Before", "After", "OnFailure", "OnSuccess",
        "StartLimitIntervalSec", "StartLimitBurst", "StartLimitAction",
        "ConditionPathExists", "ConditionPathIsDirectory", "AssertPathExists",
        "DefaultDependencies", "StopWhenUnneeded", "RefuseManualStart", "RefuseManualStop",
        "IgnoreOnIsolate", "FailureAction", "SuccessAction",
    },
    "Service": {
        "Type", "ExitType", "RemainAfterExit", "GuessMainPID", "PIDFile", "BusName",
        "ExecStartPre", "ExecStart", "ExecStartPost", "ExecCondition", "ExecReload",
        "ExecStop", "ExecStopPost", "RestartSec", "Restart", "RestartSteps",
        "RestartMaxDelaySec", "TimeoutStartSec", "TimeoutStopSec", "TimeoutSec",
        "RuntimeMaxSec", "WatchdogSec", "SuccessExitStatus", "RestartPreventExitStatus",
        "RestartForceExitStatus", "NonBlocking", "NotifyAccess", "Sockets", "OOMPolicy",
        # systemd.exec
        "User", "Group", "WorkingDirectory", "RootDirectory", "Environment",
        "EnvironmentFile", "PassEnvironment", "UnsetEnvironment", "StandardInput",
        "StandardOutput", "StandardError", "SyslogIdentifier", "SyslogFacility",
        "SyslogLevel", "UMask", "Nice", "OOMScoreAdjust", "LimitNOFILE", "LimitCORE",
        "NoNewPrivileges", "ProtectSystem", "ProtectHome", "ReadWritePaths",
        "ReadOnlyPaths", "InaccessiblePaths", "PrivateTmp", "PrivateDevices",
        "ProtectKernelTunables", "ProtectKernelModules", "ProtectControlGroups",
        "ProtectClock", "ProtectHostname", "RestrictNamespaces", "RestrictRealtime",
        "RestrictSUIDSGID", "LockPersonality", "MemoryDenyWriteExecute",
        "SystemCallFilter", "SystemCallArchitectures", "CapabilityBoundingSet",
        "AmbientCapabilities", "DeviceAllow", "SupplementaryGroups", "TTYPath",
        # systemd.kill
        "KillMode", "KillSignal", "RestartKillSignal", "FinalKillSignal",
        "SendSIGHUP", "SendSIGKILL", "TimeoutAbortSec",
        # resource control
        "MemoryMax", "MemoryHigh", "CPUQuota", "TasksMax", "IOWeight",
    },
    "Install": {"WantedBy", "RequiredBy", "Also", "Alias", "DefaultInstance"},
}


def parse_unit(path: Path) -> configparser.ConfigParser:
    # Unit files allow repeated keys (ReadWritePaths=, Environment=) - use a
    # dict subclass that keeps them all so nothing is silently merged.
    class MultiDict(dict):
        def __setitem__(self, key, value):
            if isinstance(value, list) and key in self:
                self[key].extend(value)
            else:
                super().__setitem__(key, value)

    cp = configparser.ConfigParser(
        dict_type=MultiDict, strict=False, interpolation=None, delimiters=("=",),
        comment_prefixes=("#", ";"), inline_comment_prefixes=None,
    )
    cp.optionxform = str  # case-sensitive keys
    cp.read_string(path.read_text())
    return cp


@pytest.mark.parametrize("unit", UNITS, ids=[u.name for u in UNITS])
def test_every_directive_is_valid_for_its_section(unit):
    cp = parse_unit(unit)
    assert cp.sections(), f"{unit.name}: no sections parsed"
    problems = []
    for section in cp.sections():
        assert section in KNOWN, f"{unit.name}: unknown section [{section}]"
        for key in cp[section]:
            if key not in KNOWN[section]:
                where = [s for s, keys in KNOWN.items() if key in keys]
                hint = f" (valid in [{where[0]}])" if where else ""
                problems.append(f"[{section}] {key}={hint}")
    assert not problems, f"{unit.name}: directives systemd would IGNORE: {problems}"


def test_units_that_bind_all_interfaces_do_not_wait_for_network_online():
    """network-online.target pulls NetworkManager-wait-online, which at the
    nets (no known WiFi) blocks for its full timeout. The radar is USB; the
    server and UI bind 0.0.0.0. None of them may wait for it."""
    for name in ("cricket-radar.service", "cricket-server.service", "cricket-ui.service"):
        text = (UNIT_DIR / name).read_text()
        code = "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))
        assert "network-online.target" not in code, f"{name} waits for network-online.target"


# Specifiers systemd substitutes in unit values (systemd.unit(5)). '%%' is a
# literal percent. Anything else after '%' - notably the %20 of a URL-encoded
# path - makes systemd log "Failed to resolve unit specifiers ... Invalid
# slot" and DISCARD the whole line, silently.
VALID_SPECIFIERS = set("aAbBCdEfgGhHiIjJlLmMnNoOpPsStTuUvVwWyY%")


@pytest.mark.parametrize("unit", UNITS, ids=[u.name for u in UNITS])
def test_percent_signs_are_escaped(unit):
    problems = []
    for lineno, line in enumerate(unit.read_text().splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        value = stripped.split("=", 1)[1]
        i = 0
        while i < len(value):
            if value[i] == "%":
                nxt = value[i + 1] if i + 1 < len(value) else ""
                if nxt not in VALID_SPECIFIERS:
                    problems.append(f"line {lineno}: '%{nxt}' is not a specifier - escape it as '%%{nxt}'")
                i += 2  # skip the pair either way ('%%' is literal)
                continue
            i += 1
    assert not problems, f"{unit.name}: {problems}"


def test_read_write_paths_tolerate_missing_directories():
    for unit in UNITS:
        for line in unit.read_text().splitlines():
            if line.startswith("ReadWritePaths="):
                assert line.startswith("ReadWritePaths=-"), f"{unit.name}: {line!r} needs the '-' prefix"


def test_start_limit_directives_live_in_unit_section():
    for unit in UNITS:
        cp = parse_unit(unit)
        if "Service" in cp:
            for key in cp["Service"]:
                assert not key.startswith("StartLimit"), f"{unit.name}: {key} in [Service] is ignored"


def test_radar_unit_uses_the_repo_profile():
    text = (UNIT_DIR / "cricket-radar.service").read_text()
    assert "cricket-app/config/profile_cricket.cfg" in text
    assert (UNIT_DIR.parent.parent / "config" / "profile_cricket.cfg").exists()


def test_every_shipped_unit_is_installed_by_both_scripts():
    deploy = (UNIT_DIR.parent / "deploy_to_pi.sh").read_text()
    install = (UNIT_DIR.parent / "install_services.sh").read_text()
    code = "\n".join(line for line in deploy.splitlines() if not line.strip().startswith("#"))
    assert "systemd/*.service" in code
    assert '"$UNIT_DIR"/*.service' in install
    for unit in UNITS:
        assert unit.stem in code, f"deploy_to_pi.sh does not enable {unit.name}"


def test_deploy_gates_run_before_any_file_lands_on_the_pi():
    """The deps check and the frontend build must precede the first rsync:
    a failure after code has landed leaves new code waiting to start against
    an unmigrated DB on the next restart."""
    deploy = (UNIT_DIR.parent / "deploy_to_pi.sh").read_text()
    first_rsync = deploy.index("rsync -az")
    assert deploy.index("import websockets, serial") < first_rsync
    assert deploy.index("npm run build") < first_rsync
