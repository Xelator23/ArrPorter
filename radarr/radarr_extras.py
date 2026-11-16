import os
import json
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile

# Only library metadata and subtitles belong in Transit. Samples, SFV/RAR and
# other release extras are deliberately excluded.
EXTS = {'.nfo', '.srt', '.vtt', '.idx', '.sub', '.ass', '.ssa', '.sup'}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def scan(root):
    result = []
    def fail(error):
        raise error
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=fail):
        require(not any((Path(directory)/d).is_symlink() for d in dirs), 'Symlink im Extras-Verzeichnis')
        for name in names:
            p = Path(directory)/name
            if p.suffix.lower() in EXTS:
                require(p.is_file() and not p.is_symlink(), 'Extra ist keine reguläre Datei')
                result.append(p)
    return result

def validate_roots(source, dest):
    require(source.is_dir() and dest.is_dir(), 'Quell- oder Filmordner fehlt; keine NFO gelöscht')
    require(source.is_absolute() and dest.is_absolute() and source.resolve() == source and dest.resolve() == dest,
            'Quell- oder Zielpfad wird umgeleitet')
    require(source != dest and source not in dest.parents and dest not in source.parents,
            'Quelle und Ziel überlappen')
    require(source.parent != source and dest.parent != dest, 'Wurzelverzeichnis nicht erlaubt')

def sync(source, dest, manifest, video=None):
    source, dest = Path(source), Path(dest)
    validate_roots(source, dest)
    current = scan(source)  # Complete enumeration required even for zero extras.
    previous = [p for p in scan(dest) if p.suffix.lower() == '.nfo']
    previous_hashes = {p: digest(p) for p in previous}
    entries = []
    for file in current:
        rel = file.relative_to(source)
        require(not any(part.startswith('.') or '\\' in part or any(ord(c)<32 for c in part) for part in rel.parts),
                'Unsicherer Extras-Dateiname')
        target = dest/rel
        require(target.resolve() == target and not target.is_symlink(), 'Ziel wird umgeleitet')
        require(not target.exists() or target.is_file(), 'Ziel ist keine Datei')
        value = digest(file)
        if target.exists() and file.suffix.lower() != '.nfo':
            require(digest(target) == value, 'Namenskonflikt mit anderem Inhalt: ' + str(rel))
        entries.append((file, rel, target, value))
    # Stage every current extra on the destination filesystem before changing NFOs.
    with tempfile.TemporaryDirectory(prefix='.radarr-extras-', dir=dest) as temp:
        stage = Path(temp)
        for file, rel, target, value in entries:
            copy = stage/rel
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, copy)
            copy.chmod(0o644)
            require(digest(copy) == value and digest(file) == value, 'Quelle während Sicherung verändert')
        for file, rel, target, value in entries:
            target.parent.mkdir(parents=True, exist_ok=True)
            require(target.resolve() == target and not target.is_symlink(), 'Ziel wurde umgeleitet')
            if target in previous_hashes:
                require(digest(target) == previous_hashes[target], 'Alte NFO zwischenzeitlich verändert')
            if file.suffix.lower() == '.nfo':
                os.replace(stage/rel, target)
            elif not target.exists():
                os.link(stage/rel, target)
            require(digest(target) == value, 'Übernahme nicht verifiziert')
        keep = {target for _, _, target, _ in entries}
        if video and not any(target.suffix.lower() == '.nfo' for target in keep):
            sidecar = Path(video).with_suffix('.nfo')
            for old in previous:
                if old.parent == sidecar.parent and old.name.casefold() == sidecar.name.casefold():
                    keep.add(old)
                    print(f'Extras: NFO beim aktuellen Film erhalten (Originalname unbekannt): {old}', flush=True)
        for old in previous:
            if old not in keep:
                require(not old.is_symlink() and old.resolve() == old and digest(old) == previous_hashes[old],
                        'Alte NFO zwischenzeitlich verändert')
                old.unlink()
                print('Extras: Alte NFO entfernt: ' + str(old), flush=True)
        Path(manifest).write_text(json.dumps({'source':str(source),'destination':str(dest),
            'files':[{'path':str(target),'sha256':value} for _,_,target,value in entries]}), encoding='utf-8')
    print(f'Extras: {len(entries)} aktuelle Dateien übernommen.', flush=True)

if __name__ == '__main__':
    try:
        require(bool(os.environ.get('RADARR_EXTRAS_MANIFEST')), 'Nur über radarr-import-chain.sh starten')
        dest = os.environ['radarr_movie_path']
        preview = os.environ.get('RADARR_EXTRAS_PREVIEW')
        if sys.argv[1:] == ['--commit']:
            sync(os.environ['RADARR_EXTRAS_PREVIEW'], dest, os.environ['RADARR_EXTRAS_MANIFEST'],
                 os.environ.get('radarr_moviefile_path'))
        else:
            # Capture current download files even if Radarr later removes the source.
            source = Path(os.environ['radarr_moviefile_sourcefolder'])
            if preview:
                validate_roots(source, Path(dest))
            sync(source, preview or dest, os.environ['RADARR_EXTRAS_MANIFEST'], os.environ.get('radarr_moviefile_path'))
    except (ValueError, OSError) as error:
        raise SystemExit('ERROR: Extras: ' + str(error))
