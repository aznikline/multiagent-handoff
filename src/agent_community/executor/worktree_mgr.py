"""Git worktree management for agent isolation.

Each agent gets its own worktree (independent directory + branch) so they
can work on the same codebase without file conflicts.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class WorktreeError(Exception):
    """Raised when worktree operations fail."""


class WorktreeManager:
    """Manage git worktrees for parallel agent execution.

    Each worktree provides an isolated filesystem view on its own branch,
    preventing agents from stepping on each other's changes.
    """

    def __init__(self, project_dir: Path | str) -> None:
        self._project = Path(project_dir).resolve()
        self._worktrees_dir = self._project / ".community-worktrees"

    @property
    def project_dir(self) -> Path:
        return self._project

    def ensure_git_repo(self) -> None:
        """Verify we're inside a git repository."""
        result = self._run_git(["rev-parse", "--git-dir"])
        if result.returncode != 0:
            raise WorktreeError(
                f"Not a git repository: {self._project}\n"
                "Agent Community requires a git repo for worktree isolation."
            )

    def create_worktree(self, branch_name: str, task_id: str) -> Path:
        """Create a new git worktree on a fresh branch.

        Args:
            branch_name: Branch name (e.g., 'community/task-1-auth').
            task_id: Task identifier (used for directory naming).

        Returns:
            Path to the new worktree directory.

        Raises:
            WorktreeError: If creation fails.
        """
        self.ensure_git_repo()
        self._worktrees_dir.mkdir(parents=True, exist_ok=True)

        # Sanitize task_id for filesystem
        safe_id = task_id.replace("/", "-").replace(" ", "-")
        worktree_path = self._worktrees_dir / safe_id

        if worktree_path.exists():
            logger.warning("Worktree already exists at %s, removing", worktree_path)
            self.remove_worktree(worktree_path)

        # Get the current HEAD to branch from
        head_result = self._run_git(["rev-parse", "HEAD"])
        if head_result.returncode != 0:
            raise WorktreeError("Failed to get HEAD commit")

        # Create the worktree with a new branch
        result = self._run_git([
            "worktree", "add",
            "-b", branch_name,
            str(worktree_path),
            "HEAD",
        ])

        if result.returncode != 0:
            # Branch might already exist — try without -b
            result = self._run_git([
                "worktree", "add",
                str(worktree_path),
                branch_name,
            ])
            if result.returncode != 0:
                raise WorktreeError(
                    f"Failed to create worktree: {result.stderr}"
                )

        logger.info("Created worktree: %s on branch %s", worktree_path, branch_name)
        return worktree_path

    def remove_worktree(self, worktree_path: Path) -> None:
        """Remove a worktree and its branch."""
        if not worktree_path.exists():
            return

        # Remove via git
        result = self._run_git([
            "worktree", "remove", str(worktree_path), "--force"
        ])
        if result.returncode != 0:
            # Fallback: manual cleanup
            logger.warning("git worktree remove failed, doing manual cleanup")
            shutil.rmtree(worktree_path, ignore_errors=True)
            self._run_git(["worktree", "prune"])

        logger.info("Removed worktree: %s", worktree_path)

    def cleanup_all(self) -> int:
        """Remove all community worktrees. Returns count removed."""
        if not self._worktrees_dir.exists():
            return 0

        count = 0
        for entry in self._worktrees_dir.iterdir():
            if entry.is_dir():
                self.remove_worktree(entry)
                count += 1

        # Also prune stale worktree entries
        self._run_git(["worktree", "prune"])

        # Remove the parent dir if empty
        try:
            self._worktrees_dir.rmdir()
        except OSError:
            pass

        logger.info("Cleaned up %d worktrees", count)
        return count

    def get_committed_files(self, worktree_path: Path) -> list[str]:
        """Get list of files changed in the worktree's branch vs main."""
        # Find the base branch (main or master)
        base = self._detect_base_branch()

        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True,
            text=True,
            cwd=str(worktree_path),
        )
        if result.returncode != 0:
            return []

        return [f.strip() for f in result.stdout.splitlines() if f.strip()]

    def merge_branch(self, branch_name: str) -> tuple[bool, str]:
        """Merge a branch into the current branch.

        Returns:
            (success, message) tuple.
        """
        result = self._run_git(["merge", "--no-edit", branch_name])
        if result.returncode == 0:
            return True, f"Merged {branch_name} successfully"

        # Check for conflicts
        if "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
            # Abort the merge
            self._run_git(["merge", "--abort"])
            return False, f"Merge conflict in {branch_name}: {result.stderr}"

        return False, f"Merge failed: {result.stderr}"

    def _detect_base_branch(self) -> str:
        """Detect the base branch (main or master)."""
        result = self._run_git(["rev-parse", "--verify", "main"])
        if result.returncode == 0:
            return "main"
        result = self._run_git(["rev-parse", "--verify", "master"])
        if result.returncode == 0:
            return "master"
        return "main"  # default fallback

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        """Run a git command in the project directory."""
        return subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=str(self._project),
        )

    def list_worktrees(self) -> list[dict[str, str]]:
        """List all community worktrees with their branch info."""
        if not self._worktrees_dir.exists():
            return []

        worktrees = []
        result = self._run_git(["worktree", "list", "--porcelain"])
        if result.returncode != 0:
            return []

        current: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                current = {"path": line[9:]}
            elif line.startswith("branch "):
                current["branch"] = line[7:]
            elif line == "":
                if current and str(self._worktrees_dir) in current.get("path", ""):
                    worktrees.append(current)
                current = {}

        return worktrees
