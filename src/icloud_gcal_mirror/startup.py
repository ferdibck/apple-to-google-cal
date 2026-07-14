from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "iCloud Google Calendar Mirror"


def install_startup_task(home: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("Startup task installation is only supported on Windows.")

    xml_path = home / "startup-task.xml"
    xml_path.write_text(_task_xml(sys.executable), encoding="utf-8")
    subprocess.run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"],
        check=True,
        capture_output=True,
        text=True,
    )


def uninstall_startup_task() -> None:
    if os.name != "nt":
        raise RuntimeError("Startup task removal is only supported on Windows.")
    subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        check=True,
        capture_output=True,
        text=True,
    )


def startup_task_status() -> str:
    if os.name != "nt":
        return "not supported on this operating system"
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return "installed"
    return "not installed"


def _task_xml(python_executable: str) -> str:
    command = escape(python_executable)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Runs the iCloud Google Calendar Mirror synchronizer at user login.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>-m icloud_gcal_mirror run</Arguments>
    </Exec>
  </Actions>
</Task>
"""
