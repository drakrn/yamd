# yamd

Yet Another Media Downloader.

Download online media and convert local files, from a CLI, a GUI, or an HTTP API.

## Requirements

- Python 3.13+
- uv
- ffmpeg available on your PATH

## Installation

```
git clone https://github.com/yourname/yamd.git
cd yamd

# Create virtual environment and install all dependencies
uv sync --dev

# Copy and edit environment variables
cp .env.example .env
```

## Usage

```
# Download a video
uv run yamd download https://www.youtube.com/watch?v=...

# Convert a local file
uv run yamd convert input.mp4 --format mp3

# Start the HTTP API
uv run yamd api serve
```

## Development

```
# Run tests
uv run pytest

# Lint and format
uv run ruff check src tests
uv run ruff format src tests

# Type check
uv run mypy src
```

## Architecture

See `docs/architecture.md` for the full four-layer design.

## License

This project is released under the MIT license - check the [LICENSE.txt](LICENSE.txt) file for details.