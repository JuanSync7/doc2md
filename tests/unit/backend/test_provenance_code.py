"""
title: Unit — code and host identity
kind: tests
layer: backend
summary: The converter stamp is read from the checkout, works without a git binary, and never invents a clean tree.
"""
import os

from backend.provenance import (code_identity, git_commit, host_identity,
                                package_version)


def _git(tmp_path, head, refs=None, packed=""):
    g = tmp_path / ".git"
    g.mkdir()
    (g / "HEAD").write_text(head)
    for ref, sha in (refs or {}).items():
        p = g
        for part in ref.split("/")[:-1]:
            p = p / part
            p.mkdir(exist_ok=True)
        (p / ref.split("/")[-1]).write_text(sha + "\n")
    if packed:
        (g / "packed-refs").write_text(packed)
    return str(tmp_path)


def test_commit_from_a_loose_ref(tmp_path):
    root = _git(tmp_path, "ref: refs/heads/main\n",
                {"refs/heads/main": "81e94a7c0de1f2a3b4c5d6e7f8091a2b3c4d5e6f"})
    assert git_commit(root) == "81e94a7c0de1f2a3b4c5d6e7f8091a2b3c4d5e6f"


def test_commit_from_packed_refs_when_the_loose_ref_is_absent(tmp_path):
    # A freshly cloned checkout has every ref packed and no loose files at all.
    root = _git(tmp_path, "ref: refs/heads/main\n",
                packed="# pack-refs with: peeled\n"
                       "aaaabbbbccccddddeeeeffff0000111122223333 refs/heads/main\n"
                       "^deadbeef00000000000000000000000000000000\n")
    assert git_commit(root) == "aaaabbbbccccddddeeeeffff0000111122223333"


def test_detached_head_reports_the_sha_directly(tmp_path):
    root = _git(tmp_path, "1234567890abcdef1234567890abcdef12345678\n")
    assert git_commit(root) == "1234567890abcdef1234567890abcdef12345678"


def test_not_a_checkout_reports_nothing_rather_than_guessing(tmp_path):
    assert git_commit(str(tmp_path)) == ""
    ident = code_identity(str(tmp_path))
    assert "commit" not in ident and ident["name"] == "doc2md"


def test_a_worktree_gitdir_file_is_followed(tmp_path):
    real = tmp_path / "real"
    (real / "worktrees" / "wt").mkdir(parents=True)
    (real / "refs" / "heads").mkdir(parents=True)
    (real / "refs" / "heads" / "topic").write_text("cafebabe" * 5 + "\n")
    (real / "worktrees" / "wt" / "HEAD").write_text("ref: refs/heads/topic\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: %s\n" % (real / "worktrees" / "wt"))
    # The ref lives in the COMMON dir, not the per-worktree one.
    assert git_commit(str(wt)) == "cafebabe" * 5


def test_dirty_is_omitted_when_unknown_never_reported_clean(tmp_path):
    # "clean" is exactly the claim a reader would act on, so it must not be
    # inventable from an absent answer.
    assert "dirty" not in code_identity(str(tmp_path))
    assert code_identity(str(tmp_path), dirty=False)["dirty"] is False
    assert code_identity(str(tmp_path), dirty=True)["dirty"] is True


def test_version_comes_from_the_project_table_not_a_dependency_pin(tmp_path):
    """The decoy goes BEFORE `[project]`, which is the only placement that tests it.

    With the decoy after, the first match in the file is the right one anyway and
    this test passed with the guard deleted — a check that cannot fail. A real
    pyproject opens with a comment or `[build-system]` (this repo's does both), so
    the old `text.startswith("[project]")` guard never fired and the first
    `version =` anywhere in the file won.
    """
    (tmp_path / "pyproject.toml").write_text(
        '# doc2md\n[build-system]\nrequires = ["setuptools"]\n\n'
        '[tool.commitizen]\nversion = "9.9.9"\n\n'
        '[project]\nname = "doc2md"\nversion = "0.1.0"\n\n'
        '[tool.poetry.dependencies]\nversion = "8.8.8"\n')
    assert package_version(str(tmp_path)) == "0.1.0"


def test_a_project_table_that_states_no_version_yields_none_not_someone_elses(tmp_path):
    # PEP 621 `dynamic = ["version"]`: the distribution's version is not in this
    # file. Reporting a tool table's number instead would stamp every bundle's
    # `run.code.version` — and the graded `converter` string — with a version of the
    # converter that does not exist.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "doc2md"\ndynamic = ["version"]\n\n'
        '[tool.bumpversion]\nversion = "9.9.9"\n')
    assert package_version(str(tmp_path)) == ""


def test_a_project_with_no_project_table_still_reports_its_version(tmp_path):
    # A poetry layout keeps the version in `[tool.poetry]`, and there is no other
    # candidate to confuse it with. Losing it would trade a wrong version for a
    # missing one, which degrades the converter stamp to `doc2md-ooxml/0+<sha>`.
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "doc2md"\nversion = "2.3.4"\n')
    assert package_version(str(tmp_path)) == "2.3.4"


def test_version_absent_is_empty_not_an_exception(tmp_path):
    assert package_version(str(tmp_path)) == ""


def test_this_repo_identifies_itself():
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    ident = code_identity(root)
    assert ident["name"] == "doc2md"
    assert ident.get("version"), "pyproject.toml should carry a version"
    assert len(ident.get("commit", "")) == 40, "a real checkout resolves its commit"


def test_host_identity_never_names_the_machine():
    host = host_identity()
    assert host["python"].count(".") == 2
    assert host["platform"]
    import platform as _p
    node = _p.node()
    if node and len(node) > 3:
        assert node not in " ".join("%s" % v for v in host.values())
