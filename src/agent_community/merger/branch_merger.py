"""Branch merging and post-merge validation.

After all agents complete their work in isolated worktrees,
this module merges all branches and runs validation.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class MergeError(Exception):
    """Raised when merge operations fail."""


class BranchMerger:
    """Merge agent work branches and validate the combined result."""

    def __init__(self, project_dir: Path | str) -> None:
        self._project = Path(project_dir).resolve()

    def merge_branches(
        self,
        branch_names: list[str],
        strategy: str = "sequential",
    ) -> tuple[bool, list[str]]:
        """Merge multiple branches into the current branch.

        Args:
            branch_names: Branches to merge, in order.
            strategy: Merge strategy — 'sequential' merges one by one.

        Returns:
            (success, conflicts) tuple. conflicts is a list of conflict descriptions.
        """
        conflicts: list[str] = []
        merged: list[str] = []

        for branch in branch_names:
            success, msg = self._merge_single(branch)
            if success:
                merged.append(branch)
                logger.info("✓ Merged %s", branch)
            else:
                conflicts.append(f"{branch}: {msg}")
                logger.warning("✗ Merge conflict in %s: %s", branch, msg)
                # Abort the failed merge to keep working tree clean
                self._run_git(["merge", "--abort"])

        all_success = len(conflicts) == 0
        return all_success, conflicts

    def _merge_single(self, branch_name: str) -> tuple[bool, str]:
        """Merge a single branch. Returns (success, message)."""
        result = self._run_git(["merge", "--no-edit", branch_name])
        if result.returncode == 0:
            return True, f"Merged {branch_name}"

        stderr = result.stderr.strip()
        if "CONFLICT" in stderr or "CONFLICT" in result.stdout:
            return False, f"Conflict: {stderr[:300]}"
        return False, stderr[:300]

    def validate_merge(self, test_command: str = "pytest -x -q") -> tuple[bool, str]:
        """Run validation after merge.

        Args:
            test_command: Command to run for validation.

        Returns:
            (passed, output) tuple.
        """
        logger.info("Running post-merge validation: %s", test_command)

        result = subprocess.run(
            test_command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(self._project),
            timeout=120,
        )

        passed = result.returncode == 0
        output = result.stdout + result.stderr

        if passed:
            logger.info("✓ Validation passed")
        else:
            logger.warning("✗ Validation failed:\n%s", output[:500])

        return passed, output[:2000]

    def get_merge_diff_summary(self) -> str:
        """Get a summary of all changes from the merge."""
        result = self._run_git(["diff", "--stat", "HEAD~1"])
        if result.returncode == 0:
            return result.stdout
        return "(could not get diff)"

    def rollback_merge(self, commit_count: int = 1) -> bool:
        """Rollback merge commits."""
        result = self._run_git(["reset", "--hard", f"HEAD~{commit_count}"])
        return result.returncode == 0

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=str(self._project),
        )
