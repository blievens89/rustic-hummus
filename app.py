# Google Autocomplete (SerpAPI) — Streamlit app
# Features:
# - Batch seeds (one per line)
# - UK-friendly defaults (gl=uk, hl=en)
# - Optional client (chrome/firefox/...)
# - A–Z expansion + prefix/suffix combos
# - Progress bar, RPM pacing, retry with backoff
# - De‑duplication, summary table, CSV export
# - Playground for a single query (shows raw JSON)
# - Reads SERPAPI_KEY from Streamlit Secrets or env (do NOT hardcode keys)

import os
import time
import string
import requests
import pandas as pd
import streamlit as st

# -------------------------
# App setup
# -------------------------
st.set_page_config(page_title="Google Autocomplete (SerpAPI)", layout="wide")
st.title("Google Autocomplete (SerpAPI)")

# Read API key safely
SERPAPI_KEY = st.secrets.get("SERPAPI_KEY", os.getenv("SERPAPI_KEY", ""))

if not SERPAPI_KEY:
    st.warning(
        "No SERPAPI_KEY found.\n\n"
        "• Streamlit Cloud → App → Settings → Secrets → add:  SERPAPI_KEY=\"your_real_key\"\n"
        "• Local run: create .streamlit/secrets.toml with the same line."
    )

# -------------------------
# Sidebar controls
# -------------------------
with st.sidebar:
    st.header("Settings")

    # Localisation (docs allow gl, hl for this engine; keep hl two‑letter)
    gl = st.selectbox(
        "gl (country)",
        ["uk", "us", "ie", "nl", "de", "fr", "es", "it", "au", "ca", "in"],
        index=0,
        help="Country code for localisation."
    )
    hl = st.selectbox(
        "hl (language)",
        ["en", "fr", "de", "es", "it", "nl"],
        index=0,
        help="Two‑letter language code (Autocomplete requires 2 letters)."
    )
    client = st.selectbox(
        "client (optional)",
        ["", "chrome", "firefox", "safari", "youtube", "android", "ios"],
        index=1,  # chrome by default is common
        help="Autocomplete client; leave blank for default."
    )

    st.divider()
    st.caption("Request behaviour")
    rpm = st.slider("Max requests per minute", 10, 120, 60,
                    help="Adds a small delay between requests to avoid throttling.")
    delay = 60.0 / float(rpm)
    max_retries = st.slider("Max retries", 0, 5, 2)
    backoff_base = st.slider("Backoff (seconds)", 1, 10, 3,
                             help="Wait time grows with each retry.")

    st.divider()
    st.caption("Generation options")
    use_az = st.checkbox("A–Z expansion (append a..z)", value=False,
                         help="For each seed, also query 'seed a'..'seed z'.")
    use_prefix_suffix = st.checkbox("Use prefix/suffix lists", value=False,
                                    help="Combine seeds with your prefixes/suffixes.")
    unique_only = st.checkbox("De‑duplicate suggestions per seed", value=True)
    keep_seed_row = st.checkbox("Include base seed queries in output", value=True)

    prefixes, suffixes = [], []
    if use_prefix_suffix:
        prefixes = [p.strip() for p in st.text_area(
            "Prefixes (one per line)",
            value="best\ncheap\nenterprise\nwhat is",
            height=120
        ).splitlines() if p.strip()]
        suffixes = [s.strip() for s in st.text_area(
            "Suffixes (one per line)",
            value="software\nservices\nnear me\nfor small business",
            height=120
        ).splitlines() if s.strip()]

    st.divider()
    csv_sep = st.selectbox("CSV delimiter", [",", ";", "\\t"], index=0)

# -------------------------
# Helpers
# -------------------------
API_URL = "https://serpapi.com/search"

def serpapi_autocomplete(q: str, gl: str, hl2: str, client_opt: str, api_key: str):
    """
    SerpAPI Google Autocomplete.
    Endpoint: /search?engine=google_autocomplete
    Required: engine=google_autocomplete, q, api_key
    Optional: gl (country), hl (two‑letter), client
    """
    params = {
        "engine": "google_autocomplete",
        "q": q,
        "api_key": api_key,
    }
    if gl:
        params["gl"] = gl
    if hl2:
        params["hl"] = hl2.split("-")[0]  # normalise 'en-GB' -> 'en'
    if client_opt:
        params["client"] = client_opt

    r = requests.get(API_URL, params=params, timeout=20)

    # surface helpful error messages
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        detail = ""
        try:
            detail = r.json().get("error") or r.json().get("message") or ""
        except Exception:
            detail = r.text[:300]
        raise RuntimeError(f"SerpAPI error {r.status_code}: {detail}") from e

    data = r.json()
    suggestions = data.get("suggestions", [])  # list of dicts with 'value'
    values = []
    for i, s in enumerate(suggestions, start=1):
        val = s.get("value")
        if val:
            values.append((i, val))
    # Also return header usage if present
    remaining = r.headers.get("X-RateLimit-Remaining")
    return values, remaining

def with_retry(fn, *args, **kwargs):
    tries = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            tries += 1
            if tries > max_retries:
                raise
            time.sleep(backoff_base * tries)

def expand_queries(seed: str):
    """Make query variants for a seed based on toggles."""
    variants = [seed]
    if use_az:
        variants += [f"{seed} {ch}" for ch in string.ascii_lowercase]
    if prefixes:
        variants += [f"{p} {seed}" for p in prefixes]
    if suffixes:
        variants += [f"{seed} {s}" for s in suffixes]
    # de‑dupe, preserve order
    return list(dict.fromkeys([v.strip() for v in variants if v.strip()]))

# -------------------------
# Main UI
# -------------------------
st.markdown("**Seeds (one per line):**")
seeds_raw = st.text_area("Enter seeds", height=140, value="web design\nwebsite builder")
seeds = [s.strip() for s in seeds_raw.splitlines() if s.strip()]
seeds = list(dict.fromkeys(seeds))  # de‑dupe

c_run, c_play = st.columns([1,1])
run = c_run.button("Run autocomplete")
show_playground = c_play.checkbox("Show single‑query playground", value=False)

# Playground (raw JSON viewer)
if show_playground:
    st.markdown("#### Playground")
    q = st.text_input("Test a single query", value="coffee")
    if st.button("Test query"):
        try:
            values, remaining = with_retry(serpapi_autocomplete, q, gl, hl, client, SERPAPI_KEY)
            st.write({"suggestions": [v for _, v in values], "rate_limit_remaining": remaining})
        except Exception as e:
            st.error(str(e))

# Run batch
if run:
    if not SERPAPI_KEY:
        st.error("Missing SERPAPI_KEY. Add it via Streamlit Secrets or environment variable.")
        st.stop()
    if not seeds:
        st.error("Add at least one seed.")
        st.stop()

    rows = []
    total_queries = sum(len(expand_queries(seed)) for seed in seeds)
    progress = st.progress(0)
    status = st.empty()
    done = 0
    last_remaining = None

    for seed in seeds:
        queries = expand_queries(seed)
        if keep_seed_row and seed not in queries:
            queries.insert(0, seed)

        for q in queries:
            status.write(f"Fetching **{q}** …")
            try:
                suggestions, remaining = with_retry(serpapi_autocomplete, q, gl, hl, client, SERPAPI_KEY)
                last_remaining = remaining
                if not suggestions:
                    rows.append({"seed": seed, "query_sent": q, "position": None, "suggestion": None})
                else:
                    for pos, text in suggestions:
                        rows.append({"seed": seed, "query_sent": q, "position": pos, "suggestion": text})
            except Exception as e:
                rows.append({"seed": seed, "query_sent": q, "position": None, "suggestion": None, "error": str(e)})

            done += 1
            progress.progress(int(done * 100 / max(1, total_queries)))
            time.sleep(delay)

    progress.empty(); status.empty()

    df = pd.DataFrame(rows)

    # De‑dup per seed if requested
    if unique_only and not df.empty:
        df = df.sort_values(["seed", "suggestion", "position"], na_position="last")
        df = df.drop_duplicates(subset=["seed", "suggestion"], keep="first")

    if df.empty:
        st.warning("No suggestions returned.")
    else:
        st.markdown("### Results")
        st.dataframe(df, use_container_width=True)

        # Summary: unique suggestion count per seed
        summary = (
            df.dropna(subset=["suggestion"])
              .groupby("seed")["suggestion"].nunique()
              .reset_index(name="unique_suggestions")
              .sort_values("unique_suggestions", ascending=False)
        )
        st.markdown("#### Summary (unique suggestions per seed)")
        st.dataframe(summary, use_container_width=True)

        # Export CSV
        sep = {",": ",", ";": ";", "\\t": "\t"}[csv_sep]
        st.download_button(
            "Download CSV",
            df.to_csv(index=False, sep=sep).encode("utf-8"),
            file_name="autocomplete_results.csv",
            mime="text/csv"
        )

    # Rate limit hint if header was available
    if last_remaining is not None:
        st.caption(f"SerpAPI X-RateLimit-Remaining: {last_remaining}")
