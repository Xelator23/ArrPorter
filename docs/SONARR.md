# Sonarr mit ArrPorter einrichten

1. [ArrPorter installieren oder vorhandene Konfigurationen übernehmen](ANLEITUNG.md). In der zentralen `arrporter.conf` stehen `UPPOLLO_URL`, `API_KEY`, `SONARR_URL` und `SONARR_API_KEY`. Den Sonarr-Schlüssel findest du unter **Einstellungen → Allgemein → Sicherheit**. Der Dienst muss die eingetragenen Adressen erreichen können.
2. Trage den bisherigen oder gewünschten `SONARR_TRANSIT_BASE` ein und setze für den ersten Durchlauf `SONARR_DRY_RUN=true`. Bearbeite die Release- und Quellenfilter in `sonarr/filters.conf`.
3. Öffne **Einstellungen → Verbindungen (Connect) → + → Custom Script**. Verwende bei der dokumentierten Unraid-Installation `/scripts/arrporter/sonarr/sonarr-uppollo.sh` als Pfad. Bei einer nativen Installation verwende den absoluten Skriptpfad aus Sicht von Sonarr.
4. Aktiviere **On Grab**, **On File Import**, **On File Upgrade** und **On Import Complete**. Der Grab und die Einzelmeldungen liefern die Zuordnung der Folgen. Bei einem Staffelpack löst erst der abgeschlossene Import die gemeinsame Übergabe aus.
5. Entferne `nfo` bzw. `.nfo` aus **Import Extra Files** in Sonarr. ArrPorter sichert die Original-NFO mit den Extras des aktuellen Downloads für die Übergabe an upPollo.
6. Klicke **Test** und speichere die Verbindung. Der Test fragt die upPollo-API ohne Upload ab. Für die Textausgabe kannst du auf Unraid denselben Test im Container starten.

```bash
docker exec -e sonarr_eventtype=Test Sonarr bash /scripts/arrporter/sonarr/sonarr-uppollo.sh
```

Bei einer nativen Installation im Projektverzeichnis als Sonarr-Dienstbenutzer:

```bash
sonarr_eventtype=Test bash sonarr/sonarr-uppollo.sh
```

**Erster Import.** Importiere ein Release und prüfe das Joblog unter dem konfigurierten Transitpfad in `_logs/`. `DRY RUN OK` bestätigt den Probelauf. Bei `SKIP` steht der Grund im Log. Prüfe bei einer Einzelfolge die Anzahl der Videos und bei einem Staffelpack die Vollständigkeit aller erwarteten Folgen. Auch ein Pack mit nur einer NFO ist möglich. Setze danach `SONARR_DRY_RUN=false`, wenn neue Importe an upPollo übergeben werden sollen. Prüfe den ersten Liveauftrag in upPollo und im Download-Client.

[Gemeinsame Konfiguration, Cleanup und Fehlerdiagnose](ANLEITUNG.md)

### srrDB im Probelauf prüfen

Lass `SONARR_DRY_RUN=true` gesetzt und importiere eine Folge oder ein Staffelpack. Die srrDB-Abfrage läuft bei **On Import Complete**. Für ein Pack prüft ArrPorter die geplanten Folgen und Extras gemeinsam anhand des Release-Eintrags. Führt srrDB etwa eine NFO für das gesamte Pack auf, wird genau diese an ihrem ursprünglichen Pfad erwartet. Im Joblog bestätigt `srrDB OK` die Namen, Pfade und CRC32-Prüfsummen. [Alle Prüfergebnisse und ihre Bedeutung](../README.md#srrdb-prüfung-vor-dem-upload)
