"""
Docker sandbox — tests risky commands in a container before applying to the real system.
"""

import logging
import subprocess

logger = logging.getLogger(__name__)

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


class Sandbox:
    """Executes commands in a Docker container for safe testing."""

    def __init__(self, image: str = "mcr.microsoft.com/windows/nanoserver:ltsc2022"):
        self.image = image
        self._available = self._check_docker()

    def _check_docker(self) -> bool:
        try:
            result = subprocess.run(
                ["docker", "version"], capture_output=True, text=True, timeout=10, creationflags=_SUBPROCESS_FLAGS,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.warning("Docker not available — sandbox disabled")
            return False

    @property
    def available(self) -> bool:
        return self._available

    def test_command(self, command: str, timeout: int = 30) -> dict:
        """
        Run a command in a disposable Docker container.
        Returns: {"success": bool, "output": str, "exit_code": int}
        """
        if not self._available:
            return {
                "success": False,
                "output": "Docker not available",
                "exit_code": -1,
            }

        try:
            result = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "--memory=512m",
                    "--cpus=0.5",
                    self.image,
                    "cmd", "/c", command,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=_SUBPROCESS_FLAGS,
            )
            return {
                "success": result.returncode == 0,
                "output": result.stdout or result.stderr,
                "exit_code": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": f"Command timed out after {timeout}s",
                "exit_code": -1,
            }
        except Exception as e:
            return {
                "success": False,
                "output": str(e),
                "exit_code": -1,
            }
