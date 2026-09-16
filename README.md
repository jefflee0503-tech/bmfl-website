# BMFL GitHub Pages mirror

Static GitHub Pages version of the public BMFL website (jeon.kaist.ac.kr).

## Publish on GitHub Pages
1. Create a new GitHub repository (for example `bmfl-website`).
2. Upload everything in this folder to the repository root.
3. GitHub → Settings → Pages → Build and deployment → Deploy from a branch.
4. Select `main` and `/ (root)`, then Save.
5. The included `CNAME` is already set to `jeon.kaist.ac.kr`. Only switch KAIST DNS after the GitHub preview has been checked.

## Editing
- `index.html`: home / recent publications
- `research.html`: research
- `people.html`: members
- `publications.html`: publications
- `news.html`: news
- `contact.html`: contact
- `assets/style.css`: appearance

## Important asset note
Because the Weebly export/archive was unavailable, this first mirror uses several currently-public remote image URLs. Before the old service is taken offline, those images should be downloaded into `assets/images/` and the HTML URLs replaced with local paths. Text and page structure are already static and independent of Weebly.
