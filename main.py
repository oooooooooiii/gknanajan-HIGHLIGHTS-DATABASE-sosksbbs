from datetime import datetime
import json
import os
import re
import requests

FIREBASE_URL = os.environ.get("FIREBASE_URL", "")
FIREBASE_SECRET = os.environ.get("FIREBASE_SECRET", "")
API_BASE_URL = os.environ.get("API_BASE_URL")

CHECKPOINT_FILE = "last_match.json"


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


def make_slug(title, date_str):
    clean_title = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")
    formatted_date = ""
    if date_str:
        try:
            dt = datetime.strptime(date_str.strip(), "%b %d, %Y")
            formatted_date = dt.strftime("%Y-%m-%d")
        except Exception:
            formatted_date = re.sub(r"[^a-z0-9]+", "-", str(date_str).lower()).strip("-")
    if formatted_date:
        return f"{clean_title}-{formatted_date}"
    return clean_title


def get_last_checkpoint():
    if not os.path.exists(CHECKPOINT_FILE):
        return ""
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list) and len(data) > 0:
                return data[0].get("last_slug", "").strip()
    except Exception:
        pass
    return ""


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
            "date": date_str
        }
    ]
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(checkpoint_data, f, indent=2, ensure_ascii=False)


def fetch_new_matches(checkpoint_slug):
    all_fetched = []
    checkpoint_found = False
    current_page = 1
    max_page = 1

    while current_page <= max_page:
        if current_page == 1:
            url = f"{API_BASE_URL.rstrip('/')}/data.json"
        else:
            url = f"{API_BASE_URL.rstrip('/')}/data_page_{current_page - 1}.json"

        try:
            res = requests.get(url, timeout=30)
            if res.status_code != 200:
                break
            content = res.json()
            matches = content.get("matches", [])
            max_page = content.get("lastPage", max_page)

            for m in matches:
                slug = make_slug(m.get("title", ""), m.get("date", ""))
                if checkpoint_slug and slug == checkpoint_slug:
                    checkpoint_found = True
                    break
                all_fetched.append((slug, m))

            if checkpoint_found:
                break

            current_page += 1
        except Exception as err:
            print(f"Error fetching page {current_page}: {err}")
            break

    return all_fetched


def upload_to_firebase(new_matches_list):
    if not new_matches_list:
        print("No new matches to upload.")
        return False

    firebase_payload = {}
    for slug, m in new_matches_list:
        safe_m = sanitize_deep_json(m)
        firebase_payload[slug] = safe_m

    target_endpoint = f"{FIREBASE_URL.rstrip('/')}/Highlights/matches.json?auth={FIREBASE_SECRET}"
    items = list(firebase_payload.items())
    batch_size = 200
    total_batches = (len(items) + batch_size - 1) // batch_size

    success_count = 0
    for i in range(total_batches):
        batch_dict = dict(items[i * batch_size : (i + 1) * batch_size])
        try:
            res = requests.patch(
                target_endpoint,
                data=json.dumps(batch_dict, ensure_ascii=False),
                headers={"Content-Type": "application/json"},
                timeout=35
            )
            if res.status_code == 200:
                success_count += len(batch_dict)
                print(f"Batch {i+1}/{total_batches} uploaded successfully ({len(batch_dict)} matches)")
            else:
                print(f"Batch {i+1} failed with status: {res.status_code}")
        except Exception as e:
            print(f"Network error on batch {i+1}: {e}")

    return success_count > 0


def main():
    if not FIREBASE_URL or not FIREBASE_SECRET:
        print("Error: FIREBASE_URL or FIREBASE_SECRET environment variables are missing.")
        return

    last_slug = get_last_checkpoint()
    print(f"Last Checkpoint Slug: '{last_slug}'")

    new_matches = fetch_new_matches(last_slug)
    print(f"Total new matches found: {len(new_matches)}")

    if new_matches:
        uploaded = upload_to_firebase(new_matches)
        if uploaded:
            newest_match = new_matches[0][1]
            save_last_checkpoint(newest_match)
            print("Checkpoint updated successfully in last_match.json")
    else:
        print("Database is up to date.")


if __name__ == "__main__":
    main()
