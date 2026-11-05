# Streamline

Private Streaming-Webapp für eigene Videos. Das Frontend lässt sich als statische Webseite z. B. auf GitHub Pages hosten. Konten, private Videos und Wiedergabedaten liegen dabei dauerhaft in Supabase und sind nach dem Login auf verschiedenen Geräten verfügbar.

## Internetbetrieb mit GitHub Pages + Supabase

### 1. Supabase-Projekt einrichten

1. Lege ein Projekt bei [Supabase](https://supabase.com/) an.
2. Öffne den SQL Editor und führe [`supabase/schema.sql`](supabase/schema.sql) vollständig aus. Das erstellt Serien, Episoden, Fortschritte, Favoriten und Merkliste sowie den **privaten** `videos`-Bucket und Zugriffsregeln. Für eine bestehende Installation ergänzt das Skript `thumbnail_path` und die erlaubten Video-MIME-Typen.
3. Notiere Projekt-URL und den `publishable`- bzw. `anon`-Key aus den API-Einstellungen.
4. Aktiviere E-Mail/Passwort bei **Authentication → Providers → Email**. Bei aktivierter E-Mail-Bestätigung zeigt die Website nach der Registrierung eine entsprechende Meldung.
5. Setze in **Authentication → URL Configuration** die Site URL und Redirect-URLs auf deine GitHub-Pages-Adresse, zum Beispiel `https://BENUTZER.github.io/REPOSITORY/`.

### 2. GitHub Pages veröffentlichen

1. Lade dieses Repository auf GitHub hoch und verwende den Branch `main`.
2. Öffne **Repository Settings → Pages** und stelle als Build and deployment **GitHub Actions** ein.
3. Lege unter **Settings → Secrets and variables → Actions → Variables** die beiden Repository-Variablen an:
   - `SUPABASE_URL` — Projekt-URL, z. B. `https://abcxyz.supabase.co`
   - `SUPABASE_ANON_KEY` — Publishable-/Anon-Key aus den API-Einstellungen
4. Der Workflow [`.github/workflows/pages.yml`](.github/workflows/pages.yml) baut `config.js` und veröffentlicht die statische Website. Nach dem ersten erfolgreichen Lauf zeigt GitHub die öffentliche Adresse unter **Settings → Pages**.

Der Browser-Key ist absichtlich öffentlich und darf im Frontend erscheinen. Er kann allein keine fremden Daten lesen oder ändern: Postgres-RLS und Storage-Policies begrenzen jeden Zugriff auf den angemeldeten Eigentümer. **Niemals** `service_role`, Secret Keys, Datenbankpasswörter oder andere privilegierte Schlüssel in `config.js`, GitHub Actions Variables für den Browser-Build oder HTML/JavaScript eintragen.

### 3. Große Videos und Kosten

Supabase Storage wird für Videos verwendet, nicht die GitHub-Dateiablage. Unterstützt werden gängige Container wie MP4, M4V, MKV, WebM, MOV, AVI, WMV, FLV, MPEG, OGV, 3GP, TS/MTS/M2TS, VOB, ASF, RM/RMVB und DIVX. Der Upload läuft direkt vom Browser zum privaten Storage über TUS und kann nach einer Unterbrechung fortgesetzt werden. Nach erneutem Auswählen derselben Datei versucht die TUS-Bibliothek, einen passenden vorherigen Upload wieder aufzunehmen.

**Uploadformat ist nicht gleich Wiedergabeformat.** Browser spielen MP4/H.264 und WebM meist direkt ab. MKV und manche AVI/MOV/WMV-Dateien können gespeichert werden, aber der Browser kann Container oder enthaltenen Codec möglicherweise nicht wiedergeben. Für zuverlässige MKV-Wiedergabe auf allen Geräten braucht es serverseitiges Transcoding (z. B. FFmpeg/HLS); das ist in diesem statischen GitHub-Pages/Supabase-Aufbau nicht enthalten. Lange Dateien werden nicht im Browser umgewandelt.

Die aktuellen Supabase-Limits sind wichtig: Der Free-Plan begrenzt die globale Dateigröße auf **50 MB**. Größere Dateien benötigen einen Plan, der ein höheres Global File Size Limit erlaubt. Resumable Uploads unterstützen derzeit Dateien bis **50 GB**; auch der gewählte Plan, Speicherplatz und ausgehender Videodatenverkehr setzen Grenzen. Lange Dateien können deshalb laufende Speicher- und Bandbreitenkosten verursachen. Vor dem Hochladen in **Storage → Settings** die benötigte Dateigröße erlauben und die aktuellen Quoten prüfen. [Dateigrößen-Limits](https://supabase.com/docs/guides/storage/uploads/file-limits), [TUS-Uploads](https://supabase.com/docs/guides/storage/uploads/resumable-uploads), [Storage-Preise](https://supabase.com/docs/guides/storage/pricing).

## Was über das Konto hinweg gespeichert wird

- Supabase Auth verwaltet Anmeldedaten. Passwörter werden nicht in HTML, JavaScript, der SQLite-Datenbank der lokalen Variante oder im Browser-Storage der App abgelegt.
- Serien und Episoden liegen in Postgres mit aktiviertem Row-Level-Security.
- Videos und automatisch erzeugte JPEG-Vorschaubilder liegen in einem privaten Storage-Bucket unter einem Pfad mit Benutzer-ID. Download und Upload werden durch Storage-Policies eingeschränkt. Zum Abspielen erhält der angemeldete Nutzer eine zeitlich begrenzte signierte URL.
- Serien erscheinen als Cover-Karten. Das erste verfügbare Folgenbild wird als Seriencover verwendet; im Episodeneditor kann ein anderes Folgenbild als Seriencover gesetzt werden.
- Favoriten, Merkliste und Position, Dauer, zuletzt gespielt sowie „vollständig angesehen“ werden nutzerbezogen gespeichert. Anmeldung mit demselben Konto auf einem anderen Gerät lädt denselben Stand.

## Lokal starten

Für den lokalen Einzelplatzbetrieb gibt es weiterhin die Python/SQLite-Variante:

```sh
python3 server.py
```

Dann `http://127.0.0.1:8000` öffnen. Die lokale Variante speichert alles im Ordner `data/` und ist von außen nicht erreichbar. Beim konfigurierten Supabase-Frontend werden Anmeldung, Bibliothek und Uploads über Supabase statt über die lokale API abgewickelt.

## Lokale Supabase-Konfiguration

Für lokales statisches Testen `config.example.js` nach `config.js` kopieren und URL sowie Publishable-/Anon-Key eintragen. `config.js` wird von Git ignoriert. Alternativ lässt sich das Frontend auf `http://localhost` über einen statischen Server aufrufen. Ohne `config.js` verwendet `server.py` weiterhin den lokalen API-Modus.

## Struktur

- `index.html`, `style.css`, `app.js`: responsive Oberfläche und Player.
- `config.example.js`: Vorlage mit ausschließlich öffentlichen Browser-Einstellungen.
- `server.py`: optionale lokale API mit SQLite.
- `supabase/schema.sql`: Cloud-Datenmodell, Eigentümerregeln und privater Videospeicher.
- `.github/workflows/pages.yml`: Veröffentlichung über GitHub Pages Actions.

## Grenzen

Kein serverseitiges Transcoding/HLS, kein Upload eigener Coverbilder, keine E-Mail-Passwort-zurücksetzen-Oberfläche und keine Medien-Löschfunktion. Wiedergabe hängt bei allen Formaten vom Browser und Codec ab; insbesondere MKV ist nicht in jedem Browser abspielbar.
