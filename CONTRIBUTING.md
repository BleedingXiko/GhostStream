# Contributing to GhostStream

Thank you for your interest in contributing to GhostStream! This document provides guidelines and information for contributors.

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [How to Contribute](#how-to-contribute)
- [Development Setup](#development-setup)
- [Code Style](#code-style)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)
- [Issue Guidelines](#issue-guidelines)

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/ghoststream.git
   cd ghoststream
   ```
3. **Add upstream remote**:
   ```bash
   git remote add upstream https://github.com/ORIGINAL_OWNER/ghoststream.git
   ```

## How to Contribute

### 🐛 Reporting Bugs

Before submitting a bug report:
- Check existing issues to avoid duplicates
- Collect information about the bug:
  - OS and version (Windows/macOS/Linux)
  - Python version
  - FFmpeg version
  - GPU and driver version (if applicable)
  - Steps to reproduce
  - Expected vs actual behavior
  - Relevant log output

### 💡 Suggesting Features

Feature requests are welcome! Please:
- Check if the feature has already been requested
- Describe the use case and why it would be valuable
- Consider if it fits the project's scope

### 🔧 Code Contributions

We accept pull requests for:
- Bug fixes
- New features (discuss in an issue first for major features)
- Documentation improvements
- Test coverage improvements
- Performance optimizations

## Development Setup

### Prerequisites

- Python 3.10+
- FFmpeg with hardware acceleration support
- Git

### Setup

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/ghoststream.git
cd ghoststream

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or
.\venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Install development dependencies
pip install pytest pytest-asyncio black isort mypy

# Verify setup
python -m ghoststream --help
```

### Project Structure

```
ghoststream/
├── ghoststream/           # Main package
│   ├── __init__.py
│   ├── __main__.py       # Entry point
│   ├── api.py            # FastAPI routes
│   ├── config.py         # Configuration
│   ├── hardware.py       # Hardware detection
│   ├── jobs.py           # Job management
│   ├── models.py         # Pydantic models
│   ├── transcoder.py     # Legacy re-exports
│   └── transcoding/      # Modular transcoding package
│       ├── adaptive.py   # Enterprise load balancing
│       ├── commands.py   # FFmpeg command building
│       ├── constants.py  # Constants and presets
│       ├── encoders.py   # Encoder selection
│       ├── engine.py     # Main engine
│       ├── filters.py    # Video filters
│       ├── models.py     # Transcoding models
│       └── probe.py      # Media probing
├── tests/                # Test suite
├── docs/                 # Documentation
├── examples/             # Usage examples
└── docker/               # Docker files
```

## Code Style

### Python Style

We follow PEP 8 with these tools:

```bash
# Format code with black
black ghoststream/

# Sort imports with isort
isort ghoststream/

# Type checking with mypy
mypy ghoststream/
```

### Guidelines

- Use type hints for all function signatures
- Write docstrings for public functions and classes
- Keep functions focused and small
- Use meaningful variable names
- Add comments for complex logic

### Example

```python
async def transcode_video(
    source: str,
    output_config: OutputConfig,
    progress_callback: Optional[Callable[[TranscodeProgress], None]] = None
) -> Tuple[bool, str]:
    """
    Transcode a video file with the given configuration.
    
    Args:
        source: URL or path to source video
        output_config: Transcoding output configuration
        progress_callback: Optional callback for progress updates
        
    Returns:
        Tuple of (success, output_path_or_error_message)
    """
    # Implementation...
```

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=ghoststream

# Run specific test file
pytest tests/test_transcoder.py

# Run with verbose output
pytest -v
```

### Writing Tests

- Place tests in the `tests/` directory
- Name test files `test_*.py`
- Name test functions `test_*`
- Use pytest fixtures for common setup
- Mock external dependencies (FFmpeg, nvidia-smi, etc.)

```python
import pytest
from ghoststream.transcoding import TranscodeEngine

@pytest.fixture
def engine():
    return TranscodeEngine()

async def test_get_media_info(engine):
    info = await engine.get_media_info("test_video.mp4")
    assert info.width > 0
    assert info.height > 0
```

## Pull Request Process

### Before Submitting

1. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/bug-description
   ```

2. **Make your changes** with clear, atomic commits

3. **Test your changes**:
   ```bash
   pytest
   black --check ghoststream/
   isort --check ghoststream/
   ```

4. **Update documentation** if needed

### Submitting

1. Push to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```

2. Open a Pull Request on GitHub

3. Fill out the PR template with:
   - Description of changes
   - Related issue numbers
   - Testing done
   - Screenshots (if UI changes)

### PR Review

- Maintainers will review your PR
- Address any requested changes
- Once approved, your PR will be merged

## Issue Guidelines

### Bug Reports

Use the bug report template and include:
- Clear title describing the issue
- Steps to reproduce
- Expected behavior
- Actual behavior
- Environment details
- Logs/screenshots

### Feature Requests

Use the feature request template and include:
- Clear description of the feature
- Use case / motivation
- Possible implementation approach
- Alternatives considered

## Questions?

- Open a Discussion on GitHub
- Check existing issues and discussions
- Read the documentation in `docs/`

---

Thank you for contributing to GhostStream! 🎉
