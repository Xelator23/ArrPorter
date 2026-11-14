#!/usr/bin/env python3
"""Verified cleanup for file-shaped Sonarr transits after confirmed upload completion."""
import argparse
import os
from pathlib import Path, PurePosixPath
import re
import sys

import cleanup_support as s
import cleanup_duplicates as d


def target_candidates(config, state, name):
    special_group = (config.get('SPECIAL_GROUP') and state['release'].upper().endswith('-' + config['SPECIAL_GROUP'].upper()))
    key = 'CLIENT_SPECIAL_BASE' if special_group else 'CLIENT_UPLOAD_BASE'
    base = Path(config.get(key) or ('/data/torrents/special' if special_group else '/data/torrents/upload'))
    s.require(base.is_absolute() and base != base.parent, 'Ungültige Zielbasis')
    d.plain(base, directory=True)
    # Normally upPollo copies the MKV directly. A verified scene/NFO lookup may
    # make upPollo stage it in a named directory; allow only these exact paths.
    return base, {base / name, base / state['transitFolder'] / name,
                  base / s.safe_part(PurePosixPath(name).stem) / name}


def find_target(config, state, dest, source, expected_hash=None):
    _, name = s.transit_spec(state)
    base, candidates = target_candidates(config, state, name)
    transit_base = dest.parent
    s.require(base != transit_base and base not in transit_base.parents
              and transit_base not in base.parents, 'Transit und Zielbasis überlappen')
    infos = d.qbit_info(config)
    d.no_torrent_uses(dest, infos)
    matches = []
    for info in infos:
        content = Path(info['content_path'])
        possible = [p for p in candidates if content == p or (p.parent != base and content == p.parent)]
        if not possible:
            continue
        s.require(info.get('progress') == 1 and info.get('amount_left') == 0
                  and info.get('state') in {'uploading', 'stalledUP', 'forcedUP', 'queuedUP', 'pausedUP', 'stoppedUP'},
                  'Zieltorrent ist nicht vollständig oder wird gerade geprüft/verschoben')
        torrent_hash = info.get('hash', '')
        s.require(re.fullmatch(r'(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})', torrent_hash) is not None,
                  'Ungültiger Ziel-Torrent-Hash')
        files = d.qbit_files(config, torrent_hash)
        s.require(isinstance(files, list) and files and all(isinstance(f, dict) and f.get('progress') == 1 for f in files),
                  'Torrent-Dateiliste ist nicht vollständig')
        for target in possible:
            for item in files:
                rel = PurePosixPath(item.get('name', ''))
                s.require(rel.parts and not rel.is_absolute(), 'Ungültiger Torrent-Dateipfad')
                for part in rel.parts:
                    s.safe_part(part)
                if Path(info['save_path']) / str(rel) != target:
                    continue
                d.plain(target)
                s.require(item.get('size') == target.stat().st_size, 'Zieldateigröße widerspricht Torrent')
                d.verify_pair(target, source)
                matches.append((torrent_hash, target))
    s.require(len(matches) == 1, 'Keine eindeutige vollständige Zieldatei in qBittorrent')
    torrent_hash, target = matches[0]
    s.require(expected_hash is None or torrent_hash == expected_hash, 'Zieltorrent hat sich geändert')
    return torrent_hash, target


def cleanup(config, job, apply=False):
    s.require(re.fullmatch('[a-f0-9]{64}', job) is not None, 'Ungültige Job-ID')
    base = Path(config['TRANSIT_BASE'])
    s.require(base.is_absolute() and base != base.parent, 'Ungültige Transitbasis')
    d.plain(base, directory=True)
    d.plain(base / '_state', directory=True)
    path = base / '_state' / (job + '.json')
    with s.lock(base / '_state' / (job + '.lock')):
        state = d.read_json(path)
        if state.get('status') == 'cleaned':
            print('Bereits bereinigt.', flush=True)
            return
        s.require(state.get('status') in {'accepted_unverified', 'cleanup_started'}, 'Auftrag erlaubt keinen Upload-Cleanup')
        kind, name = s.transit_spec(state)
        s.require(kind == 'file' and state.get('application') == 'sonarr', 'Kein Sonarr-Dateitransit')
        dest = base / name
        s.require(state['path'] == str(dest), 'Statuspfad stimmt nicht mit Transit überein')
        with s.transit_locks(base, state):
            for other_path in (base / '_state').glob('*.json'):
                if other_path == path or not re.fullmatch('[a-f0-9]{64}', other_path.stem):
                    continue
                other = d.read_json(other_path)
                if other.get('path') == str(dest):
                    s.require(other.get('status') in {'cleaned', 'skipped_duplicate', 'superseded', 'archived'},
                              'Ein weiterer offener Auftrag verwendet dieselbe Transitdatei')
            pairs = d.verified_pairs(base, job, state, dest)
            source = pairs[name]
            exists = os.path.lexists(dest)
            s.require(exists or state['status'] == 'cleanup_started', 'Transitdatei fehlt')
            if exists:
                d.verify_pair(dest, source)
            torrent_hash, target = find_target(config, state, dest, source)
            print(f'CHECK OK: Einzelne MKV am Ziel verifiziert. Torrent={torrent_hash}; Ziel={target}', flush=True)
            if not apply:
                print('Nichts gelöscht.', flush=True)
                return
            state.update(status='cleanup_started', targetTorrentHash=torrent_hash,
                         cleanupUpdatedAt=s.now(), completionConfirmation=config.get('CLEANUP_CONFIRMATION') or 'operator')
            s.write_json(path, state)
            _, refreshed = find_target(config, state, dest, source, torrent_hash)
            s.require(refreshed == target, 'Zieldateipfad hat sich geändert')
            if exists:
                d.verify_pair(dest, source)
                dest.unlink()
            state.update(status='cleaned', cleanupUpdatedAt=s.now())
            s.write_json(path, state)
            print(f'CLEANED: Nur die verifizierte Transit-MKV wurde entfernt: {dest}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--apply-confirmed', action='store_true')
    parser.add_argument('job')
    args = parser.parse_args()
    cleanup(dict(os.environ), args.job, args.apply_confirmed)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        detail = s.error_detail(error)
        raise SystemExit('ERROR: ' + detail) from None
