import requests
import json
import time
import random
import string

# --- CONFIGURATION ---
GATEWAY_URL = "http://localhost:8010"
COOKIE = "__Host-inst_sid=2Ak7THEA19xL1vupkV-tl3RD86ZXhBaucAvt0Ja8w8s" 
CSRF_TOKEN = "SB5buLL5iNjliNDC8d3Yrxi0-WjPI_WcVeCSVXZN1gE" 

# --- CHANGED: Pointing to the new JSON file ---
JSON_FILE_PATH = "new_batch_students.json"

headers = {
    "Cookie": COOKIE,
    "Content-Type": "application/json",
    "Origin": "http://localhost:8081",
    "X-CSRF-Token": CSRF_TOKEN
}

def generate_email(name):
    clean_name = name.lower().replace(" ", ".")
    rand_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{clean_name}.{rand_str}@example.com"

def seed_students():
    with open(JSON_FILE_PATH, "r") as f:
        data = json.load(f)

    items = data.get("items", [])
    print(f"Loaded {len(items)} students from {JSON_FILE_PATH}")

    for item in items:
        branch_id = item["branch_id"]
        full_name = item["student_name"]
        
        # Split the name into first and last name
        name_parts = full_name.split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        # Construct the exact payload expected by the backend
        payload = {
            "academic": {
                "class_level": item["academic"]["class_level"],
                "academic_year": item["academic"]["academic_year"],
                "other_class_description": None
            },
            "batch_ids": [], # Hardcoded to empty list to bypass validation
            "batch_subjects": [], 
            "confirm_distinct_person": False,
            "new_student": {
                "first_name": first_name,
                "last_name": last_name,
                "email": generate_email(full_name),
                "phone": f"9{random.randint(100000000, 999999999)}"
            }
        }
        
        url = f"{GATEWAY_URL}/api/v1/branches/{branch_id}/enrollments"
        
        try:
            response = requests.post(url, json=payload, headers=headers)
            
            if response.status_code in [200, 201]:
                print(f"✅ Successfully enrolled: {full_name} ({item['student_id']})")
            else:
                print(f"❌ Failed to enroll {full_name}: {response.status_code} - {response.text}")
                
        except Exception as e:
            print(f"❌ Network Error for {full_name}: {e}")
            
        time.sleep(0.3)

    print("\nSeeding complete!")

if __name__ == "__main__":
    seed_students()