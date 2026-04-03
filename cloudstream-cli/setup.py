from setuptools import setup, find_namespace_packages

setup(
    name="cli-anything-cloudstream",
    version="1.0.0",
    description="CLI harness for CloudStream — search, browse, and download streaming content",
    long_description=open("cli_anything/cloudstream/README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="cli-anything",
    license="GPL-3.0",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    install_requires=[
        "click>=8.0.0",
        "prompt-toolkit>=3.0.0",
        "requests>=2.28.0",
        "beautifulsoup4>=4.11.0",
        "pycryptodome>=3.15.0",
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-cloudstream=cli_anything.cloudstream.cloudstream_cli:main",
        ],
    },
    python_requires=">=3.10",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: OS Independent",
        "Environment :: Console",
    ],
)
