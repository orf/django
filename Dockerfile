ARG PYTHON_VERSION
FROM python:${PYTHON_VERSION}-slim

LABEL org.opencontainers.image.source https://github.com/orf/django

RUN apt-get update \
    && apt-get install --no-install-recommends -y -qq \
          libmemcached-dev \
          build-essential \
          libsqlite3-mod-spatialite binutils libproj-dev gdal-bin libgdal20 libgeoip1 geoip-database \
          default-libmysqlclient-dev default-mysql-client \
          libpq-dev \
          unzip libaio1 \
          libenchant1c2a git \
          gettext \
          wget \
    && apt-get clean

RUN groupadd -r test && useradd --no-log-init -r -g test test

RUN wget -q https://raw.githubusercontent.com/vishnubob/wait-for-it/master/wait-for-it.sh -O /bin/wait-for-it.sh \
    && chmod a+x /bin/wait-for-it.sh

ENV PIP_NO_CACHE_DIR=off
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
RUN pip install --upgrade pip

COPY --chown=test:test tests/requirements/ /requirements/
COPY --chown=test:test docs/requirements.txt /requirements/docs.txt
RUN for f in /requirements/*.txt; do pip install -q -r $f; done && \
    pip install -q flake8 flake8-isort selenium unittest-xml-reporting

RUN mkdir /tests && chown -R test:test /tests
RUN mkdir /tests/results && chown -R test:test /tests/results/
USER test:test
ENV PYTHONPATH "${PYTHONPATH}:/tests/django/"
VOLUME /tests/django
WORKDIR /tests/django
