from setuptools import find_packages, setup

setup(
    name="enterprise-insight-task-bridge",
    version="0.1.0",
    description="A narrow, idempotent Frappe v16 Task operation bridge for Enterprise Insight.",
    packages=find_packages(exclude=("tests", "tests.*")),
    include_package_data=True,
    zip_safe=False,
    install_requires=[],
)
