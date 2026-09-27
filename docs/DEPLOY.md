# Deploying astro-ingest on UnRAID

The production image is built by GitHub Actions (`.github/workflows/docker-publish.yml`) on every push to `main` that
touches the app, after the test suite passes, and published as **`ghcr.io/cfmorrell/astro-ingest`**:
`latest` (main), `sha-<commit>`, and `v<version>` for version tags. `dev/Dockerfile` is only for the dev container.

## What the container needs

| Container path | Host path (default) | Mode | What |
|---|---|---|---|
| `/astro` | `/mnt/user/Astronomy` | **rw** | The Astronomy share: frames are filed here. App state lives in `Z95-ClaudeReferences/ingest/` on it (answers, batches, logs, SQLite). |
| `/staging` | `/mnt/user/astro-ingest-staging` | rw | Frames read once from the ASIAIR, until filed. Its own share. |
| `/cache` | `/mnt/user/docker_appdata/astro-ingest/cache` | rw | Rendered previews. Disposable. |
| port 8000 | 8091 | tcp | The web app. (8090 is the dev container's.) |

Variables: `TZ` (default `America/New_York`), `ALLOW_DEVICE_DELETE` (**0** in the image; `1` lets Clean up delete from
the ASIAIR), and the optional `ASIAIR_SUBNET` (only to override the detected home network) and `ASSUMED_WIFI_MB_S`.
The ASIAIR itself needs no mount: the app finds it on the network and talks SMB to it (bridge networking is fine).
The container runs as `99:100` (nobody:users), so everything it writes on the shares is owned like the rest of the array.

## Install (UnRAID template)

1. **Make the image pullable.** GHCR packages start private. On GitHub: your profile → Packages → `astro-ingest` →
   Package settings → Change visibility → Public. (Or keep it private and `docker login ghcr.io` on UnRAID with a
   personal access token that has `read:packages`.)
2. **Add the template** and create the cache folder (the container runs as nobody:users, and a folder UnRAID
   creates for a mount would be root's). From the UnRAID console:
   ```bash
   mkdir -p /mnt/user/docker_appdata/astro-ingest/cache && chown -R 99:100 /mnt/user/docker_appdata/astro-ingest
   curl -fsSL https://raw.githubusercontent.com/cfmorrell/astro-ingest/main/deploy/unraid/astro-ingest.xml \
     -o /boot/config/plugins/dockerMan/templates-user/my-astro-ingest.xml
   ```
3. **Create the container.** Docker → Add Container → Template: *astro-ingest*. Check the paths, set
   **Allow deleting from the ASIAIR = 1** when your backups are in place, Apply.
4. Open `http://<unraid>:8091`, connect to the ASIAIR (it's found from your browser's network), and go.

To update: Docker → astro-ingest → *Force update* (or *Check for updates*), after a push to `main` has built.

Without the template, `deploy/run.sh` does the same with `docker run`.

## Moving over from the dev container

- **State starts fresh** in `/mnt/user/Astronomy/Z95-ClaudeReferences/ingest/`: the dev container's state stays in the
  sandbox (`/mnt/user/astro-sandbox/Z95-ClaudeReferences/ingest/`). Recent devices, answers, Verify results and batch
  history don't carry over: connect to the ASIAIR once and name it again.
- **Staging is shared** by default (`/mnt/user/astro-ingest-staging`), and the device's staging folder has the same
  name in both, so **don't stage from both containers at once**. The same goes for Clean up on the same ASIAIR.
- Keep the NAS backups current before the first real run (the dev runs only wrote to the sandbox).
- Recommended before exposing it beyond the LAN: put it behind Nginx Proxy Manager with authentication (it can delete
  from the ASIAIR).
