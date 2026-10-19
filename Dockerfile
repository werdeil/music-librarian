# syntax=docker/dockerfile:1
# Debian 12 : r128gain 1.0.7 fait planter le ffmpeg 7.1 de Debian 13
# (assertion sur le anullsink de son graphe de filtres). Vérifié au build.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MUSIC_LIBRARIAN_STATE=/state \
    R128GAIN=r128gain

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt r128gain==1.0.7

COPY music-librarian music_ranger.py import_inbox.py watch.py lms.py enrich.py \
     embed_covers.py embed_lyrics.py tags.py artwork.py lyrics.py ./
RUN ln -s /app/music-librarian /usr/local/bin/music-librarian \
 && useradd --create-home --uid 1000 librarian

# Les tests tournent à la construction : une image qui échoue n'est pas produite.
FROM base AS test
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests ./tests
USER librarian
RUN python -m pytest -q -p no:cacheprovider tests \
 && music-librarian --help > /dev/null \
 && python embed_lyrics.py --help > /dev/null \
 && python embed_covers.py --help > /dev/null \
 && ffmpeg -v error -f lavfi -i sine=duration=2 -c:a alac /tmp/sine.m4a \
 && r128gain --dry-run /tmp/sine.m4a 2>&1 | grep -q 'loudness = -' \
 && touch /tmp/tests-ok

FROM base
COPY --from=test /tmp/tests-ok /tmp/tests-ok
USER librarian
ENTRYPOINT ["music-librarian"]
CMD ["--help"]
