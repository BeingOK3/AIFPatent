#!/bin/sh
set -eu

secret_source="${AIFPATENT_PROVIDER_CREDENTIALS_SOURCE:-}"
secret_target="${AIFPATENT_PROVIDER_CREDENTIALS:-}"

if [ -n "$secret_source" ]; then
    if [ ! -r "$secret_source" ]; then
        echo "Provider credentials source is not readable: $secret_source" >&2
        exit 1
    fi
    if [ -z "$secret_target" ]; then
        echo "AIFPATENT_PROVIDER_CREDENTIALS must name the runtime credential path" >&2
        exit 1
    fi
    install -d -m 0700 -o 10001 -g 10001 "$(dirname "$secret_target")"
    install -m 0400 -o 10001 -g 10001 "$secret_source" "$secret_target"
fi

exec setpriv --reuid=10001 --regid=10001 --init-groups "$@"
