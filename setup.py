"""
Setup script for GhostStream

Installation modes:
    pip install ghoststream            # SDK + shared models/contracts
    pip install "ghoststream[server]"  # Add the server runtime dependencies

SDK Usage:
    from ghoststream import GhostStreamClient, TranscodeStatus

    client = GhostStreamClient(manual_server="192.168.4.2:8765")
    job = client.transcode(source="http://...", resolution="1080p")
    print(f"Stream URL: {job.stream_url}")
"""

from setuptools import setup, find_packages

# Read version without importing the full package (avoids dependency issues)
__version__ = "1.0.0"
try:
    with open("ghoststream/__init__.py", "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("__version__"):
                __version__ = line.split("=")[1].strip().strip('"').strip("'")
                break
except Exception:
    pass

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

# Core SDK dependencies (minimal for client-only usage)
sdk_requirements = [
    "gevent>=24.2.1",
    "httpx>=0.27.0",
    "pydantic>=2.6.0",
    "zeroconf>=0.131.0",
]

# Full server dependencies (in addition to SDK)
server_requirements = [
    # Core Framework (Specter-native: Flask + gevent)
    "specter-runtime>=0.1.2",
    "flask>=3.0.0",
    "gevent-websocket>=0.10.1",
    # Configuration
    "pyyaml>=6.0.1",
    "pydantic-settings>=2.2.0",
    # Logging
    "python-json-logger>=2.0.7",
    # Dashboard (TUI)
    "textual>=0.50.0",
    "rich>=13.7.0",
    # Utilities
    "psutil>=5.9.7",
]

# All dependencies (SDK + server)
all_requirements = sdk_requirements + [
    requirement for requirement in server_requirements if requirement not in sdk_requirements
]

setup(
    name="ghoststream",
    version=__version__,
    author="GhostStream Contributors",
    description="Open Source Cross-Platform Transcoding Service & SDK",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/BleedingXiko/GhostStream",
    packages=find_packages(exclude=["tests", "tests.*", "examples"]),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Multimedia :: Video :: Conversion",
        "Framework :: Flask",
    ],
    python_requires=">=3.9",
    # By default, install the SDK + shared public models/contracts.
    install_requires=sdk_requirements,
    extras_require={
        # Full server installation
        "server": server_requirements,
        # All dependencies (SDK + server)
        "all": all_requirements,
        # Development dependencies
        "dev": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.23.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "ghoststream=ghoststream.__main__:main",
        ],
    },
    include_package_data=True,
    package_data={
        "ghoststream": ["*.yaml"],
    },
)
