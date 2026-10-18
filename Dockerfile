# syntax=docker/dockerfile:1
# Debian 12 : r128gain 1.0.7 fait planter le ffmpeg 7.1 de Debian 13
# (assertion sur le anullsink de son graphe de filtres). Vérifié au build.
FROM python:3.12-slim-bookworm AS base

# Scripts d'enrichissement de lyrion-dashboard (embed_lyrics.py, embed_covers.py),
# figés sur un commit en attendant leur migration dans ce dépôt. Son image ne
# les contient pas (exclus par son .dockerignore).
ARG LYRION_DASHBOARD_REF=d0ec7904b8a8d5cdc24188a0ae836943cc267e0a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MUSIC_RANGER_ROOT=/media/werdeil/internal_drive/Musique \
    MUSIC_RANGER_INBOX=/media/werdeil/internal_drive/Inbox_Musique \
    MUSIC_RANGER_STATE=/state \
    LYRION_DASHBOARD_DIR=/opt/lyrion-dashboard \
    R128GAIN=r128gain

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

ADD https://github.com/werdeil/lyrion-dashboard/archive/${LYRION_DASHBOARD_REF}.tar.gz /tmp/lyrion-dashboard.tar.gz
RUN mkdir -p /opt/lyrion-dashboard \
 && tar -xzf /tmp/lyrion-dashboard.tar.gz -C /opt/lyrion-dashboard --strip-components=1 \
        --wildcards '*/scripts/embed_*.py' '*/services/*' '*/requirements-cli.txt' \
 && rm /tmp/lyrion-dashboard.tar.gz

WORKDIR /app
COPY requirements.txt ./
# r128gain : même version que /opt/venvs/r128gain sur l'hôte
RUN pip install --no-cache-dir -r requirements.txt \
        -r /opt/lyrion-dashboard/requirements-cli.txt r128gain==1.0.7

COPY music-ranger music_ranger.py import_inbox.py watch.py lms.py enrich.py ./
RUN ln -s /app/music-ranger /usr/local/bin/music-ranger \
 && useradd --create-home --uid 1000 ranger

# Les tests tournent à la construction : une image qui échoue n'est pas produite.
FROM base AS test
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests ./tests
USER ranger
RUN python -m pytest -q -p no:cacheprovider tests \
 && music-ranger --help > /dev/null \
 && python /opt/lyrion-dashboard/scripts/embed_lyrics.py --help > /dev/null \
 && python /opt/lyrion-dashboard/scripts/embed_covers.py --help > /dev/null \
 && ffmpeg -v error -f lavfi -i sine=duration=2 -c:a alac /tmp/sine.m4a \
 && r128gain --dry-run /tmp/sine.m4a 2>&1 | grep -q 'loudness = -' \
 && touch /tmp/tests-ok

FROM base
COPY --from=test /tmp/tests-ok /tmp/tests-ok
USER ranger
ENTRYPOINT ["music-ranger"]
CMD ["--help"]
