"""
Setup script for GhostStream

Installation modes:
    pip install ghoststream          # SDK only (lightweight, for clients)
    pip install ghoststream[server]  # Full server with FastAPI + all dependencies

SDK Usage:
    from ghoststream import GhostStreamClient, TranscodeStatus
    
    client = GhostStreamClient(manual_server="192.168.4.2:8765")
    job = client.transcode_sync(source="http://...", resolution="1080p")
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
    "httpx>=0.27.0",
    "zeroconf>=0.131.0",
    "websockets>=12.0",  # For real-time progress updates
]

# Full server dependencies (in addition to SDK)
server_requirements = [
    # Core Framework
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "python-multipart>=0.0.9",
    # Configuration
    "pyyaml>=6.0.1",
    "pydantic>=2.6.0",
    "pydantic-settings>=2.2.0",
    # Async
    "aiofiles>=23.2.1",
    "asyncio-throttle>=1.0.2",
    # Logging
    "python-json-logger>=2.0.7",
    # Utilities
    "psutil>=5.9.7",
]

# All dependencies (SDK + server)
all_requirements = sdk_requirements + server_requirements

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
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Multimedia :: Video :: Conversion",
        "Framework :: FastAPI",
    ],
    python_requires=">=3.10",
    # By default, install SDK dependencies only (lightweight)
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
