# astro-ingest: dev environment setup (UnRAID + GitHub + Claude Code)

Run steps 1–4 on the **UnRAID console** (SSH to FractalR5Tower as root). Steps 5–8 run **inside the dev container**.
Names used throughout: repo `cfmorrell/astro-ingest`, container `astro-ingest-dev`, host port `8090`.

## 1. Project folders
```bash
BASE=/mnt/user/docker_appdata/astro-ingest-dev
mkdir -p $BASE/src $BASE/home
cp -r /mnt/user/Astronomy/Z95-ClaudeReferences/astro-ingest-scaffold/. $BASE/src/
chown -R 99:100 $BASE
```

## 2. Sandbox: a small, writable copy of the archive for development
The dev container mounts the real archive **read-only**. Everything the app writes during development goes to this sandbox.
```bash
SB=/mnt/user/astro-sandbox
mkdir -p $SB/{000-FinalizedImages,001-MasterBias,002-MasterDarks,100-ByMessierNumber,101-ByNGCNumber,102-ByICNumber,103-ByDate}
rsync -a /mnt/user/Astronomy/Z95-ClaudeReferences /mnt/user/Astronomy/ZZ_*.md $SB/
rsync -a "/mnt/user/Astronomy/001-MasterBias/ASI2600MC Pro"  $SB/001-MasterBias/
rsync -a "/mnt/user/Astronomy/002-MasterDarks/ASI2600MC Pro" $SB/002-MasterDarks/
rsync -a /mnt/user/Astronomy/MarkariansChain $SB/          # one small, complete 2600MC target to test against
chown -R 99:100 $SB
```
- **Use real copies (rsync), never hard links.** The app rewrites files such as PROJECT_INFO.txt, and with hard links those rewrites would hit the real archive.
- To reset the sandbox at any time, delete `$SB` and rerun this step.
- Don't add the sandbox to any backup job.

## 3. Mount the ASIAIR share on UnRAID
1. Give the ASIAIR a **static DHCP reservation** on your router so its IP never changes.
2. UnRAID → **Main → Unassigned Devices → Add Remote SMB/NFS Share** → SMB → the ASIAIR's IP → browse to its share (guest/no password) → mount name **`ASIAIR`**. It appears at `/mnt/remotes/ASIAIR`. Turn on auto-mount.
3. The ASIAIR is often in the field. The container uses `rslave` propagation, so the share shows up inside the container whenever UnRAID mounts it, with no restart.
4. **Recommended:** while the ASIAIR is online, snapshot its contents once for testing, so development never touches the real device:
   `rsync -a /mnt/remotes/ASIAIR/ /mnt/user/astro-sandbox/_asiair-sample/`

## 4. Build and start the dev container
```bash
bash /mnt/user/docker_appdata/astro-ingest-dev/src/dev/run-dev.sh
docker exec -it astro-ingest-dev bash
```
The container runs as uid 99 / gid 100 (UnRAID `nobody:users`), so anything it creates matches the array's ownership.

## 5. Install and log in to Claude Code (inside the container)
```bash
curl -fsSL https://claude.ai/install.sh | bash      # installs into /home/dev/.local, which is persisted
claude --version
cd /workspace && claude                              # first run: prints a login URL; open it on your Mac and paste the code back
```
Because `/home/dev` is a persisted volume, the install, auto-updates, login and history survive container rebuilds.

## 6. Git identity and GitHub access (inside the container)
```bash
git config --global user.name  "Chris Morrell"
git config --global user.email "<your GitHub email or noreply address>"
git config --global init.defaultBranch main
ssh-keygen -t ed25519 -C "astro-ingest-dev@FractalR5Tower" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```
On GitHub:
1. Create a **private, empty** repo named `astro-ingest`. Don't add a README, .gitignore or license, since the scaffold already has them.
2. Add the public key as a **deploy key with write access**: repo → Settings → Deploy keys. This scopes the key to this one repo, which is safer than adding it to your account.

Then verify:
```bash
ssh -T git@github.com     # accept the host key; expect "Hi cfmorrell/astro-ingest! You've successfully authenticated"
```

## 7. Initial commit and push (inside the container)
```bash
cd /workspace
git init
cp .env.example .env
git add -A
git status               # sanity check: no .fit/.fits/.env/state files listed
git commit -m "Scaffold: CLAUDE.md, handoff docs, dev container, reference scripts"
git remote add origin git@github.com:cfmorrell/astro-ingest.git
git push -u origin main
```

The staging share `/mnt/user/astro-ingest-staging` (created in UnRAID → Shares) is mounted at `/staging` by
`dev/run-dev.sh`, which also sets `STAGING_DIR=/staging`.

Python dependencies go in a virtualenv inside the repo (git-ignored, persisted with `/workspace`):
```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```
`dev/run-dev.sh` passes the repo's `.env` to the container, so after editing `.env`, rerun it on the UnRAID console.

## 8. Hand off to Claude Code
In `/workspace`, run `claude`, switch to **plan mode** (Shift+Tab), and start with:

> Read CLAUDE.md, then docs/ClaudeHandoff.md and docs/ORGANIZATION_GUIDE.md in full. Survey the reference scripts and
> the read-only archive at /astro and the sandbox at /astro-sandbox (including /astro-sandbox/_asiair-sample if present).
> Then propose an architecture and a phased plan for the ASIAIR ingest app (indexer → review screen → copy/verify →
> filing + PROJECT_INFO + index links → source cleanup). Ask me anything that's unclear before writing code.

## Later: production
- A separate `astro-ingest` container from the same repo, mounting **`/mnt/user/Astronomy:/astro` read-write** with `ASTRO_ROOT=/astro`, and no Claude Code inside.
- Expose it through Nginx Proxy Manager like your other apps, e.g. `ingest.cfmorrell.com` → `192.168.1.4:<prod port>`.
- **Before the first production run, make sure the Astronomy share has a backup.** This will be the first automated writer to your archive.

## Checklist
- [ ] 1 Folders + scaffold copied
- [ ] 2 Sandbox seeded
- [ ] 3 ASIAIR mounted at /mnt/remotes/ASIAIR (+ sample snapshot)
- [ ] 4 Dev container running
- [ ] 5 Claude Code installed + logged in
- [ ] 6 Deploy key added, `ssh -T` OK
- [ ] 7 Initial commit pushed
- [ ] 8 First plan-mode session
