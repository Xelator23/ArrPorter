# Radarr mit ArrPorter einrichten

1. [ArrPorter installieren oder vorhandene Konfigurationen übernehmen](ANLEITUNG.md). In der zentralen `arrporter.conf` stehen `UPPOLLO_URL`, `API_KEY`, `RADARR_URL` und `RADARR_API_KEY`. Den Radarr-Schlüssel findest du unter **Einstellungen → Allgemein → Sicherheit**. Der Dienst muss die eingetragenen Adressen erreichen können.
2. Trage den bisherigen oder gewünschten `RADARR_TRANSIT_BASE` ein und setze für den ersten Durchlauf `RADARR_DRY_RUN=true`. Bearbeite die Release- und Quellenfilter in `radarr/filters.conf`.
3. Öffne **Einstellungen → Verbindungen (Connect) → + → Custom Script**. Verwende bei der dokumentierten Unraid-Installation `/scripts/arrporter/radarr/radarr-import-chain.sh` als Pfad. Bei einer nativen Installation verwende den absoluten Skriptpfad aus Sicht von Radarr.
4. Aktiviere **On Import** und **On Upgrade**. Der Hook übernimmt beim Import die Extras des aktuellen Downloads. Bei einem Upgrade kann er alte NFOs im Filmordner entfernen. Verwende für den ersten Import einen Film, dessen NFOs aus dem aktuellen Download stammen.
5. Entferne `nfo` bzw. `.nfo` aus **Import Extra Files** in Radarr. ArrPorter übernimmt die Original-NFO aus dem aktuellen Download über seinen Extras-Import.
6. Klicke **Test** und speichere die Verbindung. Der Test fragt die upPollo-API ohne Upload ab. Für die Textausgabe kannst du auf Unraid denselben Test im Container starten.

```bash
docker exec -e radarr_eventtype=Test Radarr bash /scripts/arrporter/radarr/radarr-import-chain.sh
```

Bei einer nativen Installation im Projektverzeichnis als Radarr-Dienstbenutzer:

```bash
radarr_eventtype=Test bash radarr/radarr-import-chain.sh
```

**Erster Import.** Importiere ein Release und prüfe das Joblog unter dem konfigurierten Transitpfad in `_logs/`. `DRY RUN OK` bestätigt den Probelauf. Bei `SKIP` steht der Grund im Log. Prüfe, ob der ursprüngliche Videoname, die NFO des aktuellen Downloads und die Untertitel unter `Subs/` zum Film passen. Setze danach `RADARR_DRY_RUN=false`, wenn neue Importe an upPollo übergeben werden sollen. Prüfe den ersten Liveauftrag in upPollo und im Download-Client.

[Gemeinsame Konfiguration, Cleanup und Fehlerdiagnose](ANLEITUNG.md)

### srrDB im Probelauf prüfen

Lass `RADARR_DRY_RUN=true` gesetzt und importiere einen Film. Der Verbindungstest prüft die upPollo-API. Erst der Filmimport liefert den Release-Namen für die srrDB-Abfrage. Bei einem bekannten Release bestätigt `srrDB OK` im Joblog, dass Videodatei und Extras unter den erwarteten Namen und Pfaden vorliegen und ihre CRC32 übereinstimmt. Bei `CRC32 weicht ab` findest du den Dateinamen sowie den srrDB-Wert und deinen lokalen Wert in derselben Zeile. [Alle Prüfergebnisse und ihre Bedeutung](../README.md#srrdb-prüfung-vor-dem-upload)
