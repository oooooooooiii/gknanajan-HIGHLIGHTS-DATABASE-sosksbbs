from datetime import datetime
import json
import os
import re
import requests

FIREBASE_URL = os.environ.get("FIREBASE_URL")
FIREBASE_SECRET = os.environ.get("FIREBASE_SECRET")
API_BASE_URL = os.environ.get("API_BASE_URL")

CHECKPOINT_FILE = "last_match.json"


def normalize_string(text):
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def format_date_part(date_str):
    if not date_str:
        return ""
    try:
        dt = datetime.strptime(str(date_str).strip(), "%b %d, %Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return normalize_string(date_str)


def make_slug(title, date_str):
    clean_t = normalize_string(title)
    clean_d = format_date_part(date_str)
    if clean_d:
        return f"{clean_t}-{clean_d}"
    return clean_t


def parse_date(date_str):
    if not date_str:
        return datetime.min
    date_str = str(date_str).strip()
    formats = ["%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            pass
    return datetime.min


def clean_firebase_key(text):
    if not text:
        return "clean_key"
    safe = str(text)
    safe = re.sub(r"[\.#\$\[\]/\\]", "", safe)
    return safe.strip()


def sanitize_deep_json(obj):
    if isinstance(obj, dict):
        cleaned_dict = {}
        for k, v in obj.items():
            safe_k = clean_firebase_key(k)
            if not safe_k:
                safe_k = "key"
            cleaned_dict[safe_k] = sanitize_deep_json(v)
        return cleaned_dict
    elif isinstance(obj, list):
        return [sanitize_deep_json(item) for item in obj]
    else:
        return obj


def get_last_checkpoint():
    if not os.path.exists(CHECKPOINT_FILE):
        return "", "", ""
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list) and len(data) > 0:
                item = data[0]
                return (
                    item.get("last_slug", "").strip(),
                    item.get("title", "").strip(),
                    item.get("date", "").strip(),
                )
    except Exception:
        pass
    return "", "", ""


def is_checkpoint_match(m_slug, m_title, m_date, chk_slug, chk_title, chk_date):
    if not chk_slug and not chk_title:
        return False

    if chk_slug and m_slug == chk_slug:
        return True

    norm_m_title = normalize_string(m_title)
    norm_c_title = normalize_string(chk_title)
    norm_m_date = format_date_part(m_date)
    norm_c_date = format_date_part(chk_date)

    if norm_m_title and norm_m_title == norm_c_title:
        if not norm_c_date or norm_m_date == norm_c_date:
            return True

    if chk_slug and (chk_slug in m_slug or m_slug in chk_slug):
        if norm_m_date and norm_c_date and norm_m_date == norm_c_date:
            return True

    return False


def save_last_checkpoint(match_obj):
    title = match_obj.get("title", "")
    date_str = match_obj.get("date", "")
    slug = make_slug(title, date_str)
    events_info = match_obj.get("events_info", {})
    home_team = events_info.get("home_team", {}).get("name", "")
    away_team = events_info.get("away_team", {}).get("name", "")

    checkpoint_data = [
        {
            "last_slug": slug,
            "title": title,
            "team1_name": home_team,
            "team2_name": away_team,
            "date": date_str,
        }
    ]
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(checkpoint_data, f, indent=2, ensure_ascii=False)


def sync_and_sort_firebase(new_matches_list):
    if not FIREBASE_URL or not FIREBASE_SECRET:
        print("[!] Error: FIREBASE_URL or FIREBASE_SECRET environment variable is missing!")
        return False, None

    endpoint = f"{FIREBASE_URL.rstrip('/')}/Highlights/matches.json?auth={FIREBASE_SECRET}"

    existing_data = {}
    try:
        res = requests.get(endpoint, timeout=45)
        if res.status_code == 200:
            fetched = res.json()
            if isinstance(fetched, dict):
                existing_data = fetched
    except Exception:
        pass

    all_matches_map = {}

    for key, match in existing_data.items():
        if isinstance(match, dict):
            base_slug = re.sub(r"^\d+_", "", key)
            all_matches_map[base_slug] = match

    for slug, match in new_matches_list:
        all_matches_map[slug] = sanitize_deep_json(match)

    match_tuples = []
    for slug, match in all_matches_map.items():
        d_str = match.get("date", "")
        dt = parse_date(d_str)
        match_tuples.append((dt, slug, match))

    match_tuples.sort(key=lambda x: x[0], reverse=True)

    total_count = len(match_tuples)
    digits = max(4, len(str(total_count)))
    sorted_payload = {}

    for idx, (dt, slug, match) in enumerate(match_tuples, start=1):
        serial_key = f"{idx:0{digits}d}_{slug}"
        sorted_payload[serial_key] = match

    try:
        put_res = requests.put(
            endpoint,
            data=json.dumps(sorted_payload, ensure_ascii=False),
            headers={"Content-Type": "application/json"},
            timeout=180
        )
        if put_res.status_code == 200:
            newest_match = match_tuples[0][2] if match_tuples else None
            return True, newest_match
    except Exception as e:
        print(f"[!] Upload error: {e}")

    return False, None


def main():
    now_str = datetime.now().strftime("%I:%M:%S %p %d-%m-%Y")
    print("==========================================")
    print(f" ReFooty Live Tracker Started: {now_str}")
    print("==========================================")

    if not FIREBASE_URL or not FIREBASE_SECRET or not API_BASE_URL:
        print("[!] Error: Required environment variables are missing!")
        return

    chk_slug, chk_title, chk_date = get_last_checkpoint()
    if chk_slug or chk_title:
        print(f"[+] Loaded local checkpoint: Slug='{chk_slug}', Title='{chk_title}'")
    else:
        print("[!] No valid local checkpoint found. Preparing initial scan.")

    print("[-] Checking ReFooty API for new matches...")

    all_new_matches = []
    checkpoint_matched = False
    matched_title = ""
    matched_slug = ""
    current_page = 1
    max_page = 1
    base = API_BASE_URL.rstrip('/')

    while current_page <= max_page:
        print(f"[*] Fetching API page {current_page}...")
        if current_page == 1:
            url = f"{base}/data.json"
        else:
            url = f"{base}/data_page_{current_page - 1}.json"

        try:
            res = requests.get(url, timeout=30)
            if res.status_code != 200:
                print(f"[!] Error fetching API page {current_page}: Status {res.status_code}")
                break

            content = res.json()
            matches = content.get("matches", [])
            max_page = content.get("lastPage", max_page)

            for m in matches:
                m_title = m.get("title", "")
                m_date = m.get("date", "")
                m_slug = make_slug(m_title, m_date)

                if is_checkpoint_match(m_slug, m_title, m_date, chk_slug, chk_title, chk_date):
                    checkpoint_matched = True
                    matched_title = m_title
                    matched_slug = m_slug
                    break

                all_new_matches.append((m_slug, m))

            if checkpoint_matched:
                print(f"\n[+] MATCHED LAST CHECKPOINT: '{matched_title}' ({matched_slug})!")
                print("[+] Stopping API scan immediately.")
                break

            current_page += 1
        except Exception as err:
            print(f"[!] Error processing API page {current_page}: {err}")
            break

    total_new = len(all_new_matches)
    if total_new > 0:
        success, newest_match = sync_and_sort_firebase(all_new_matches)
        if success and newest_match:
            save_last_checkpoint(newest_match)
            print(f"[+] Total {total_new} match successfully added in database")
            print("[+] Full database Sorted with new to old\n")
        else:
            print("[!] Failed to sync database.")
    else:
        print("[+] No new matches found. Database is up to date.\n")


if __name__ == "__main__":
    main()
