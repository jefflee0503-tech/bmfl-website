# One-time original image archiving

This repository includes a GitHub Actions workflow that copies the currently public BMFL site into `docs/` and downloads externally hosted images/assets into `docs/assets/mirror/`.

## Run it
1. Upload/commit this repository to GitHub.
2. Open **Actions** → **Archive current BMFL site** → **Run workflow**.
3. Wait for the green check mark.
4. Confirm that the repository now contains a `docs/` folder and open `docs/MIRROR_REPORT.md`.
5. In **Settings → Pages**, select **Deploy from a branch**, branch `main`, folder `/docs`, then Save.

After the workflow commits the archive, the site's downloaded images are stored in the repository and no longer depend on the old image URLs for those successfully mirrored assets.

Run this before the source site is taken offline.
