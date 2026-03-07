"""Tests for public repo readiness (P4: GitHub AGPL-3.0 prep)."""

import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestLicenseFile:
    """AGPL-3.0 license file exists and has correct content."""

    def test_license_file_exists(self):
        assert os.path.isfile(os.path.join(REPO_ROOT, 'LICENSE'))

    def test_license_is_agpl3(self):
        with open(os.path.join(REPO_ROOT, 'LICENSE')) as f:
            content = f.read()
        assert 'GNU AFFERO GENERAL PUBLIC LICENSE' in content
        assert 'Version 3' in content

    def test_license_has_copyright(self):
        with open(os.path.join(REPO_ROOT, 'LICENSE')) as f:
            content = f.read()
        assert 'OpenLEG' in content or 'openleg' in content.lower()


class TestGitignore:
    """Sensitive files excluded from repo."""

    def test_gitignore_excludes_env(self):
        with open(os.path.join(REPO_ROOT, '.gitignore')) as f:
            content = f.read()
        assert '.env' in content

    def test_gitignore_excludes_secrets(self):
        with open(os.path.join(REPO_ROOT, '.gitignore')) as f:
            content = f.read()
        assert 'secrets/' in content
        assert '*.pem' in content

    def test_no_env_file_tracked(self):
        # .env should not exist in repo (only .env.example)
        env_path = os.path.join(REPO_ROOT, '.env')
        if os.path.exists(env_path):
            # Verify it's gitignored (not tracked)
            import subprocess

            result = subprocess.run(['git', 'check-ignore', '.env'], cwd=REPO_ROOT, capture_output=True, text=True)
            assert result.returncode == 0, '.env should be gitignored'


class TestEnvExample:
    """.env.example has no real secrets."""

    def test_env_example_exists(self):
        assert os.path.isfile(os.path.join(REPO_ROOT, '.env.example'))

    def test_env_example_has_placeholders(self):
        with open(os.path.join(REPO_ROOT, '.env.example')) as f:
            content = f.read()
        # Should have placeholder patterns, not real values
        assert 'your-' in content or 'XXXXXXXXXX' in content
        # Should not contain real API keys
        assert 'sk-ant-' not in content or content.count('sk-ant-') == content.count('sk-ant-...')


class TestReadme:
    """README exists for public repo."""

    def test_readme_exists(self):
        assert os.path.isfile(os.path.join(REPO_ROOT, 'README.md'))

    def test_readme_mentions_leg(self):
        with open(os.path.join(REPO_ROOT, 'README.md')) as f:
            content = f.read()
        assert 'LEG' in content or 'Elektrizitätsgemeinschaft' in content


class TestContributingGuide:
    """CONTRIBUTING.md exists with required sections."""

    def test_contributing_exists(self):
        assert os.path.isfile(os.path.join(REPO_ROOT, 'CONTRIBUTING.md'))

    def test_contributing_has_dev_setup(self):
        with open(os.path.join(REPO_ROOT, 'CONTRIBUTING.md')) as f:
            content = f.read()
        assert 'pytest' in content

    def test_contributing_has_architecture(self):
        with open(os.path.join(REPO_ROOT, 'CONTRIBUTING.md')) as f:
            content = f.read()
        assert 'Flask' in content
        assert 'PostgreSQL' in content

    def test_contributing_has_good_first_issues(self):
        with open(os.path.join(REPO_ROOT, 'CONTRIBUTING.md')) as f:
            content = f.read()
        assert 'Good First Issues' in content or 'good first' in content.lower()
