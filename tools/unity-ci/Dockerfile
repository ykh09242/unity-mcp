# GameCI layout/wrapper recipe pinned to 75891f03fd6c243db6198f285313118cef0fc806.
# The manifest supplies the immutable unityci/base digest (Ubuntu 22.04).
ARG PREVIEW_BASE_IMAGE=unityci/base:ubuntu-3.2.2@sha256:076fe28aa9863a2466c69d4dce5af51e25988b31e4d9ed110f4025f6fd2f98d3
FROM ${PREVIEW_BASE_IMAGE}

ARG UNITY_VERSION
ARG EDITOR_URL
ARG EDITOR_MD5
ARG EDITOR_SIZE
ARG IMAGE_PURPOSE=compile
ARG MODULE_DESTINATION=Editor/Data/PlaybackEngines/LinuxStandaloneSupport
ARG IL2CPP_URL
ARG IL2CPP_MD5
ARG IL2CPP_SIZE
ARG SERVER_URL
ARG SERVER_MD5
ARG SERVER_SIZE
ENV UNITY_VERSION=${UNITY_VERSION}

RUN set -eu; \
    archive=/tmp/unity-editor.tar.xz; \
    curl --fail --location --retry 3 --retry-all-errors --retry-max-time 600 \
        --connect-timeout 30 --max-time 600 --proto '=https' --proto-redir '=https' \
        "$EDITOR_URL" --output "$archive"; \
    test "$(stat -c%s "$archive")" = "$EDITOR_SIZE"; \
    printf '%s  %s\n' "$EDITOR_MD5" "$archive" | md5sum -c -; \
    mkdir -p "$UNITY_PATH"; \
    tar -xJf "$archive" -C "$UNITY_PATH"; \
    test -x "$UNITY_PATH/Editor/Unity"; \
    rm "$archive"; \
    if [ "$IMAGE_PURPOSE" = tests ]; then \
        destination="$UNITY_PATH/$MODULE_DESTINATION"; \
        mkdir -p "$destination"; \
        for module in IL2CPP SERVER; do \
            case "$module" in \
                IL2CPP) module_url="$IL2CPP_URL"; module_md5="$IL2CPP_MD5"; module_size="$IL2CPP_SIZE" ;; \
                SERVER) module_url="$SERVER_URL"; module_md5="$SERVER_MD5"; module_size="$SERVER_SIZE" ;; \
            esac; \
            archive="/tmp/unity-$module.tar.xz"; \
            curl --fail --location --retry 3 --retry-all-errors --retry-max-time 600 \
                --connect-timeout 30 --max-time 600 --proto '=https' --proto-redir '=https' \
                "$module_url" --output "$archive"; \
            test "$(stat -c%s "$archive")" = "$module_size"; \
            printf '%s  %s\n' "$module_md5" "$archive" | md5sum -c -; \
            tar -xJf "$archive" -C "$destination"; \
            rm "$archive"; \
        done; \
        test -d "$destination/Variations"; \
        test -n "$(find "$destination/Variations" -type f -print -quit)"; \
    else \
        test "$IMAGE_PURPOSE" = compile; \
    fi; \
    printf '%s\n' '#!/bin/bash' \
        'shopt -s nullglob' \
        'for script in /usr/bin/unity-editor.d/*.sh; do source "$script"; done' \
        'xvfb-run -ae /dev/stdout "$UNITY_PATH/Editor/Unity" -batchmode "$@"' \
        > /usr/bin/unity-editor; \
    chmod 755 /usr/bin/unity-editor; \
    touch /root/.bashrc
