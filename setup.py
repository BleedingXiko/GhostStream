"""
Setup script for GhostStream
"""

from setuptools import setup, find_packages
from ghoststream import __version__

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

with open("requirements.txt", "r", encoding="utf-8") as f:
    requirements = [
        line.strip() for line in f
        if line.strip() and not line.startswith("#")
    ]

setup(
    name="ghoststream",
    version=__version__,
    author="GhostStream Contributors",
    description="Open Source Cross-Platform Transcoding Service",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/ghoststream",
    packages=find_packages(exclude=["tests", "tests.*"]),
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
    install_requires=requirements,
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.21.0",
            "httpx>=0.25.0",
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
