import shutil
from typing import List


def powershell_executable() -> str:
    if shutil.which("pwsh.exe"):
        return "pwsh.exe"
    if shutil.which("pwsh"):
        return "pwsh"
    return "powershell.exe"


def powershell_cim_command(script: str) -> List[str]:
    return [
        powershell_executable(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
