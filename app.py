# Streamlit + SerpAPI Google Autocomplete dashboard
# - Batch seeds (one per line)
# - UK defaults (gl=uk, hl=en, google.co.uk) + selectable
# - A–Z expansion, prefix/suffix combos
# - Progress bar, rate limit pacing, retry/backoff
# - CSV export
# - Reads SERPAPI_KEY from Streamlit Secrets or environment

import os
import time
import string
import requests
import pandas as pd
import streamlit as st

# -------------------------
# Config
# -------------------------
st.set_page_config(page_title="Google Autocomplete (SerpAPI)", layout="wide")
st.title("Google Autocomplete (SerpAPI)")

# Get API key safely
SERPAPI_KEY = st.secrets.get("SERPAPI_KEY", os.getenv("SERPAPI_KEY", ""))

if not SERPAPI_KEY:
    st.warning(
        "No SERPAPI_KEY found. Add it in Streamlit Secrets or as an environment variable.\n\n"
        "In Streamlit Cloud: App → Settings → Secrets → `SERPAPI_KEY=\"your_key\"`.\n"
        "Locally: create `.streamlit/secrets.toml` with the same line."
    )

# -------------------------
# Sidebar controls
# -------------------------
with st.sidebar:
    st.header("Settings")

    # Region & language (UK defaults)
    gl = st.selectbox(
        "gl (country code)",
        ["uk", "us", "ie", "nl", "de", "fr", "es", "it", "au", "ca", "in"],
        index=0,
        help="Geo location parameter for Google."
    )
    hl = st.selectbox(
        "hl (language)",
        ["en", "en-GB", "en-US", "nl", "de", "fr", "es", "it"],
        index=1,
        help="UI language parameter Google uses."
    )
    google_domain = st.selectbox(
        "google_domain",
        ["google.co.uk", "google.com", "google.ie", "google.nl", "google.de", "google.fr", "google.es", "google.it", "google.com.au", "google.ca", "google.co.in"],
        index=0
    )

    st.divider()
    st.caption("Request behaviour")
    rpm = st.slider("Max requests per minute", 10, 120, 60)
    delay = 60.0 / float(rpm)
    max_retries = st.slider("Max retries", 0, 5, 2)
    backoff_base = st.slider("Backoff (seconds)", 1, 10, 3)

    st.divider()
    st.caption("Generation options")
    use_az = st.checkbox("A–Z expansion (append a..z)", value=False, help="For each seed, also query 'seed a', 'seed b', ...")
    prefix_suffix = st.checkbox("Use prefix/suffix lists", value=False, help="Combine seeds with your prefixes/suffixes.")
    unique_only = st.checkbox("De‑duplicate suggestions", value=True)
    keep_seed_row = st.checkbox("Include the original seed row", value=True)

    prefixes = []
    suffixes = []
    if prefix_suffix:
        prefixes = st.text_area("Prefixes (one per line)", value="best\ncheap\nenterprise\nwhat is", height=120).splitlines()
        prefixes = [p.strip() for p in prefixes if p.strip()]
        suffixes = st.text_area("Suffixes (one per line)", value="software\nservices\nnear me\nfor small business", height=120).splitlines()
        suffixes = [s.strip() for s in suffixes if s.strip()]

    st.divider()
    csv_sep = st.selectbox("CSV delimiter", [",", ";", "\t"], index=0)

# -------------------------
# Helpers
# -------------------------
def serpapi_autocomplete(q, gl, hl, google_domain, api_key):
    """
    Call SerpAPI Google Autocomplete.
    Docs: engine=google_autocomplete
    """
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google_autocomplete",
        "q": q,
        "gl": gl,
        "hl": hl,
        "google_domain": google_domain,
        "api_key": api_key,
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    # Expect data["suggestions"] as list of {"value": "...", ...}
    suggestions = data.get("suggestions", [])
    values = []
    for i, s in enumerate(suggestions, start=1):
        val = s.get("value")
        if val:
            values.append((i, val))
    return values  # list of (position, suggestion)

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

def expand_queries(seed):
    # Base queries always include seed itself
    variants = [seed]
    if use_az:
        variants += [f"{seed} {ch}" for ch in string.ascii_lowercase]
    # Add prefix/suffix combos
    if prefixes:
        variants += [f"{p} {seed}" for p in prefixes]
    if suffixes:
        variants += [f"{seed} {s}" for s in suffixes]
    return list(dict.fromkeys([v.strip() for v in variants]))  # de‑dupe, preserve order

# -------------------------
# Main UI
# -------------------------
st.markdown("**Seeds (one per line):**")
default_seeds = "web design\nwebsite builder"
seeds_raw = st.text_area("Enter seeds", height=160, value=default_seeds)
seeds = [s.strip() for s in seeds_raw.splitlines() if s.strip()]
seeds = list(dict.fromkeys(seeds))  # de‑dupe, keep order

run = st.button("Run autocomplete")

if run:
    if not SERPAPI_KEY:
        st.error("Missing SERPAPI_KEY. Add it via Streamlit Secrets or env var.")
        st.stop()
    if not seeds:
        st.error("Add at least one seed.")
        st.stop()

    rows = []
    # Pre-compute total steps for progress
    total_queries = sum(len(expand_queries(seed)) for seed in seeds)
    progress = st.progress(0)
    status = st.empty()
    done = 0

    for seed in seeds:
        queries = expand_queries(seed)
        if keep_seed_row and seed not in queries:
            queries.insert(0, seed)

        for q in queries:
            status.write(f"Fetching **{q}** …")
            try:
                suggestions = with_retry(serpapi_autocomplete, q, gl, hl, google_domain, SERPAPI_KEY)
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

    # De‑duplicate suggestion text if requested (keep first occurrence per seed)
    if unique_only and not df.empty:
        df = df.sort_values(["seed", "suggestion", "position"], na_position="last")
        df = df.drop_duplicates(subset=["seed", "suggestion"], keep="first")

    # Tidy & show
    if df.empty:
        st.warning("No suggestions returned.")
    else:
        st.markdown("### Results")
        st.dataframe(df, use_container_width=True)

        # Simple summary per seed
        summary = (
            df.dropna(subset=["suggestion"])
              .groupby("seed")["suggestion"].nunique()
              .reset_index(name="unique_suggestions")
              .sort_values("unique_suggestions", ascending=False)
        )
        st.markdown("#### Summary (unique suggestions per seed)")
        st.dataframe(summary, use_container_width=True)

        # CSV export
        sep = {";": ";", ",": ",", "\t": "\t"}[csv_sep]
        st.download_button(
            "Download CSV",
            df.to_csv(index=False, sep=sep).encode("utf-8"),
            file_name="autocomplete_results.csv",
            mime="text/csv"
        )

    st.caption("Tip: ‘A–Z expansion’ multiplies requests (26×). Use RPM slider to avoid throttling.")
