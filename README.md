![ArrPorter – zwei Importwege, eine Übergabe](assets/arrporter.svg)

# ArrPorter

**Originalname bleibt. Release kommt an.** Radarr und Sonarr dürfen ihre Bibliothek umbenennen. ArrPorter bereitet das importierte Video über Hardlinks unter seinem ursprünglichen Namen für upPollo vor. Ein eigener Cleanup entfernt den Transit erst nach Prüfung. Entwickelt für Unraid mit Docker und gemeinsamem Linux-Dateisystem.

| Eingang | Was ArrPorter macht |
|---|---|
| Filmimport oder Upgrade | Aktuelle Extras übernehmen, Release prüfen und einen Auftrag übergeben. Untertitel inklusive IDX/SUB liegen im Transit unter `Subs/`; SFV wird nicht übernommen. |
| Einzelne Folge | Ursprünglichen Namen über die Sonarr-Historie rekonstruieren. Für bestimmte Gruppen kann genau eine MKV ohne Extras direkt übergeben werden. |
| Staffelpack | Einzelmeldungen sammeln, Vollständigkeit prüfen und das Pack einmal übergeben. Echte Episoden-Unterordner bleiben erhalten; eine doppelte identische äußere Ordnerebene wird normalisiert. |
| Cleanup | Tageslogs, Jobstatus und qBittorrent abgleichen. Bestätigte Duplikate getrennt behandeln; bei fehlendem oder widersprüchlichem Nachweis bleibt der Transit erhalten. |

**[Installation und Betrieb Schritt für Schritt →](docs/ANLEITUNG.md)** · [Beispiel: Radarr](radarr/uppollo.conf.example) · [Beispiel: Sonarr](sonarr/uppollo.conf.example) · [Cleanup-Einstellungen](upload_cleanup/settings.conf.example)

Es sind keine Zugangsdaten oder Produktionskonfigurationen enthalten. Die Beispiele starten mit `DRY_RUN=true`. Der Radarr-Extras-Import und die Sonarr-Extras-Sicherung arbeiten auch im Probelauf. Für den ersten Test ein entbehrliches Release verwenden.

ArrPorter ist eigenständig. Die Installation von upPollo, Tracker, Indexer und Download-Clients gehört nicht zum Paket.
