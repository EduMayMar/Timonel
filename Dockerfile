FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 timonel

COPY dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

USER timonel
WORKDIR /work
ENV TIMONEL_HOME=/home/timonel/.timonel
ENTRYPOINT ["timonel"]
CMD ["--help"]
