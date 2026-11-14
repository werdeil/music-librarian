# syntax=docker/dockerfile:1
FROM python:3.12-slim-trixie AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MUSIC_LIBRARIAN_STATE=/state

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Dependencies come pinned from requirements.txt, hence --no-deps
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps . \
 && rm -rf src build \
 && useradd --create-home --uid 1000 librarian

# Built on demand only: docker build --target test .
FROM base AS test
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests ./tests
USER librarian
RUN python -m pytest -q -p no:cacheprovider tests

FROM base
USER librarian
# The tools are checked on each platform at build time: an image whose ffmpeg fails is never produced.
RUN music-librarian --help > /dev/null \
 && python -m music_librarian.enrich.lyrics --help > /dev/null \
 && python -m music_librarian.enrich.covers --help > /dev/null \
 && python -m music_librarian.enrich.genres --help > /dev/null \
 && mkdir /tmp/sine && ffmpeg -v error -f lavfi -i sine=duration=2 -c:a alac /tmp/sine/sine.m4a \
 && MUSIC_LIBRARIAN_STATE=/tmp/state python -m music_librarian.enrich.replaygain /tmp/sine \
    | grep '^GAIN ' > /dev/null \
 && rm -rf /tmp/sine /tmp/state
ENTRYPOINT ["music-librarian"]
CMD ["--help"]
