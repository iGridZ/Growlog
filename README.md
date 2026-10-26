# GrowLog

Kleines Grow-Tagebuch als Web-App (PWA): Gewichte, Gießen in ml, Notizen, Verlaufsdiagramm
und fertiger Discord-Block (nur heute + gestern, mit Verlust seit dem letzten Wiegen gestern).

## Starten (Docker CLI)

    git clone https://github.com/iGridZ/Growlog.git && cd Growlog
    docker build -t growlog .
    docker run -d --name growlog --restart unless-stopped \
      -p 8087:8087 \
      -v "$(pwd)/data:/data" \
      -e TZ_NAME=Europe/Berlin \
      -e APP_USER='grow' \
      -e APP_PASSWORD='bitte-aendern' \
      -e DISCORD_WEBHOOK_URL='' \
      growlog

Dann `http://<server-ip>:8087` öffnen. Daten liegen in `./data/growlog.db` (Backup = diese Datei kopieren).

## Fertiges Image statt selbst bauen

Nach jedem Push auf `main` baut GitHub Actions das Image und legt es in die GitHub Container Registry.
Einmalig im Repo unter Actions den Workflow `build-image` laufen lassen, danach auf dem Server:

    docker pull ghcr.io/igridz/growlog:latest
    docker run -d --name growlog --restart unless-stopped -p 8087:8087 \
      -v "$(pwd)/data:/data" -e APP_USER='grow' -e APP_PASSWORD='bitte-aendern' \
      ghcr.io/igridz/growlog:latest

Ist das Paket privat, vorher `docker login ghcr.io` mit einem Token (read:packages), oder das Paket in den
GitHub-Einstellungen auf öffentlich stellen.

## Update

    git pull
    docker build -t growlog .
    docker rm -f growlog
    # danach den docker run-Befehl von oben erneut ausführen (Daten bleiben in ./data)

## Einstellungen (Umgebungsvariablen)

- `APP_USER` / `APP_PASSWORD`: Zugangsdaten für die Anmeldeseite (Standard-Benutzer `grow`). Die Seite ist ein normales
  Formular, Bitwarden bietet Speichern und Ausfüllen an. Nach dem Login bleibst du 90 Tage angemeldet.
  Nach 5 Fehlversuchen ist die IP 5 Minuten gesperrt. Ein neues Passwort meldet alle Geräte ab. Leer = kein Login.
- `SECRET_KEY`: optional. Ohne Angabe wird beim ersten Start ein Schlüssel in `data/secret.key` erzeugt.
- `TRUST_PROXY`: Standard `1` (App steht hinter Nginx Proxy Manager, echte IP und https werden übernommen).
  Bei direktem Zugriff ohne Proxy auf `0` setzen.
- `DISCORD_WEBHOOK_URL`: in Discord unter Kanal → Integrationen → Webhooks erzeugen. Aktiviert den Knopf "An Discord senden".
- `TZ_NAME`: Zeitzone für Anzeige und Discord-Text (Standard Europe/Berlin).

## Von außen / aufs Handy

1. In Nginx Proxy Manager einen Proxy-Host auf `<server-ip>:8087` anlegen und SSL aktivieren
   (läuft NPM im selben Docker-Netzwerk, geht auch `growlog:8087`).
2. Seite in Chrome am Pixel öffnen → Menü → "App installieren" / "Zum Startbildschirm hinzufügen".

## Pflanzen

Pflanzen legst du direkt in der App im Tab "Pflanzen" an (Name, optional Kürzel). Dort kannst du sie auch
umbenennen oder entfernen. Pflanzen mit Einträgen werden nur archiviert, der Verlauf bleibt erhalten.

## Gieß-Übersicht (Richtwerte)

Im Tab "Wiegen" zeigt die App pro Pflanze, wie voll der Topf noch ist, den Verbrauch pro Tag und wann die
Schwelle voraussichtlich erreicht ist. Ziel- und Schwellengewicht werden automatisch aus dem letzten Gießen
übernommen (Ziel = erste Messung nach dem Gießen, Schwelle = Messung davor). Im Tab "Pflanzen" kannst du beide
Werte selbst festlegen. Beim Eintragen eines Gewichts siehst du sofort die ml bis zum Ziel, im Tab "Gießen"
übernimmt ein Tipp auf "Vorschlag" die Menge. Das sind Richtwerte, der Fingertest entscheidet.

## Export und Backup

Im Tab "Pflanzen" unter "Daten & Backup":

- CSV-Export für Gewichte, Gießen und Notizen (Excel-tauglich: Semikolon, Dezimalkomma, UTF-8).
- Backup als `.db`-Datei herunterladen und über "Aus Backup wiederherstellen" wieder einspielen
  (vorher wird automatisch eine Kopie `vor-restore-…` angelegt).
- Automatisch: einmal pro Tag eine Kopie in `data/backups/` (die letzten 14 bleiben).
