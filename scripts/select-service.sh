#!/usr/bin/env bash
# Select the service-specific settings in a shared configuration.
if [[ "$1" == radarr ]]; then
    TRANSIT_BASE=${RADARR_TRANSIT_BASE:?RADARR_TRANSIT_BASE fehlt}
    DRY_RUN=${RADARR_DRY_RUN:-true}
    FILTER_CONFIG=${RADARR_FILTER_CONFIG:-${FILTER_CONFIG:-}}
    unset SONARR_API_KEY
else
    TRANSIT_BASE=${SONARR_TRANSIT_BASE:?SONARR_TRANSIT_BASE fehlt}
    DRY_RUN=${SONARR_DRY_RUN:-true}
    unset RADARR_API_KEY
fi
if [[ "$TRANSIT_BASE" != /* || "$(realpath -m -- "$TRANSIT_BASE")" == / ]]; then
    echo 'ERROR: Transit muss ein absoluter Unterordner sein.' >&2; return 1
fi
if [[ -n ${RADARR_TRANSIT_BASE:-} && -n ${SONARR_TRANSIT_BASE:-} ]]; then
    arr_radarr=$(realpath -m -- "$RADARR_TRANSIT_BASE") || return 1
    arr_sonarr=$(realpath -m -- "$SONARR_TRANSIT_BASE") || return 1
    if [[ "$arr_radarr/" == "$arr_sonarr/"* || "$arr_sonarr/" == "$arr_radarr/"* ]]; then
        echo 'ERROR: Radarr und Sonarr brauchen getrennte Transitverzeichnisse.' >&2; return 1
    fi
fi
if [[ "$DRY_RUN" != true && "$DRY_RUN" != false ]]; then
    echo 'ERROR: Probelauf muss true oder false sein.' >&2; return 1
fi
unset arr_radarr arr_sonarr
