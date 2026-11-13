# SPDX-License-Identifier: AGPL-3.0-or-later
"""Copy the authoritative operator contract into built CLI distributions."""

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildCLI(build_py):
    def run(self):
        super().run()
        self.copy_file(
            str(Path(__file__).parent / "docs/openapi/operator-v1.yaml"),
            str(Path(self.build_lib) / "openleg_ctl/operator-v1.yaml"),
        )


setup(cmdclass={"build_py": BuildCLI})
