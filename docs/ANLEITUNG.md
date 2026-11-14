# ArrPorter auf Unraid einrichten

## 1. Voraussetzungen und Pfade

Benötigt werden ein bereits funktionierender Radarr- bzw. Sonarr-Import, ein upPollo-Server mit aktivierter API und für den automatischen Cleanup qBittorrent mit Web-API. Radarr/Sonarr brauchen Lesezugriff auf den ursprünglichen Download und die importierte Datei; upPollo und qBittorrent müssen Transit und endgültiges Seeding-Ziel unter den erwarteten Pfaden sehen. Der Cleanup liest die datierten upPollo-Textlogs auf dem Host. Das mitgelieferte Setup für den kombinierten Cleanup setzt beide Arr-Container voraus.

Beispiel für Docker-Pfadzuordnungen. Container-Namen und Hostordner sind Beispiele; die Containerpfade in der mittleren Spalte werden von den Befehlen unten verwendet.

| Unraid-Host | Container | Dienste |
|---|---|---|
| `/mnt/user/appdata/scripts` | `/scripts` | Radarr und Sonarr; Skripte les-/ausführbar. Zum Einrichten ggf. schreibbar. |
| Ein gemeinsamer Datenordner, z. B. `/mnt/user/data` | `/data` | Download-Client, Arr, upPollo und qBittorrent. Rechte passend zur jeweiligen Aufgabe. |
| upPollo-Logverzeichnis | im Cleanup als **Hostpfad** eintragen | Tagesdateien `upPollo_api_YYYY-MM-DD.log`. |

Lege Bibliothek, Transit und Seeding-Ziel als **getrennte** Verzeichnisse an. Bibliothek und Transit müssen Hardlinks erlauben: am besten ein gemeinsames `/data`-Mount statt separater Docker-Mounts für Unterordner. Verschiedene Dateisysteme oder Mounts können `Invalid cross-device link` verursachen. Dieselben Containerpfade allein reichen nicht. Verzeichnisse vorab anlegen; die Arr-Dienstbenutzer brauchen passende Rechte. Keine Dateien aus einem produktiven Transit löschen, um einen Test zu wiederholen.

## 2. Skripte installieren

Auf dem Unraid-Host (Neuinstallation, Git erforderlich; alternativ GitHub-ZIP entpacken und den obersten Projektordner in `arrporter` umbenennen):

```bash
cd /mnt/user/appdata/scripts
git clone https://github.com/Xelator23/ArrPorter.git arrporter
cd arrporter
umask 077
for app in radarr sonarr; do
    cp -n "$app/uppollo.conf.example" "$app/uppollo.conf"
    cp -n "$app/filters.conf.example" "$app/filters.conf"
    chmod 600 "$app/uppollo.conf" "$app/filters.conf"
done
find radarr sonarr upload_cleanup -type f -name '*.sh' -exec chmod 755 {} +
```

**Dateibesitzer beachten:** Wenn du als root kopierst, sind Dateien mit Modus 600 für andere Benutzer unlesbar. Setze Besitzer der beiden lokalen Konfigurationsdateien pro App auf die tatsächliche UID/GID des jeweiligen Arr-Dienstes (im Container-Template prüfen). Keine universelle UID voraussetzen. Der manuelle Aufruf als Container-root ersetzt den Test in der Arr-Oberfläche nicht.

In beiden Arr-Containern benötigt: Bash, Python 3.10 oder neuer, curl, jq, flock (util-linux), GNU coreutils, find, grep und awk. Auf dem Unraid-Host: Docker-CLI, Bash, jq, awk, flock und GNU date. Auf Debian-basierten Containern lassen sich fehlende Pakete über die Paketverwaltung installieren; für Container-Neuerstellungen ins eigene Image oder dessen dokumentierten Init-Hook übernehmen.

## 3. Private Konfiguration

Bearbeite **nur die lokalen Kopien** `radarr/uppollo.conf`, `sonarr/uppollo.conf` und die zwei `filters.conf`. Sie sind ausführbare Bash-Konfiguration: Werte in einfache Anführungszeichen setzen, Schreibzugriff beschränken. Alle Adressen mit `.invalid` ersetzen. Zugangsdaten nie in Commits, Screenshots oder Fehlerberichte übernehmen.

| Einstellung | Wert |
|---|---|
| `UPPOLLO_URL` / `API_KEY` | Direkte URL inklusive `/api/upload` und Schlüssel des upPollo-Servers. |
| `RADARR_URL` / `RADARR_API_KEY` bzw. `SONARR_URL` / `SONARR_API_KEY` | Basisadresse der jeweiligen Arr-API, ohne `/api/v3`; Schlüssel unter Einstellungen → Allgemein. |
| `QBIT_URL`, `QBIT_USER`, `QBIT_PASS` | Basisadresse ohne abschließenden Slash und qBittorrent-Webzugang. Für Torrent-Quellprüfung und Cleanup. |
| `TRANSIT_BASE` | Eigener Transit pro App. Bei bestehenden Installationen mit offenen Jobs den bestehenden Pfad behalten. |
| `CLIENT_UPLOAD_BASE` | Tatsächlicher endgültiger Speicherpfad in qBittorrent; für die Arr-Container unter demselben absoluten Pfad sichtbar. |
| `SPECIAL_GROUP` / `CLIENT_SPECIAL_BASE` | Optional: Gruppe, deren Name auch die qBittorrent-Kategorie ist, und deren Speicherort. Leer lassen für das Standardziel. In qBittorrent den Speicherpfad dieser Kategorie und die Torrent-Verwaltung passend einstellen. |
| `BLACKHOLE_BASE` (Sonarr) | Oberordner für abgeschlossene Blackhole-Downloads, aus Sicht von Sonarr. Bei diesem Client ist die ursprüngliche Downloadseite nicht verfügbar. |
| `DRY_RUN` | Zum Start `true`. Erst nach Prüfung auf `false` stellen. |

Beispiel-Filter: `EXCLUDED_GROUPS=('FuN' 'FUN-Low')` sperrt **beide** Endungen unabhängig von der Schreibweise. Kein führender Bindestrich; `FuN` allein sperrt `-FUN-Low` nicht. Persönliche Gruppen, die der Encoder separat übergibt, hier ergänzen. Weitere Optionen: gesperrte Quellen (`EXCLUDED_INDEXERS`), NFO-Pflicht je Gruppe, erlaubte Auflösungen und gesperrte Tags. Mindestens eine ausdrücklich erlaubte Auflösung muss erkannt werden. Eigene Filter vor dem Livebetrieb prüfen.

## 4. Arr-Verbindungen und erster Import

Unter **Einstellungen → Connect/Verbindungen → Custom Script** einrichten:

| Dienst | Skriptpfad im Container | Ereignisse |
|---|---|---|
| Radarr | `/scripts/arrporter/radarr/radarr-import-chain.sh` | On Import und On Upgrade (je nach UI „On File Import/Upgrade“). |
| Sonarr | `/scripts/arrporter/sonarr/sonarr-uppollo.sh` | On Grab, On File Import, On File Upgrade und On Import Complete. |

Kein zweites Extras-Skript parallel ausführen. **Test** führt die lokale Prüfung und einen API-Verbindungstest ohne Medienpfad aus; es entsteht kein Upload. Dabei wird die erwartete Validierungsantwort von `/api/upload/check` ausgewertet und zwischen akzeptiertem Schlüssel und deaktivierter Authentifizierung unterschieden. Nicht jede API-Version liefert dieselbe Antwort. Das ist keine Tracker-Vorprüfung eines konkreten Releases. Manuell auf dem Host:

```bash
docker exec -e radarr_eventtype=Test Radarr bash /scripts/arrporter/radarr/radarr-import-chain.sh
docker exec -e sonarr_eventtype=Test Sonarr bash /scripts/arrporter/sonarr/sonarr-uppollo.sh
```

1. Mit `DRY_RUN=true` ein Release importieren. Unter `TRANSIT_BASE/_logs/` nach `DRY RUN OK`, `SKIP` oder einer Fehlerursache sehen. Es erfolgt kein Upload. **Radarr ersetzt beim Import vorhandene Release-NFOs durch die aktuellen; ist die Quelle nachweislich NFO-frei, werden alte NFOs aus dem Filmordner entfernt.** Keine separat generierten oder handgepflegten NFOs mit dieser Funktion mischen. Sonarr speichert Event- und Extras-Kopien auch im Probelauf.
2. Release-Name, Anzahl, Extras und Ziel prüfen. In Sonarr ist erst die Sammelmeldung für ein Staffelpack maßgeblich. Kein aktuelles Extra aus einem alten Bibliotheksrelease hinzumischen.
3. In der gewünschten App `DRY_RUN=false` setzen. Bei einem neuen Import bedeutet `HTTP 2xx` (Radarr) bzw. `QUEUED` (Sonarr) nur die Annahme der Anfrage. Den tatsächlichen Erfolg in upPollo und qBittorrent prüfen.
4. Eine MKV ohne Extras darf bei Radarr für snk, WeebPinn und DRiFTKiNG direkt übergeben werden; bei Sonarr zusätzlich für ToonsHub und Pudding-sama. Andere Releases bekommen einen Release-Ordner. Die Namensregel ersetzt keine Gruppenfreigabe.

## 5. Cleanup separat aktivieren

Lokal `upload_cleanup/settings.conf.example` nach `upload_cleanup/settings.conf` kopieren; Container-Namen und **Hostpfad** der upPollo-Logs setzen. Dann:

```bash
cd /mnt/user/appdata/scripts/arrporter/upload_cleanup
bash setup.sh
bash cleanup.sh --check
```

Setup übernimmt nur die für den Cleanup erforderlichen Werte aus den beiden Importkonfigurationen in `radarr/cleanup.conf` und `sonarr/cleanup.conf` unterhalb des Cleanup-Ordners. Bereits vorhandene Dateien werden nicht überschrieben. Nach Passwort-, Transit- oder Zieländerungen diese privaten Kopien ebenfalls aktualisieren. Sobald die erste Statusdatei pro App existiert, kann `bash test.sh` die Tests in den tatsächlichen Containern ausführen.

Bei `CHECK OK` sind die geprüften Zielinhalte mit dem Transit abgeglichen. `PENDING` bedeutet: noch kein eindeutiger Abschluss. Große Uploads brauchen Zeit; es gibt keine pauschale Löschfrist. Mehrdeutige Logsequenzen, fehlende Ziele oder ein Torrent, der noch den Transit verwendet, stoppen die Bereinigung. Ein eindeutig abgelehntes Duplikat wird separat geprüft. Bei plausibler Check-Ausgabe denselben Aufruf mit `--apply` ausführen. Für Unraid **User Scripts**:

```bash
#!/bin/bash
exec bash /mnt/user/appdata/scripts/arrporter/upload_cleanup/cleanup.sh --apply
```

Als Custom-Zeitplan `*/10 * * * *` eintragen. Nur **einen** Cleanup für dieselben Statusdateien laufen lassen. Sperren im Projekt verhindern Überlappungen innerhalb derselben Installation.

## 6. Diagnose und Wiederanlauf

| Beobachtung | Nächster Schritt |
|---|---|
| Verbindungstest grün, kein Upload im Log | Beim Test richtig. Bei echtem Import Ereignisse, `DRY_RUN` und das Joblog prüfen. Radarr zeigt erfolgreiche Skriptausgaben im Debug-Log. |
| Quelle verschwunden, Extras unbekannt | Prüfen, ob Grab, Upgrade und Einzelimport aktiviert waren. Sonarr: Bei vorhandenem gespeicherten Event zunächst nur einen Replay mit `--dry-run` ausführen. |
| Sonarr-Staffel unvollständig | History und erwartete Episoden derselben Download-ID vergleichen. Bei Blackhole muss die Staffelliste zum Pack passen. Nicht mehrere Download-IDs zusammenführen. |
| Ziel existiert, Timeout oder Status unklar | Nicht erneut senden oder Dateien löschen. Erst upPollo-Tageslog, Tracker und qBittorrent prüfen. `bash cleanup.sh --diagnose` liest lokale Statusdateien und Torrent-Zuordnungen. Diagnoseausgaben enthalten lokale Pfade und Release-Namen. |
| qBittorrent zeigt andere Kategorie/anderen Pfad | Tatsächlichen Speicherpfad und Automatik in qBittorrent prüfen; Container-Mounts und beide Konfigurationskopien vergleichen. |
| Permission denied / Hardlink schlägt fehl | Ausführungsrecht des Hooks, Leserechte auf Konfiguration, UID/GID, Schreibrechte und gemeinsames Dateisystem prüfen. |

Beispiel für einen **Sonarr**-Replay: `EVENT` durch den tatsächlichen Containerpfad unter `TRANSIT_BASE/_events/<job>/latest-event.json` ersetzen:

```bash
docker exec Sonarr bash /scripts/arrporter/sonarr/sonarr-uppollo.sh --replay "$EVENT" --dry-run
```

`--saved-extras-confirmed` nur wenn der Betreiber die Vollständigkeit der **vorhandenen Sicherung** unabhängig kennt; `--no-extras-confirmed` ausschließlich bei bestätigt extrasfreiem Download. Kein genereller Fallback. Ein bestehender Status schützt vor doppelter Übergabe.

**Administrative Archivierung** ist optional und gehört nicht in den Zeitplan. Nur nach manueller Diagnose eines bereits fehlenden Transitpfads: lokal `upload_cleanup/reconcile-jobs.json` als Liste mit `application` (Radarr/Sonarr), `job`, `release`, `path` und `submittedAt` exakt aus dem verifizierten Status anlegen. `bash reconcile.sh --check` auswerten, erst danach `--apply`. Originalstatus wird gesichert; `archived` behauptet keinen erfolgreichen Upload. Das Projekt liefert keine echten Job-IDs mit.

## 7. Updates und bestehende Installationen

Bei Neuinstallationen per Git: lokalen Konfigurations- und Statusbestand sichern, `git pull --ff-only` ausführen, neue Beispielwerte mit lokaler Konfiguration vergleichen und Verbindungstests wiederholen. Lokale Konfigurationen, Logs und Schlüssel sind ignoriert. Keine Live-Konfiguration mit `git add -f` veröffentlichen.

Bei einer bestehenden Installation den neuen Ordner **parallel** anlegen, ohne eine zweite Live-Verbindung oder einen zweiten Cleanup zu aktivieren. Die bisherige Sperrliste, bisherigen Transitbasen und Zielpfade lokal übernehmen. Früher gruppenspezifisch benannte Zielvariablen durch `SPECIAL_GROUP` und `CLIENT_SPECIAL_BASE` ersetzen, auch für ältere offene Jobs. In einem ruhigen Zeitfenster die **vorhandenen** Arr-Verbindungen auf die neuen Skriptpfade umstellen. Nur nach bestandenem Test den bestehenden User-Scripts-Aufruf umstellen. Die Rückkehr erfolgt durch Zurückstellen dieser Pfade; nicht die Transit- oder Statusordner leeren.
