# iPronto Marketing

This folder contains everything marketing-related for iPronto:

| File | Purpose |
|---|---|
| `generate_social_content.py` | Script that pulls live MLS data and uses Claude to draft social posts |
| `MarketingStrategy.md` | Full strategy: Reddit, Instagram, blog posts, backlinks, physical marketing |
| `Dockerfile` | Container definition for running the script |
| `requirements.txt` | Python dependencies |
| `output/` | Generated drafts land here (gitignored — review before posting) |

---

## Running the content generator

### With Docker (recommended)

From `data_service/`:

```bash
# Generate all content types (Reddit + Instagram + blog)
docker-compose run --rm marketing

# Generate one type
docker-compose run --rm marketing reddit
docker-compose run --rm marketing instagram
docker-compose run --rm marketing blog
```

Drafts are saved to `marketing/output/` with a timestamp, e.g. `reddit_20260401_0900.txt`.
Review and edit them before posting — they're starting points, not final copy.

### Locally (without Docker)

```bash
cd data_service/marketing
pip install -r requirements.txt
python generate_social_content.py all
```

The script finds the `env` file automatically when run locally.

---

## How the script works

1. **Connects to MongoDB** and pulls live market stats:
   - Active listing count, new this week, pending
   - Average list price and days on market
   - Price reductions in the last 7 days
   - Top cities by inventory
   - Most affordable active listing

2. **Calls the Claude API** (`claude-sonnet-4-6`) to write three content types — all grounded in the real live numbers so every run produces fresh, specific content:

   - **Reddit post** — market data summary for r/bayarea or r/SFBayHousing, with a low-key mention of iPronto at the end
   - **Instagram caption** — picks a featured listing at random (active, has photos), writes emoji-formatted stats + insight + hashtags
   - **Blog post** — 500–700 words covering market conditions, tips for buyers, closing CTA for iPronto

3. **Saves output** to `output/` and prints to terminal.

---

## Content strategy summary

See `MarketingStrategy.md` for the full playbook. Quick version:

### Reddit — post 1–2×/week
- **r/SFBayHousing**, **r/bayarea**, **r/FirstTimeHomeBuyer**, **r/homebuying**
- Be genuinely useful — share data, answer questions in comments
- Never post the same thing twice; run the script for fresh data each time

### Instagram — post 3×/week
- **Monday** — listing of the week (photo + stats caption)
- **Wednesday** — market insight (Canva graphic + data caption)
- **Friday** — educational/AI angle post

### Blog — publish monthly
- Post to your own site (build a `/blog` section) and cross-post to Medium + LinkedIn
- Each post targets a specific SEO keyword and links back to `ai.roozbeh.realtor`

### Backlinks — one-time setup
Priority order: Google Business Profile → Zillow Agent Profile → Realtor.com → Yelp → LinkedIn → Medium → Nextdoor

See `MarketingStrategy.md` for full instructions and a tracking checklist.

---

## Checkboxes

In `MarketingStrategy.md`, mark tasks done by changing `[ ]` to `[x]`:

```markdown
- **Status:** [x] Done — 2026-04-01
```

GitHub renders these as ticked checkboxes in the file view.

---

## Rebuilding the Docker image

If you update `generate_social_content.py` or `requirements.txt`:

```bash
docker-compose build marketing
```
