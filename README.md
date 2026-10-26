# GrowLog

Kleines Grow-Tagebuch als Web-App (PWA): Gewichte, Gießen in ml, Notizen, Verlaufsdiagramm
und fertiger Discord-Block (nur heute + gestern, mit Verlust seit dem letzten Wiegen gestern).

## Starten (Docker CLI)

    git clone https://github.com/iGridZ/Growlog.git && cd Growlog
    docker build -t growlog .
    docker run -d --name growlog --restart unless-stopped \
      -p 8087:8087 -v "$(pwd)/data:/data" growlog

Dann `http://<server-ip>:8087` öffnen. Beim allerersten Aufruf erscheint eine Einrichtungsseite, auf der du
den Admin-Zugang (Benutzername + Passwort) anlegst. **Alles Weitere wird im Web-UI eingestellt**, es sind keine
Umgebungsvariablen nötig. Daten liegen in `./data/growlog.db`.

## Fertiges Image statt selbst bauen

Nach jedem Push auf `main` baut GitHub Actions das Image und legt es in die GitHub Container Registry:

    docker pull ghcr.io/igridz/growlog:latest
    docker run -d --name growlog --restart unless-stopped -p 8087:8087 \
      -v "$(pwd)/data:/data" ghcr.io/igridz/growlog:latest

Ist das Paket privat, vorher `docker login ghcr.io` mit einem Token (read:packages), oder das Paket in den
GitHub-Einstellungen auf öffentlich stellen.

## Update

    docker pull ghcr.io/igridz/growlog:latest
    docker rm -f growlog
    # danach den docker run-Befehl von oben erneut ausführen (Daten bleiben in ./data)

Bestehende Daten einer älteren Version werden beim ersten Start automatisch dem ersten Admin zugeordnet.

## Konten und Einstellungen (alles im Web-UI)

Tab "Konto" (Kopfzeile oben rechts):

- **Jeder Nutzer:** Passwort ändern, eigenen Discord-Webhook hinterlegen, abmelden. Pflanzen, Gewichte, Gießen,
  Notizen und Zeiten sind pro Nutzer getrennt.
- **Profil teilen:** Standard ist privat. Mit dem Schalter "Profil teilen" sehen andere angemeldete Nutzer
  (nur lesen) deine Pflanzen, den Gewichtsverlauf und das Gießen. Notizen, Discord und Einstellungen bleiben privat.
  Freigegebene Profile erscheinen bei den anderen unter "Andere Profile".
- **Admin:** Benutzer anlegen, sperren, Rolle ändern, Passwort zurücksetzen, löschen; Einladungslinks erzeugen
  (7 Tage gültig, einmal nutzbar, Freunde registrieren sich damit selbst); Server-Einstellungen (Zeitzone,
  Betrieb hinter Reverse-Proxy); Datenbank-Backup und Wiederherstellung (ersetzt alle Konten).
- Anmeldung bleibt 90 Tage, nach 5 Fehlversuchen ist die IP 5 Minuten gesperrt.

Optionale Umgebungsvariablen (nur Startwerte, nichts davon ist nötig): `APP_USER` / `APP_PASSWORD` legen den
ersten Admin automatisch an (statt der Einrichtungsseite), `TZ_NAME`, `TRUST_PROXY`, `SECRET_KEY`
(sonst automatisch in `data/secret.key`), `PORT`, `DB_PATH`.

## Von außen / aufs Handy

1. In Nginx Proxy Manager einen Proxy-Host auf `<server-ip>:8087` anlegen und SSL aktivieren
   (läuft NPM im selben Docker-Netzwerk, geht auch `growlog:8087`).
2. Seite in Chrome am Pixel öffnen → Menü → "App installieren" / "Zum Startbildschirm hinzufügen".

## Pflanzen

Pflanzen legst du direkt in der App im Tab "Pflanzen" an (Name, optional Kürzel). Dort kannst du sie auch
umbenennen oder entfernen. Pflanzen mit Einträgen werden nur archiviert, der Verlauf bleibt erhalten.

## Gieß-Übersicht (Richtwerte)

Im Tab "Wiegen" zeigt die App pro Pflanze den Füllstand, den Verbrauch und einen Gießtermin mit ml-Vorschlag.

- **Verbrauch** wird aus deinen Messungen berechnet, aber nicht als Tagesdurchschnitt: Licht- und Dunkelphase werden
  getrennt betrachtet (Zeiten im Tab "Pflanzen" unter "Zeiten"), neuere Messungen zählen stärker (Halbwertszeit 2 Tage)
  und ein Wachstumstrend wird eingerechnet. Daraus entsteht eine Prognose in 15-Minuten-Schritten.
- **Ziel- und Schwellengewicht** kommen automatisch aus dem letzten Gießen (Ziel = erste Messung danach,
  Schwelle = Messung davor) oder werden im Tab "Pflanzen" von Hand gesetzt.
- **Gießtermin:** der letzte deiner Gießzeitpunkte (Standard 17:30), bevor die Schwelle vor dem nächsten erreicht würde.
- **ml-Vorschlag:** (Ziel − erwartetes Gewicht zum Gießzeitpunkt) / gemessenes Verhältnis g pro ml aus deinen bisherigen
  Gießvorgängen. Beim Eintippen eines Gewichts siehst du sofort die ml bis zum Ziel.

Das sind Richtwerte, der Fingertest entscheidet.

## Export und Backup

Im Tab "Konto" unter "Daten & Backup":

- CSV-Export für Gewichte, Gießen und Notizen (Excel-tauglich: Semikolon, Dezimalkomma, UTF-8).
- Nur Admin: Backup als `.db`-Datei herunterladen und über "Aus Backup wiederherstellen" wieder einspielen
  (vorher wird automatisch eine Kopie `vor-restore-…` angelegt).
- Automatisch: einmal pro Tag eine Kopie in `data/backups/` (die letzten 14 bleiben).

## Image aus GitHub (ghcr.io) in Portainer nutzen

1. GitHub → Profil → Packages → `growlog` → Package settings → Sichtbarkeit auf "Public" stellen
   (privat geht auch, dann in Schritt 2 Anmeldedaten mit einem Token `read:packages` eintragen).
2. Portainer → Registries → Add registry → Custom registry: Name `ghcr`, URL `ghcr.io`.
3. Bestehenden Container öffnen → Duplicate/Edit → Image `ghcr.io/igridz/growlog:latest` → Deploy the container (Replace).
   Ports, Umgebungsvariablen und Volumes bleiben erhalten, die Daten liegen im Volume bzw. Ordner `data`.
   Der Container-Port ist 8087 (nicht mehr 8080).
4. Spätere Updates: Container → Recreate → "Pull latest image version" aktivieren.
