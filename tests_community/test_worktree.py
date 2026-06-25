"""Tests for the worktree manager."""

import os
import subprocess
import pytest
from pathlib import Path
from agent_community.executor.worktree_mgr import WorktreeManager, WorktreeError


@pytest.fixture
def temp_git_repo(tmp_path):
    """Create a temporary git repository with an initial commit."""
    repo = tmp_path / "test-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
    return repo


class TestWorktreeManager:
    def test_create_worktree(self, temp_git_repo):
        mgr = WorktreeManager(temp_git_repo)
        wt = mgr.create_worktree("community/test-branch", "task-1")
        assert wt.exists()
        assert (wt / "README.md").exists()

    def test_create_and_remove_worktree(self, temp_git_repo):
        mgr = WorktreeManager(temp_git_repo)
        wt = mgr.create_worktree("community/test-2", "task-2")
        assert wt.exists()
        mgr.remove_worktree(wt)
        assert not wt.exists()

    def test_cleanup_all(self, temp_git_repo):
        mgr = WorktreeManager(temp_git_repo)
        mgr.create_worktree("community/a", "task-a")
        mgr.create_worktree("community/b", "task-b")
        count = mgr.cleanup_all()
        assert count == 2

    def test_list_worktrees(self, temp_git_repo):
        mgr = WorktreeManager(temp_git_repo)
        mgr.create_worktree("community/list-test", "task-list")
        worktrees = mgr.list_worktrees()
        assert len(worktrees) >= 1

    def test_not_git_repo_raises(self, tmp_path):
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        mgr = WorktreeManager(non_repo)
        with pytest.raises(WorktreeError, match="Not a git repository"):
            mgr.ensure_git_repo()

    def test_merge_branch(self, temp_git_repo):
        mgr = WorktreeManager(temp_git_repo)
        # Create worktree and make a change
        wt = mgr.create_worktree("community/merge-test", "task-merge")
        (wt / "new_file.txt").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=wt, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add file"],
            cwd=wt, capture_output=True,
        )
        # Merge back
        success, msg = mgr.merge_branch("community/merge-test")
        assert success
        assert (temp_git_repo / "new_file.txt").exists()
