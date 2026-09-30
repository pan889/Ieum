FROM alpine:3.22

ARG TARGETARCH
RUN apk add --no-cache ca-certificates curl \
    && case "$TARGETARCH" in \
      amd64) minio_hash=53e2a2cb16c5366ea6fbbc479c19ddb4c6a0948273e752f740fb1fbf27bb817c; \
             mc_hash=ac90da87a35641be5a0ac75d49de5161ddb47d629b5ba01261b0ae9e00aea15f ;; \
      arm64) minio_hash=6c2f3142c94240206123177f4ba1e360daa5d1e0a4962e90757ef4f92c3ab57c; \
             mc_hash=61bb88e7435919834478ddd4d405a6de6d2c227079da5e8ee9655147398819a0 ;; \
      *) echo "Unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
    && curl -fL --retry 3 --output /usr/local/bin/minio \
      "https://github.com/minio/minio/releases/download/RELEASE.2025-04-22T22-12-26Z/minio.linux-${TARGETARCH}.RELEASE.2025-04-22T22-12-26Z" \
    && echo "$minio_hash  /usr/local/bin/minio" | sha256sum -c - \
    && curl -fL --retry 3 --output /usr/local/bin/mc \
      "https://github.com/minio/mc/releases/download/RELEASE.2025-04-16T18-13-26Z/mc.linux-${TARGETARCH}.RELEASE.2025-04-16T18-13-26Z" \
    && echo "$mc_hash  /usr/local/bin/mc" | sha256sum -c - \
    && chmod +x /usr/local/bin/minio /usr/local/bin/mc \
    && apk del curl

VOLUME /data
EXPOSE 9000 9001
ENTRYPOINT ["/usr/local/bin/minio"]
CMD ["server", "/data"]
