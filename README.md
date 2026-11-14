![ArrPorter – zwei Importwege, eine Übergabe](assets/arrporter.svg)

# ArrPorter

**Originalname bleibt. Release kommt an.** Radarr und Sonarr können Videos beim Import umbenennen. ArrPorter legt im Transit einen Hardlink mit dem ursprünglichen Dateinamen an und übergibt das Release an upPollo. Die Importskripte brauchen kein Docker; Bibliothek und Transit müssen Hardlinks auf demselben Dateisystem erlauben.

| Eingang | Was ArrPorter macht |
|---|---|
| Filmimport oder Upgrade | Aktuelle Extras übernehmen, Release prüfen und einen Auftrag übergeben. Untertitel inklusive IDX/SUB liegen im Transit unter `Subs/`; SFV wird nicht übernommen. |
| Einzelne Folge | Ursprünglichen Namen über die Sonarr-Historie rekonstruieren. Für bestimmte Gruppen kann genau eine MKV ohne Extras direkt übergeben werden. |
| Staffelpack | Einzelmeldungen sammeln, Vollständigkeit prüfen und das Pack einmal übergeben. Echte Episoden-Unterordner bleiben erhalten; eine doppelte identische äußere Ordnerebene wird normalisiert. |
| Optionaler Cleanup für Unraid | Prüft Tageslogs, Jobstatus und qBittorrent vor der Bereinigung. Bei unklarem Ergebnis bleibt der Transit erhalten. Das mitgelieferte Hostskript ruft die Prüfungen per `docker exec` in den Arr-Containern auf. |

**[Installation und Betrieb Schritt für Schritt →](docs/ANLEITUNG.md)** · [Beispiel: Radarr](radarr/uppollo.conf.example) · [Beispiel: Sonarr](sonarr/uppollo.conf.example) · [Cleanup-Einstellungen](upload_cleanup/settings.conf.example)

Es sind keine Zugangsdaten oder Produktionskonfigurationen enthalten. Die Beispiele starten mit `DRY_RUN=true`. Der Radarr-Extras-Import und die Sonarr-Extras-Sicherung arbeiten auch im Probelauf. Für den ersten Test ein entbehrliches Release verwenden.

ArrPorter ist eigenständig. Die Installation von upPollo, Tracker, Indexer und Download-Clients gehört nicht zum Paket.
